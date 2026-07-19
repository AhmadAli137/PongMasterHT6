"""Rally physics + flow tests: legal serves, net, landing outcomes, returns."""

import numpy as np

from edgepong.config import Config, GameConfig
from edgepong.fusion.filter import PoseFusion
from edgepong.game.physics import (
    check_net_block,
    integrate_ball,
    net_top,
    net_z,
    step_table_bounce,
)
from edgepong.game.service import GameService, HealthProvider
from edgepong.game.spawner import BALL_RADIUS, Spawner
from edgepong.game.state_machine import GameState
from edgepong.mathutil import quat_identity, vec3
from edgepong.types import Ball, BallLifecycle, BallType, PaddlePose, TrackingState

DT = 1.0 / 240.0


def _ball(pos, vel, state=BallLifecycle.APPROACHING) -> Ball:
    return Ball(
        id=1, spawn_time_us=0,
        position=vec3(*pos), velocity=vec3(*vel),
        radius=BALL_RADIUS, type=BallType.NORMAL, target_zone=vec3(),
        state=state, prev_position=vec3(*pos),
    )


# --------------------------------------------------------------------------- #
# Serve legality: every generated serve double-bounces and never nets.
# --------------------------------------------------------------------------- #
def test_serves_bounce_their_side_then_yours_and_clear_net():
    cfg = GameConfig()
    sp = Spawner(cfg, seed=3)
    sp._elapsed_s = 10.0  # past the forced-easy window: full randomness
    for pace in (0.2, 0.5, 0.9):
        for _ in range(10):
            ball = sp._make_serve(pace, spread=0.8)
            sides = []
            for _ in range(int(3.0 / DT)):
                integrate_ball(ball, DT)
                assert not check_net_block(ball, cfg), "serve clipped the net"
                side = step_table_bounce(ball, cfg)
                if side:
                    sides.append(side)
                if ball.position[2] >= cfg.paddle_plane_z_m:
                    break
            assert sides[:2] == ["opponent", "player"], f"bad serve bounces: {sides}"
            # arrives at a hittable height
            assert 0.78 <= float(ball.position[1]) <= 1.7
            # the precomputed hittable point matches reality (autoplay aim)
            assert abs(float(ball.position[0]) - float(ball.target_zone[0])) < 0.05
            assert abs(float(ball.position[1]) - float(ball.target_zone[1])) < 0.08


# --------------------------------------------------------------------------- #
# Table + net unit behaviour.
# --------------------------------------------------------------------------- #
def test_table_bounce_reflects_with_restitution():
    cfg = GameConfig()
    b = _ball((0.0, 1.2, 0.5), (0.0, -3.0, 0.0))
    bounced = None
    for _ in range(240):
        integrate_ball(b, DT)
        bounced = step_table_bounce(b, cfg)
        if bounced:
            break
    assert bounced == "opponent"
    assert b.velocity[1] > 0
    assert b.velocity[1] <= 3.5 * cfg.table_restitution + 0.5


def test_ball_beside_table_does_not_bounce():
    cfg = GameConfig()
    b = _ball((1.2, 1.0, 0.5), (0.0, -3.0, 0.0))  # outside table width
    for _ in range(240):
        integrate_ball(b, DT)
        assert step_table_bounce(b, cfg) is None


def test_net_blocks_low_ball_and_clears_high_ball():
    cfg = GameConfig()
    low = _ball((0.0, 0.85, 1.1), (0.0, 0.0, -3.0), BallLifecycle.RETURNED)
    blocked = False
    for _ in range(120):
        integrate_ball(low, DT)
        if check_net_block(low, cfg):
            blocked = True
            break
    assert blocked
    assert low.position[2] > net_z(cfg)  # stayed on its incoming side

    high = _ball((0.0, 1.4, 1.1), (0.0, 1.0, -3.0), BallLifecycle.RETURNED)
    for _ in range(60):
        integrate_ball(high, DT)
        assert not check_net_block(high, cfg)
        if high.position[2] < net_z(cfg) - 0.1:
            break
    assert high.position[2] < net_z(cfg)
    assert high.position[1] > net_top(cfg)


# --------------------------------------------------------------------------- #
# Rally flow at the service level.
# --------------------------------------------------------------------------- #
class _FixedRng:
    """random()/uniform() stub so opponent decisions are deterministic."""
    def __init__(self, r: float):
        self._r = r
    def random(self) -> float:
        return self._r
    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2.0


def _service(events: list, rng_value: float) -> GameService:
    cfg = Config()
    svc = GameService(
        cfg=cfg,
        fusion=PoseFusion(cfg.fusion, cfg.paddle),
        health=HealthProvider(lambda: True, lambda: True),
        on_impact=lambda i: None,
        on_rally=events.append,
    )
    svc._sm.state = GameState.PLAYING
    svc._rng = _FixedRng(rng_value)
    return svc


def _pose() -> PaddlePose:
    return PaddlePose(
        timestamp_us=0, position_m=vec3(0.0, 1.2, 1.8),
        orientation=quat_identity(), confidence=1.0,
        tracking_state=TrackingState.GOOD,
    )


def test_return_lands_in_then_winner_when_opponent_declines():
    events: list = []
    svc = _service(events, rng_value=0.99)  # opponent never returns
    ball = _ball((0.0, 1.0, 0.8), (0.0, -1.0, -2.0), BallLifecycle.RETURNED)
    svc._balls[1] = ball
    for _ in range(700):  # ~3 s: land, then fly out past the opponent
        svc._step_gameplay(DT, _pose())
        if not svc._balls:
            break
    outcomes = [e["outcome"] for e in events]
    assert outcomes[0] == "IN"
    assert "WINNER" in outcomes
    assert svc._scorer.stats.score > 0


def test_opponent_returns_the_ball_when_willing():
    events: list = []
    svc = _service(events, rng_value=0.0)  # opponent always returns
    ball = _ball((0.0, 1.0, 0.8), (0.0, -1.0, -2.0), BallLifecycle.RETURNED)
    svc._balls[1] = ball
    returned = False
    for _ in range(700):
        svc._step_gameplay(DT, _pose())
        b = svc._balls.get(1)
        if b is not None and b.state is BallLifecycle.APPROACHING and b.velocity[2] > 0:
            returned = True
            break
    assert returned, "opponent never played the ball back"
    assert [e["outcome"] for e in events][0] == "IN"
    assert svc._opponent_swings >= 1


def test_return_into_net_is_a_fault():
    events: list = []
    svc = _service(events, rng_value=0.99)
    svc._scorer.stats.combo = 4
    ball = _ball((0.0, 0.82, 1.05), (0.0, 0.0, -3.0), BallLifecycle.RETURNED)
    svc._balls[1] = ball
    for _ in range(240):
        svc._step_gameplay(DT, _pose())
        if events:
            break
    assert events and events[0]["outcome"] == "NET"
    assert svc._scorer.stats.combo == 0


def test_return_bouncing_own_side_is_a_fault():
    events: list = []
    svc = _service(events, rng_value=0.99)
    # dropping steeply on the player's own half while heading to the opponent
    ball = _ball((0.0, 0.9, 1.5), (0.0, -2.5, -0.4), BallLifecycle.RETURNED)
    svc._balls[1] = ball
    for _ in range(240):
        svc._step_gameplay(DT, _pose())
        if events:
            break
    assert events and events[0]["outcome"] == "OWN_SIDE"
