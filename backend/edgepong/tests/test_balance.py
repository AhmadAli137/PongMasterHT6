"""Balance-mode physics tests (spec §18)."""

import math

import numpy as np

from edgepong.game.balance import BalanceSim
from edgepong.mathutil import quat_from_axis_angle, quat_mul, quat_normalize, vec3
from edgepong.types import PaddlePose, TrackingState


def _face_up():
    """Orientation whose +Z face normal points up (+Y world): paddle held flat."""
    return quat_from_axis_angle(vec3(1.0, 0.0, 0.0), -math.pi / 2)


def _tilted(tilt_rad: float):
    """Face-up paddle tilted about world X so one edge dips."""
    tilt = quat_from_axis_angle(vec3(1.0, 0.0, 0.0), tilt_rad)
    return quat_normalize(quat_mul(tilt, _face_up()))


def test_flat_paddle_ball_stays_centered():
    sim = BalanceSim(0.095, 0.095)
    q = _face_up()
    for _ in range(240):  # 1 s
        sim.step(1.0 / 240.0, q)
    assert sim.on_paddle
    assert abs(sim.lx) < 1e-6
    assert abs(sim.ly) < 1e-6


def test_tilted_paddle_ball_rolls():
    sim = BalanceSim(0.095, 0.095)
    q = _tilted(0.15)  # ~8.6 degrees
    for _ in range(60):  # 0.25 s
        sim.step(1.0 / 240.0, q)
    # ball must have accelerated away from centre
    assert math.hypot(sim.lx, sim.ly) > 0.005


def test_ball_released_at_edge_then_respawns():
    sim = BalanceSim(0.05, 0.05)  # small paddle so it rolls off quickly
    q = _tilted(0.4)
    edge_events = 0
    for _ in range(240 * 3):  # 3 s: enough to roll off and respawn
        if sim.step(1.0 / 240.0, q) == "edge":
            edge_events += 1
    assert edge_events >= 1
    # after respawn cycle the sim keeps running (either back on or waiting)
    assert isinstance(sim.on_paddle, bool)


def test_edge_event_fires_exactly_once_per_falloff():
    sim = BalanceSim(0.03, 0.03)
    q = _tilted(0.5)
    events = []
    for _ in range(240):  # 1 s — one roll-off, respawn takes 1.2 s
        ev = sim.step(1.0 / 240.0, q)
        if ev:
            events.append(ev)
    assert events == ["edge"]


def test_world_position_sits_on_face():
    sim = BalanceSim(0.095, 0.095, radius=0.02)
    pose = PaddlePose(
        timestamp_us=0,
        position_m=vec3(0.0, 1.1, 1.8),
        orientation=_face_up(),
        confidence=1.0,
        tracking_state=TrackingState.GOOD,
    )
    wp = sim.world_position(pose)
    # face normal points up, so the ball rests radius above the paddle centre
    assert abs(float(wp[1]) - (1.1 + 0.02)) < 1e-6
    assert abs(float(wp[0])) < 1e-6
