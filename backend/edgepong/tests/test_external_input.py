"""Mouse (external) control tests: takeover, mapping, strike, AI handoff."""

import math

import numpy as np

from edgepong.config import Config, GameConfig, PaddleConfig
from edgepong.fusion.filter import PoseFusion
from edgepong.game.service import GameService, HealthProvider
from edgepong.mathutil import quat_rotate, vec3
from edgepong.sim.paddle_model import SimPaddleModel


def _model() -> SimPaddleModel:
    return SimPaddleModel(GameConfig(), PaddleConfig())


def test_external_input_overrides_autoplay():
    m = _model()
    assert not m.external_active
    m.set_external_input(x=0.5, y=1.0)
    assert m.external_active
    for _ in range(240):  # 1 s
        m.update(1.0 / 240.0, balls=[])
    snap = m.snapshot()
    assert abs(float(snap.position[0]) - 0.5) < 0.02
    assert abs(float(snap.position[1]) - 1.0) < 0.02


def test_ai_resumes_after_input_timeout(monkeypatch):
    m = _model()
    m.set_external_input(x=0.5, y=1.0)
    assert m.external_active
    # age the input past the timeout instead of sleeping
    m._ext_last_us -= int((SimPaddleModel.EXTERNAL_TIMEOUT_S + 1.0) * 1e6)
    assert not m.external_active


def test_strike_lunges_forward_and_recovers():
    m = _model()
    plane_z = GameConfig().paddle_plane_z_m
    m.set_external_input(x=0.0, y=1.2)
    for _ in range(60):
        m.update(1.0 / 240.0, balls=[])
    m.set_external_input(strike=True)
    min_z = plane_z
    for _ in range(int(0.2 * 240)):  # cover the whole strike window
        m.update(1.0 / 240.0, balls=[])
        min_z = min(min_z, float(m.snapshot().position[2]))
    # lunged meaningfully toward the wall (z decreases)...
    assert min_z < plane_z - 0.05
    # ...and recovered to the plane afterwards
    for _ in range(120):
        m.update(1.0 / 240.0, balls=[])
    assert abs(float(m.snapshot().position[2]) - plane_z) < 0.05


def test_wheel_tilt_pitches_face_normal():
    m = _model()
    m.set_external_input(x=0.0, y=1.2)
    for _ in range(30):
        m.update(1.0 / 240.0, balls=[])
    normal_before = quat_rotate(m.snapshot().orientation, vec3(0, 0, 1))
    m.set_external_input(tilt_delta=0.4)
    for _ in range(30):
        m.update(1.0 / 240.0, balls=[])
    normal_after = quat_rotate(m.snapshot().orientation, vec3(0, 0, 1))
    # pitch changes the vertical component of the face normal
    assert abs(float(normal_after[1]) - float(normal_before[1])) > 0.1


def test_tilt_clamped():
    m = _model()
    m.set_external_input(tilt_delta=99.0)
    assert m._ext_tilt <= SimPaddleModel.MAX_TILT_RAD + 1e-9


def test_swing_speed_drives_strike_power():
    plane_z = GameConfig().paddle_plane_z_m

    def min_lunge_z(hand_speed: float) -> float:
        m = _model()
        m.set_external_input(x=0.0, y=1.2)
        for _ in range(60):
            m.update(1.0 / 240.0, balls=[])
        # report a swing velocity, then strike with NO explicit power:
        # the model must derive it from the hand speed
        m.set_external_input(vx=hand_speed, vy=0.0)
        m.set_external_input(strike=True)
        lowest = plane_z
        for _ in range(int(0.3 * 240)):
            m.update(1.0 / 240.0, balls=[])
            lowest = min(lowest, float(m.snapshot().position[2]))
        return lowest

    slow = min_lunge_z(0.0)   # stationary hand: minimum power
    fast = min_lunge_z(6.0)   # a real flick: full power
    assert fast < slow - 0.08
    assert slow < plane_z - 0.03  # even a still-handed tap strikes


def test_hand_velocity_becomes_paddle_velocity():
    m = _model()
    m.set_external_input(x=0.0, y=1.2)
    for _ in range(60):
        m.update(1.0 / 240.0, balls=[])
    m.set_external_input(vx=4.0, vy=1.5)
    m.update(1.0 / 240.0, balls=[])
    v = m.snapshot().linear_velocity
    # reported swing dominates the ground-truth velocity (impact physics feel it)
    assert float(v[0]) > 2.0
    assert float(v[1]) > 0.7


def test_stale_swing_velocity_decays():
    m = _model()
    m.set_external_input(x=0.0, y=1.2, vx=6.0, vy=0.0)
    # age the sample past the decay window
    m._ext_vel_us -= int(0.5 * 1e6)
    m.update(1.0 / 240.0, balls=[])
    assert abs(float(m.snapshot().linear_velocity[0])) < 1.0


