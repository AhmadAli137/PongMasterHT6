"""Swept ball-vs-paddle collision (spec §16.3).

The paddle is an oriented rectangle. Each physics step we sweep the ball segment
from its previous to current position, intersect it with the (enlarged) paddle
plane, project the hit into paddle-local coordinates, and accept it inside the
forgiving collision bounds + grace window. Fast balls therefore cannot tunnel.

Returns a :class:`ContactResult` with local normalized coordinates in [-1, 1]
which feed both scoring and the haptic quadrant map.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..mathutil import Vec3, clamp, quat_rotate, vec3
from ..types import Ball, PaddlePose


@dataclass
class PaddleCollider:
    center: Vec3
    normal: Vec3     # unit face normal (world)
    right: Vec3      # unit local +x (world)
    up: Vec3         # unit local +y (world)
    half_w: float
    half_h: float
    velocity: Vec3
    angular_velocity: Vec3


@dataclass
class ContactResult:
    hit: bool
    point: Vec3
    local_x: float          # normalized -1..1 (right)
    local_y: float          # normalized -1..1 (up)
    toi: float              # time-of-impact fraction within the step [0,1]
    contact_velocity: Vec3  # paddle surface velocity at contact point


def build_collider(pose: PaddlePose, half_w: float, half_h: float, scale: float) -> PaddleCollider:
    normal = quat_rotate(pose.orientation, vec3(0.0, 0.0, 1.0))
    right = quat_rotate(pose.orientation, vec3(1.0, 0.0, 0.0))
    up = quat_rotate(pose.orientation, vec3(0.0, 1.0, 0.0))
    return PaddleCollider(
        center=np.asarray(pose.position_m, dtype=np.float64),
        normal=_norm(normal),
        right=_norm(right),
        up=_norm(up),
        half_w=half_w * scale,
        half_h=half_h * scale,
        velocity=np.asarray(pose.linear_velocity_mps, dtype=np.float64),
        angular_velocity=np.asarray(pose.angular_velocity_rad_s, dtype=np.float64),
    )


def sweep_ball(
    prev_pos: Vec3,
    curr_pos: Vec3,
    radius: float,
    paddle: PaddleCollider,
) -> ContactResult:
    """Sweep a ball segment against the (thick) oriented paddle plane."""
    p0 = np.asarray(prev_pos, dtype=np.float64)
    p1 = np.asarray(curr_pos, dtype=np.float64)
    seg = p1 - p0

    n = paddle.normal
    # signed distances of segment endpoints from the paddle plane, offset by
    # the ball radius so we detect surface contact, not centre contact.
    d0 = float(np.dot(p0 - paddle.center, n))
    d1 = float(np.dot(p1 - paddle.center, n))

    denom = d0 - d1
    # If the ball did not cross the plane within one radius, no contact.
    if abs(denom) < 1e-9:
        # moving parallel to plane; check static proximity
        if abs(d0) > radius:
            return _miss()
        toi = 0.0
    else:
        # solve for crossing of the +/- radius shell nearest to travel
        toi = (d0 - radius) / denom
        if toi < 0.0 or toi > 1.0:
            toi_neg = (d0 + radius) / denom
            if 0.0 <= toi_neg <= 1.0:
                toi = toi_neg
            else:
                return _miss()

    contact = p0 + seg * clamp(toi, 0.0, 1.0)
    rel = contact - paddle.center
    lx = float(np.dot(rel, paddle.right))
    ly = float(np.dot(rel, paddle.up))

    if abs(lx) > paddle.half_w + radius or abs(ly) > paddle.half_h + radius:
        return _miss()

    # surface velocity at contact = linear + angular x r
    r = paddle.right * lx + paddle.up * ly
    contact_vel = paddle.velocity + np.cross(paddle.angular_velocity, r)

    return ContactResult(
        hit=True,
        point=contact,
        local_x=clamp(lx / paddle.half_w, -1.0, 1.0),
        local_y=clamp(ly / paddle.half_h, -1.0, 1.0),
        toi=clamp(toi, 0.0, 1.0),
        contact_velocity=contact_vel,
    )


def reflect_velocity(
    ball_velocity: Vec3,
    contact_velocity: Vec3,
    normal: Vec3,
    restitution: float = 0.92,
    paddle_transfer: float = 0.6,
    max_speed: float = 22.0,
) -> Vec3:
    """Arcade rebound: reflect about normal + add paddle contact velocity."""
    v = np.asarray(ball_velocity, dtype=np.float64)
    n = _norm(np.asarray(normal, dtype=np.float64))
    v_ref = v - (1.0 + restitution) * np.dot(v, n) * n
    out = v_ref + paddle_transfer * np.asarray(contact_velocity, dtype=np.float64)
    speed = float(np.linalg.norm(out))
    if speed > max_speed:
        out = out / speed * max_speed
    return out


def _miss() -> ContactResult:
    return ContactResult(False, vec3(), 0.0, 0.0, 0.0, vec3())


def _norm(v: Vec3) -> Vec3:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v
