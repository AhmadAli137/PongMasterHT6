"""Ball integration + table/net physics for the fixed-step loop (spec §16.1).

Pure functions operating on :class:`Ball` state. The authoritative fixed-step
loop lives in :mod:`edgepong.game.service`. Rally realism:

- ballistic flight under gentle-but-believable gravity
- tabletop bounces with restitution + horizontal friction, tracked per half
- the net blocks anything that crosses its plane below the tape
- closed-form ballistic aiming (used for serves and opponent returns)
- numeric trajectory prediction so the autoplay AI knows where a bouncing
  ball will actually become hittable
"""

from __future__ import annotations

import math

import numpy as np

from ..config import GameConfig
from ..mathutil import Vec3, clamp, vec3
from ..types import Ball, HitQuality

GRAVITY = np.array([0.0, -6.0, 0.0])  # softened gravity: playable arcs, real feel
TABLE_FRICTION = 0.96                  # horizontal speed kept per tabletop bounce


def net_z(cfg: GameConfig) -> float:
    return cfg.table_z0_m + cfg.table_length_m * 0.5


def net_top(cfg: GameConfig) -> float:
    return cfg.table_height_m + cfg.net_height_m


def integrate_ball(ball: Ball, dt: float) -> None:
    """Semi-implicit Euler step; stores prev position for sweeps/crossings."""
    ball.prev_position = ball.position.copy()
    ball.velocity = ball.velocity + GRAVITY * dt
    ball.position = ball.position + ball.velocity * dt


def step_table_bounce(ball: Ball, cfg: GameConfig) -> str | None:
    """Bounce off the tabletop. Returns which half it hit ('opponent'/'player').

    Uses the prev→curr segment so fast balls cannot tunnel through the top.
    """
    if ball.velocity[1] >= 0.0:
        return None
    h = cfg.table_height_m + ball.radius
    if ball.position[1] >= h:
        return None
    prev = ball.prev_position if ball.prev_position is not None else ball.position
    if prev[1] < h:
        return None  # was already below the top surface (falling beside the table)

    denom = float(prev[1] - ball.position[1])
    t = float(prev[1] - h) / denom if denom > 1e-9 else 0.0
    cx = float(prev[0] + (ball.position[0] - prev[0]) * t)
    cz = float(prev[2] + (ball.position[2] - prev[2]) * t)

    if abs(cx) > cfg.table_width_m * 0.5:
        return None
    if not (cfg.table_z0_m <= cz <= cfg.table_z0_m + cfg.table_length_m):
        return None

    ball.position[0] = cx
    ball.position[1] = h
    ball.position[2] = cz
    ball.velocity[1] = -ball.velocity[1] * cfg.table_restitution
    ball.velocity[0] *= TABLE_FRICTION
    ball.velocity[2] *= TABLE_FRICTION

    if cz < net_z(cfg):
        ball.bounces_opponent += 1
        return "opponent"
    ball.bounces_player += 1
    return "player"


def check_net_block(ball: Ball, cfg: GameConfig) -> bool:
    """True if the ball just crossed the net plane below the tape (blocked).

    On a block the ball is dropped on its incoming side with most momentum killed.
    """
    nz = net_z(cfg)
    prev = ball.prev_position if ball.prev_position is not None else ball.position
    if (float(prev[2]) - nz) * (float(ball.position[2]) - nz) > 0.0:
        return False  # did not cross the net plane this step
    denom = float(ball.position[2] - prev[2])
    if abs(denom) < 1e-9:
        return False
    t = (nz - float(prev[2])) / denom
    cy = float(prev[1] + (ball.position[1] - prev[1]) * t)
    cx = float(prev[0] + (ball.position[0] - prev[0]) * t)
    if cy > net_top(cfg) + ball.radius * 0.5:
        return False  # cleared the tape
    if abs(cx) > cfg.table_width_m * 0.5 + 0.05:
        return False  # went around the net posts (legal, rare, fun)

    # blocked: drop the ball just on the incoming side
    ball.position[0] = cx
    ball.position[1] = min(cy, net_top(cfg))
    ball.position[2] = nz - math.copysign(0.03, denom)
    ball.velocity[2] = -ball.velocity[2] * 0.12
    ball.velocity[0] *= 0.4
    ball.velocity[1] = min(ball.velocity[1], 0.0)
    return True


