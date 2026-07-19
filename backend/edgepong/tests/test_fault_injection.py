"""Fault-injection tests (spec §24.2).

Simulate tag outages, packet loss/reordering/duplication, and paddle
disconnects, and verify the pipeline degrades and recovers without crashing —
and that a ball can never produce more than one impact.
"""

import numpy as np

from edgepong.camera.apriltag_detector import TagObservation
from edgepong.clock import now_us
from edgepong.config import Config, FusionConfig, PaddleConfig
from edgepong.fusion.filter import PoseFusion
from edgepong.game.service import GameService, HealthProvider
from edgepong.game.state_machine import GameState
from edgepong.mathutil import quat_identity, vec3
from edgepong.paddle.packets import PaddleTelemetry
from edgepong.paddle.udp_gateway import PaddleGateway
from edgepong.types import Ball, BallLifecycle, BallType, PaddlePose, TrackingState


# --------------------------------------------------------------------------- #
# Tag outage: DEGRADED within the prediction window, LOST beyond it, recovers.
# --------------------------------------------------------------------------- #
def _obs(t_us: int, pos=(0.0, 1.2, 1.8), conf: float = 0.9) -> TagObservation:
    return TagObservation(
        capture_time_us=t_us,
        processed_time_us=t_us,
        tag_ids=[0, 1],
        position_camera_m=np.array(pos, dtype=np.float64),
        orientation_camera_quat=quat_identity(),
        reprojection_error_px=0.5,
        decision_margin=60.0,
        confidence=conf,
    )


def _fusion() -> PoseFusion:
    return PoseFusion(FusionConfig(), PaddleConfig())


def test_tag_outage_degrades_then_loses_then_recovers():
    f = _fusion()
    t0 = 1_000_000
    # feed several good observations 16 ms apart -> GOOD
    for i in range(4):
        f.on_camera(_obs(t0 + i * 16_000))
    pose = f.step(now=t0 + 3 * 16_000 + 5_000)
    assert pose.tracking_state is TrackingState.GOOD

    last = t0 + 3 * 16_000
    # 110 ms without a tag: stale (>100 ms) but inside max_prediction (120 ms)
    pose = f.step(now=last + 110_000)
    assert pose.tracking_state is TrackingState.DEGRADED

    # 150 ms without a tag: beyond the prediction window
    pose = f.step(now=last + 150_000)
    assert pose.tracking_state is TrackingState.LOST

    # tag returns: a couple of observations rebuild confidence to GOOD
    t1 = last + 200_000
    for i in range(4):
        f.on_camera(_obs(t1 + i * 16_000))
    pose = f.step(now=t1 + 3 * 16_000 + 5_000)
    assert pose.tracking_state is TrackingState.GOOD


def test_position_prediction_stops_after_loss():
    f = _fusion()
    t0 = 1_000_000
    # moving paddle: build up linear velocity
    for i in range(5):
        f.on_camera(_obs(t0 + i * 16_000, pos=(0.05 * i, 1.2, 1.8)))
    lost_pose = f.step(now=t0 + 4 * 16_000 + 500_000)  # long after loss
    assert lost_pose.tracking_state is TrackingState.LOST
    # position must not have been extrapolated wildly (velocity was ~3 m/s)
    assert abs(float(lost_pose.position_m[0])) < 1.0


def test_outlier_jump_rejected():
    f = _fusion()
    t0 = 1_000_000
    for i in range(4):
        f.on_camera(_obs(t0 + i * 16_000))
    before = f.step(now=t0 + 60_000).position_m.copy()
    # implausible 2 m teleport in one frame
    f.on_camera(_obs(t0 + 5 * 16_000, pos=(2.0, 1.2, 1.8)))
    after = f.step(now=t0 + 100_000).position_m
    assert abs(float(after[0]) - float(before[0])) < 0.5


# --------------------------------------------------------------------------- #
# Packet loss / reordering / duplication at the UDP gateway.
# --------------------------------------------------------------------------- #
def _tel(seq: int) -> bytes:
    return PaddleTelemetry(
        sequence=seq, paddle_time_us=seq * 10_000,
        quat_w=1.0, quat_x=0.0, quat_y=0.0, quat_z=0.0,
        gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
        accel_x=0.0, accel_y=9.81, accel_z=0.0,
    ).encode()


def test_gateway_keeps_newest_and_counts_loss():
    gw = PaddleGateway(PaddleConfig())
    addr = ("127.0.0.1", 55555)
    gw._handle(_tel(1), addr)
    gw._handle(_tel(3), addr)   # seq 2 lost
    gw._handle(_tel(2), addr)   # late arrival: ignored (older)
    gw._handle(_tel(3), addr)   # duplicate: ignored
    latest = gw.latest()
    assert latest is not None and latest.sequence == 3
    assert gw.link.packets_lost == 1


def test_gateway_rejects_corrupted_packet():
    gw = PaddleGateway(PaddleConfig())
    data = bytearray(_tel(1))
    data[12] ^= 0xFF  # corrupt payload; CRC must catch it
    gw._handle(bytes(data), ("127.0.0.1", 55555))
    assert gw.latest() is None


def test_gateway_disconnect_detection():
    gw = PaddleGateway(PaddleConfig())
    gw._handle(_tel(1), ("127.0.0.1", 55555))
    assert gw.update_link_state().connected
    # backdate the last packet beyond disconnected_ms
    gw.link.last_packet_us = now_us() - 2_000_000
    assert not gw.update_link_state().connected


# --------------------------------------------------------------------------- #
# One impact per ball, ever — even across many physics steps.
# --------------------------------------------------------------------------- #
def test_single_impact_per_ball():
    cfg = Config()
    impacts: list = []
    svc = GameService(
        cfg=cfg,
        fusion=_fusion(),
        health=HealthProvider(lambda: True, lambda: True),
        on_impact=impacts.append,
    )
    svc._sm.state = GameState.PLAYING

    pose = PaddlePose(
        timestamp_us=0,
        position_m=vec3(0.0, 1.2, 1.8),
        orientation=quat_identity(),
        confidence=1.0,
        tracking_state=TrackingState.GOOD,
    )
    ball = Ball(
        id=1, spawn_time_us=now_us(),
        position=vec3(0.0, 1.2, 1.7), velocity=vec3(0.0, 0.0, 5.0),
        radius=0.02, type=BallType.NORMAL, target_zone=vec3(),
        state=BallLifecycle.APPROACHING, prev_position=vec3(0.0, 1.2, 1.7),
    )
    svc._balls[1] = ball

    for _ in range(60):  # 0.25 s of physics; ball crosses the paddle plane once
        svc._step_gameplay(1.0 / 240.0, pose)

    assert len(impacts) == 1
    assert impacts[0].ball_id == 1
