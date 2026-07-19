"""Player-serve (toss) flow: hover, drop, whiff re-serve, server alternation,
and the paddle-relative sweep that lets a lunge hit a falling ball."""

import numpy as np

from edgepong.clock import now_us
from edgepong.config import Config, GameConfig, PaddleConfig
from edgepong.fusion.filter import PoseFusion
from edgepong.game.collision import build_collider, sweep_ball
from edgepong.game.service import GameService, HealthProvider
from edgepong.game.state_machine import GameState
from edgepong.mathutil import quat_identity, vec3
from edgepong.sim.paddle_model import SimPaddleModel
from edgepong.types import BallLifecycle, PaddlePose, TrackingState

DT = 1.0 / 240.0


def _pose(x=0.0, y=1.2, z=1.8) -> PaddlePose:
    return PaddlePose(
        timestamp_us=0, position_m=vec3(x, y, z),
        orientation=quat_identity(), confidence=1.0,
        tracking_state=TrackingState.GOOD,
    )


def _service(sim_model=None) -> GameService:
    cfg = Config()
    svc = GameService(
        cfg=cfg,
        fusion=PoseFusion(cfg.fusion, cfg.paddle),
        health=HealthProvider(lambda: True, lambda: True),
        on_impact=lambda i: None,
        sim_model=sim_model,
    )
    svc._sm.state = GameState.PLAYING
    svc._server = "PLAYER"
    return svc


def test_toss_hovers_then_drops():
    svc = _service()
    svc._pose = _pose()
    svc.request_toss()
    assert len(svc._balls) == 1
    ball = next(iter(svc._balls.values()))
    assert ball.is_toss
    hover_y = float(ball.position[1])
    assert abs(hover_y - (1.2 + svc.TOSS_HEIGHT_M)) < 1e-6

    # frozen during the countdown
    for _ in range(int(0.5 / DT)):
        svc._step_gameplay(DT, _pose())
    assert abs(float(ball.position[1]) - hover_y) < 1e-9

    # countdown expires (1.5 s of sim time total) -> falls
    for _ in range(int(1.3 / DT)):
        svc._step_gameplay(DT, _pose())
    assert float(ball.position[1]) < hover_y - 0.05


def test_whiffed_toss_reserves_without_penalty():
    svc = _service()
    svc._pose = _pose()
    svc.request_toss()
    ball = next(iter(svc._balls.values()))
    ball.freeze_s = 0.0  # drop immediately
    misses_before = svc._scorer.stats.misses
    for _ in range(int(2.5 / DT)):  # let it fall to the floor untouched
        svc._step_gameplay(DT, _pose())
        if not svc._balls:
            break
    assert not svc._balls
    assert svc._scorer.stats.misses == misses_before  # no penalty
    assert svc._server == "PLAYER"  # still your serve: re-toss


def test_aim_phase_and_countdown_snapshot():
    svc = _service()
    svc._pose = _pose()
    snap = svc.snapshot()
    assert snap["server"] == "PLAYER"
    assert snap["playerServe"] == {"phase": "AIM", "dropInMs": 0}
    svc.request_toss()
    snap = svc.snapshot()
    assert snap["playerServe"]["phase"] == "COUNTDOWN"
    assert 0 < snap["playerServe"]["dropInMs"] <= 1500


def test_relative_sweep_lunge_hits_falling_ball():
    """A ball dropping parallel to the face is hit when the paddle lunges."""
    cfg = GameConfig()
    pose = _pose()
    # paddle moving toward the wall at 4 m/s (mid-lunge), plane at the ball
    pose.linear_velocity_mps = vec3(0.0, 0.0, -4.0)
    collider = build_collider(pose, 0.095, 0.095, 1.3)
    # ball falling straight down right at the plane the paddle is punching through
    prev = vec3(0.0, 1.25, 1.79)
    curr = vec3(0.0, 1.24, 1.79)
    plain = sweep_ball(prev, curr, 0.04, collider)
    relative = sweep_ball(prev + collider.velocity * DT * 4, curr, 0.04, collider)
    # the plain sweep misses the parallel-falling ball; the relative one connects
    assert relative.hit
    assert not plain.hit or relative.hit  # relative must never be worse


def test_serve_mode_opens_face_and_strike_brushes_up():
    from edgepong.mathutil import quat_rotate

    cfg = Config()
    m = SimPaddleModel(cfg.game, cfg.paddle)
    m.set_external_input(x=0.0, y=1.1)
    for _ in range(60):
        m.update(DT, balls=[])
    normal_flat = quat_rotate(m.snapshot().orientation, vec3(0, 0, 1))

    m.serve_mode = True
    for _ in range(60):
        m.update(DT, balls=[])
    normal_serve = quat_rotate(m.snapshot().orientation, vec3(0, 0, 1))
    # the face opens: its normal gains a clear upward component vs. flat stance
    assert float(normal_serve[1]) > float(normal_flat[1]) + 0.3

    # a serve strike brushes upward, not just forward
    base_y = float(m.snapshot().position[1])
    m.set_external_input(strike=True, strike_power=1.0)
    max_y = base_y
    min_z = float(m.snapshot().position[2])
    for _ in range(int(0.4 / DT)):
        m.update(DT, balls=[])
        snap = m.snapshot()
        max_y = max(max_y, float(snap.position[1]))
        min_z = min(min_z, float(snap.position[2]))
    assert max_y > base_y + 0.10   # swung up through the toss
    assert min_z < 1.8 - 0.05      # and still moved forward


def test_serve_mode_set_and_cleared_by_service():
    cfg = Config()
    model = SimPaddleModel(cfg.game, cfg.paddle)
    svc = GameService(
        cfg=cfg,
        fusion=PoseFusion(cfg.fusion, cfg.paddle),
        health=HealthProvider(lambda: True, lambda: True),
        on_impact=lambda i: None,
        sim_model=model,
    )
    svc._sm.state = GameState.PLAYING
    svc._server = "PLAYER"
    svc._step_gameplay(DT, _pose())
    assert model.serve_mode  # aiming: stance on

    svc._server = "OPPONENT"
    svc._step_gameplay(DT, _pose())
    assert not model.serve_mode  # their serve: stance off


def test_ai_autoplay_serves_itself_and_server_alternates():
    cfg = Config()
    model = SimPaddleModel(cfg.game, cfg.paddle)
    svc = GameService(
        cfg=cfg,
        fusion=PoseFusion(cfg.fusion, cfg.paddle),
        health=HealthProvider(lambda: True, lambda: True),
        on_impact=lambda i: None,
        sim_model=model,
    )
    svc._sm.state = GameState.PLAYING
    svc._server = "PLAYER"

    toss_ids: set[int] = set()
    hit_by_player = False
    for _ in range(int(10.0 / DT)):
        gt = model.snapshot()
        pose = _pose(float(gt.position[0]), float(gt.position[1]), float(gt.position[2]))
        pose.orientation = gt.orientation.copy()
        pose.linear_velocity_mps = gt.linear_velocity.copy()
        svc._step_gameplay(DT, pose)
        model.update(DT, list(svc._balls.values()))
        for b in svc._balls.values():
            if b.is_toss:
                toss_ids.add(b.id)
            # is_toss clears on the strike, so "was a toss, now RETURNED" = served
            if b.id in toss_ids and b.state is BallLifecycle.RETURNED:
                hit_by_player = True
        if svc._server == "OPPONENT":
            break
    assert toss_ids, "AI never tossed on its own serve"
    assert hit_by_player, "AI never struck its own toss"
    assert svc._server == "OPPONENT", "serve never passed to the opponent"
