"""Serve generator (spec §15.4, upgraded to real table-tennis serves).

Each serve is constructed backward from where it should land, so it is
guaranteed legal by construction:

1. pick the second bounce T2 on the PLAYER half and a post-bounce flight time
2. pick the first bounce B1 on the OPPONENT half; solve the ballistic segment
   B1→T2 and verify it clears the net (retry with a higher arc if not)
3. invert the tabletop bounce (restitution/friction) to get the pre-bounce
   arrival velocity at B1, then solve the serve stroke segment S→B1 backward —
   S lands naturally behind the table end, right where the opponent stands

Difficulty controls cadence, flight time (speed), placement spread and type
mix. The first seconds of a round force easy centred serves (demo mode §15.3).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

from ..config import GameConfig
from ..mathutil import vec3
from ..types import Ball, BallLifecycle, BallType
from .physics import GRAVITY, TABLE_FRICTION, aim_shot, predict_plane_crossing

BALL_RADIUS = 0.04


@dataclass
class ReachEnvelope:
    center_x: float = 0.0
    center_y: float = 1.05  # rally balls arrive lower than the old arcade lobs
    half_x: float = 0.5
    half_y: float = 0.35


class Spawner:
    def __init__(self, cfg: GameConfig, seed: int = 7):
        self._cfg = cfg
        self._rng = random.Random(seed)
        self._next_id = 1
        self._elapsed_s = 0.0
        self._next_spawn_s = 1.0
        self._planned: Ball | None = None  # next serve, chosen ahead of time
        self.envelope = ReachEnvelope()

    def reset(self) -> None:
        self._elapsed_s = 0.0
        self._next_spawn_s = 1.0
        self._planned = None
        # keep incrementing ids across sessions to avoid stale collisions

    def delay_next(self, seconds: float) -> None:
        """Push the next serve out (breather after a point ends)."""
        self._next_spawn_s = max(self._next_spawn_s, self._elapsed_s + seconds)

    def take_id(self) -> int:
        """Allocate a ball id from the shared sequence (player-serve tosses)."""
        bid = self._next_id
        self._next_id += 1
        return bid

    def _difficulty_params(self) -> tuple[float, float, float]:
        """Return (interval_s, serve_pace 0..1, spread 0..1) ramping over time."""
        d = self._cfg.difficulty.upper()
        ramp = min(1.0, self._elapsed_s / max(1.0, self._cfg.round_duration_s))
        if d == "EASY":
            return 2.6 - 0.8 * ramp, 0.25 + 0.25 * ramp, 0.15 + 0.35 * ramp
        if d == "HARD":
            return 1.8 - 0.6 * ramp, 0.65 + 0.35 * ramp, 0.55 + 0.4 * ramp
        return 2.2 - 0.7 * ramp, 0.45 + 0.3 * ramp, 0.35 + 0.4 * ramp  # NORMAL

    def update(self, dt: float, active_ball_count: int) -> list[Ball]:
        self._elapsed_s += dt
        interval, pace, spread = self._difficulty_params()
        spawned: list[Ball] = []
        # plan the next serve ahead of time so the opponent can telegraph it
        if self._planned is None:
            self._planned = self._make_serve(pace, spread)
        # real rally: exactly one live ball at a time
        if self._elapsed_s >= self._next_spawn_s and active_ball_count == 0:
            spawned.append(self._planned)
            self._next_spawn_s = self._elapsed_s + interval
            # replan immediately so the opponent never has a telegraph gap
            self._planned = self._make_serve(pace, spread)
        return spawned

    # ------------------------------------------------------------------ #
    def _make_serve(self, pace: float, spread: float) -> Ball:
        cfg = self._cfg
        env = self.envelope
        rng = self._rng
        h = cfg.table_height_m + BALL_RADIUS
        forced_easy = self._elapsed_s < 6.0

        # 1) second bounce on the player half
        if forced_easy:
            t2x = 0.0
        else:
            t2x = rng.uniform(-spread, spread) * min(env.half_x, cfg.table_width_m * 0.42)
        t2z = rng.uniform(1.28, 1.55)
        target2 = vec3(t2x, h, t2z)

        # 2) first bounce on the opponent half + the segment B1 -> T2
        b1 = vec3(t2x * 0.35 + rng.uniform(-0.06, 0.06), h, rng.uniform(0.42, 0.62))
        t2_flight = 0.72 - 0.30 * pace  # pace 0 -> 0.72 s (floaty), 1 -> 0.42 s (zippy)
        vp = None
        for _ in range(5):
            vp = aim_shot(b1, target2, t2_flight, cfg, min_net_clearance=0.05)
            if vp is not None and vp[1] > 0.2:
                break
            t2_flight += 0.07  # higher arc until the net clears
        assert vp is not None  # generous flight times always converge

        # 3) invert the bounce to get the serve stroke S -> B1
        pre = vp.copy()
        pre[1] = -vp[1] / cfg.table_restitution
        pre[0] = vp[0] / TABLE_FRICTION
        pre[2] = vp[2] / TABLE_FRICTION

        # serve origin sits behind the table end, like a real server
        s_z = rng.uniform(-0.18, -0.02)
        t1 = float((b1[2] - s_z) / max(pre[2], 0.5))
        t1 = float(np.clip(t1, 0.22, 0.6))
        g = float(GRAVITY[1])
        sx = float(b1[0] - pre[0] * t1)
        sy = float(b1[1] - pre[1] * t1 + 0.5 * g * t1 * t1)
        sy = float(np.clip(sy, 0.85, 1.5))
        start = vec3(sx, sy, float(b1[2] - pre[2] * t1))
        v0 = vec3(float(pre[0]), float(pre[1]) - g * t1, float(pre[2]))

        # where this serve becomes hittable (feeds the autoplay AI's aim)
        hittable = predict_plane_crossing(start, v0, BALL_RADIUS, cfg, cfg.paddle_plane_z_m)

        ball = Ball(
            id=self._next_id,
            spawn_time_us=0,  # stamped by caller with monotonic clock
            position=start,
            velocity=v0,
            radius=BALL_RADIUS,
            type=self._pick_type(forced_easy),
            target_zone=hittable,
            state=BallLifecycle.APPROACHING,
            prev_position=start.copy(),
            last_hit_by="OPPONENT",
        )
        self._next_id += 1
        return ball

    def serve_preview(self) -> dict | None:
        """Where and when the next ball will be served (for the opponent view)."""
        if self._planned is None:
            return None
        p = self._planned.position
        return {
            "position": [float(p[0]), float(p[1]), float(p[2])],
            "inMs": max(0.0, round((self._next_spawn_s - self._elapsed_s) * 1000.0, 1)),
        }

    def _pick_type(self, forced_easy: bool) -> BallType:
        # AVOID balls don't fit a real rally; SMASH/BACKHAND still spice serves
        if forced_easy:
            return BallType.NORMAL
        r = self._rng.random()
        if r < 0.15:
            return BallType.SMASH
        if r < 0.30:
            return BallType.BACKHAND
        return BallType.NORMAL