def aim_shot(
    p_from: Vec3,
    target: Vec3,
    t_flight: float,
    cfg: GameConfig,
    min_net_clearance: float = 0.05,
) -> Vec3 | None:
    """Ballistic velocity taking p_from → target in t_flight seconds.

    Returns None if that parabola would clip the net (caller retries with a
    longer flight time = higher arc).
    """
    p = np.asarray(p_from, dtype=np.float64)
    tgt = np.asarray(target, dtype=np.float64)
    v = (tgt - p - 0.5 * GRAVITY * t_flight * t_flight) / t_flight

    nz = net_z(cfg)
    if (float(p[2]) - nz) * (float(tgt[2]) - nz) < 0.0 and abs(float(v[2])) > 1e-9:
        tn = (nz - float(p[2])) / float(v[2])
        if 0.0 < tn < t_flight:
            y_at_net = float(p[1] + v[1] * tn + 0.5 * GRAVITY[1] * tn * tn)
            if y_at_net < net_top(cfg) + min_net_clearance:
                return None
    return v


def predict_plane_crossing(
    pos: Vec3,
    vel: Vec3,
    radius: float,
    cfg: GameConfig,
    plane_z: float,
    max_s: float = 3.0,
) -> Vec3:
    """March a ball (gravity + table bounces) until it crosses plane_z.

    Cheap fine-step forward simulation used to aim the autoplay AI and to
    pre-compute each serve's hittable point. Returns the crossing position
    (or the last position if it never crosses).
    """
    p = np.asarray(pos, dtype=np.float64).copy()
    v = np.asarray(vel, dtype=np.float64).copy()
    dt = 0.004
    h = cfg.table_height_m + radius
    half_w = cfg.table_width_m * 0.5
    z0, z1 = cfg.table_z0_m, cfg.table_z0_m + cfg.table_length_m
    for _ in range(int(max_s / dt)):
        v = v + GRAVITY * dt
        prev_y = float(p[1])
        p = p + v * dt
        if v[1] < 0.0 and p[1] < h <= prev_y and abs(p[0]) <= half_w and z0 <= p[2] <= z1:
            p[1] = h
            v[1] = -v[1] * cfg.table_restitution
            v[0] *= TABLE_FRICTION
            v[2] *= TABLE_FRICTION
        if float(p[2]) >= plane_z:
            return vec3(float(p[0]), float(p[1]), float(p[2]))
        if float(p[1]) < 0.0:
            break
    return vec3(float(p[0]), float(p[1]), float(p[2]))


def is_out_of_play(ball: Ball, cfg: GameConfig) -> bool:
    """Ball has left the playable volume (past either end, floor, or wide)."""
    if ball.position[2] > cfg.player_z_m + 0.6:
        return True
    if ball.position[2] < -0.9:
        return True
    if ball.position[1] - ball.radius <= 0.01:  # floor
        return True
    if ball.position[1] > 3.5 or abs(ball.position[0]) > 2.2:
        return True
    return False


def classify_quality(ball: Ball, cfg: GameConfig, plane_dist_m: float) -> HitQuality:
    """PERFECT near the ideal paddle plane, GOOD within reach, LATE at the edge."""
    d = abs(plane_dist_m)
    if d < 0.05:
        return HitQuality.PERFECT
    if d < 0.15:
        return HitQuality.GOOD
    return HitQuality.LATE


def swing_speed(ball_rel_speed: float, cfg: GameConfig) -> float:
    return clamp((ball_rel_speed - 1.0) / 12.0, 0.0, 1.0)