def test_wrist_yaw_steers_face_normal():
    m = _model()
    m.set_external_input(x=0.0, y=1.2)
    for _ in range(30):
        m.update(1.0 / 240.0, balls=[])
    nx_before = float(quat_rotate(m.snapshot().orientation, vec3(0, 0, 1))[0])
    m.set_external_input(yaw_delta=-0.4)  # drag right maps to negative delta
    for _ in range(30):
        m.update(1.0 / 240.0, balls=[])
    nx_after = float(quat_rotate(m.snapshot().orientation, vec3(0, 0, 1))[0])
    assert abs(nx_after - nx_before) > 0.1  # normal swung laterally


def test_flip_toggles_backhand_and_face_normal():
    m = _model()
    m.set_external_input(x=0.0, y=1.2)
    for _ in range(30):
        m.update(1.0 / 240.0, balls=[])
    normal_fh = quat_rotate(m.snapshot().orientation, vec3(0, 0, 1))

    m.set_external_input(flip=True)
    assert m.backhand
    for _ in range(30):
        m.update(1.0 / 240.0, balls=[])
    normal_bh = quat_rotate(m.snapshot().orientation, vec3(0, 0, 1))
    # the blade flipped 180° about the handle: the face normal reverses in z
    assert float(normal_fh[2]) * float(normal_bh[2]) < 0

    m.set_external_input(flip=True)
    assert not m.backhand  # toggles back


def test_balance_mode_mouse_steers_tray():
    m = _model()
    m.balance_mode = True
    m.set_external_input(x=0.9, y=1.2)  # mouse far right
    for _ in range(60):
        m.update(1.0 / 240.0, balls=[])
    # face normal should lean so the +x edge dips: normal tips toward +x
    normal = quat_rotate(m.snapshot().orientation, vec3(0, 0, 1))
    assert float(normal[0]) > 0.05


def test_service_maps_screen_to_world_and_ignores_garbage():
    cfg = Config()
    model = _model()
    svc = GameService(
        cfg=cfg,
        fusion=PoseFusion(cfg.fusion, cfg.paddle),
        health=HealthProvider(lambda: True, lambda: True),
        on_impact=lambda i: None,
        sim_model=model,
    )
    svc.external_input(x=1.0, y=1.0)  # top-right corner of screen
    assert abs(model._ext_target[0] - 0.75) < 1e-9
    assert abs(model._ext_target[1] - 1.70) < 1e-9

    # garbage from the sanitizer path must not blow past model clamps
    svc.external_input(x=-4.0, y=-4.0)
    assert model._ext_target[0] >= -0.9
    assert model._ext_target[1] >= 0.35


def test_backhand_stance_drives_scoring():
    from edgepong.game.state_machine import GameState
    from edgepong.mathutil import quat_identity
    from edgepong.types import Ball, BallLifecycle, BallType, PaddlePose, TrackingState
    from edgepong.clock import now_us

    cfg = Config()
    model = _model()
    svc = GameService(
        cfg=cfg,
        fusion=PoseFusion(cfg.fusion, cfg.paddle),
        health=HealthProvider(lambda: True, lambda: True),
        on_impact=lambda i: None,
        sim_model=model,
    )
    svc._sm.state = GameState.PLAYING
    model.set_external_input(x=0.0, y=0.0, flip=True)  # backhand, external active

    pose = PaddlePose(
        timestamp_us=0, position_m=vec3(0.0, 1.2, 1.8),
        orientation=quat_identity(), confidence=1.0,
        tracking_state=TrackingState.GOOD,
    )
    ball = Ball(
        id=1, spawn_time_us=now_us(),
        position=vec3(0.08, 1.2, 1.7), velocity=vec3(0.0, 0.0, 5.0),  # right side
        radius=0.04, type=BallType.NORMAL, target_zone=vec3(),
        state=BallLifecycle.APPROACHING, prev_position=vec3(0.08, 1.2, 1.7),
    )
    svc._balls[1] = ball
    for _ in range(25):
        svc._step_gameplay(1.0 / 240.0, pose)
    # contact was on the right (heuristic would say forehand) but the declared
    # stance wins because the mouse is driving
    assert svc._scorer.stats.backhand_hits == 1
    assert svc._scorer.stats.forehand_hits == 0


def test_no_sim_model_is_noop():
    cfg = Config()
    svc = GameService(
        cfg=cfg,
        fusion=PoseFusion(cfg.fusion, cfg.paddle),
        health=HealthProvider(lambda: True, lambda: True),
        on_impact=lambda i: None,
        sim_model=None,
    )
    svc.external_input(x=0.5, y=0.5)  # hardware mode: must not raise
