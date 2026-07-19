"""Small vector / quaternion helpers used across the backend.

Quaternions are stored as (w, x, y, z) numpy arrays. All frames are
right-handed to match Three.js scene conventions (see docs and spec §14).
Kept dependency-light (numpy only) and unit tested.
"""

from __future__ import annotations

import math

import numpy as np

Vec3 = np.ndarray  # shape (3,)
Quat = np.ndarray  # shape (4,) as (w, x, y, z)


def vec3(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Vec3:
    return np.array([x, y, z], dtype=np.float64)


def quat(w: float = 1.0, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Quat:
    return np.array([w, x, y, z], dtype=np.float64)


def quat_identity() -> Quat:
    return quat(1.0, 0.0, 0.0, 0.0)


def quat_normalize(q: Quat) -> Quat:
    n = float(np.linalg.norm(q))
    if n < 1e-12:
        return quat_identity()
    return q / n


def quat_mul(a: Quat, b: Quat) -> Quat:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ],
        dtype=np.float64,
    )


def quat_conjugate(q: Quat) -> Quat:
    return np.array([q[0], -q[1], -q[2], -q[3]], dtype=np.float64)


def quat_dot(a: Quat, b: Quat) -> float:
    return float(np.dot(a, b))


def quat_rotate(q: Quat, v: Vec3) -> Vec3:
    """Rotate vector v by unit quaternion q."""
    qv = quat(0.0, v[0], v[1], v[2])
    r = quat_mul(quat_mul(q, qv), quat_conjugate(q))
    return r[1:4].copy()


def quat_from_axis_angle(axis: Vec3, angle_rad: float) -> Quat:
    n = float(np.linalg.norm(axis))
    if n < 1e-12:
        return quat_identity()
    axis = axis / n
    h = 0.5 * angle_rad
    s = math.sin(h)
    return quat(math.cos(h), axis[0] * s, axis[1] * s, axis[2] * s)


def quat_from_gyro(gyro_rad_s: Vec3, dt: float) -> Quat:
    """Delta rotation from an angular-velocity vector over dt seconds."""
    omega = float(np.linalg.norm(gyro_rad_s))
    if omega < 1e-9:
        return quat_identity()
    return quat_from_axis_angle(gyro_rad_s, omega * dt)


def quat_slerp(a: Quat, b: Quat, t: float) -> Quat:
    a = quat_normalize(a)
    b = quat_normalize(b)
    d = quat_dot(a, b)
    if d < 0.0:
        b = -b
        d = -d
    if d > 0.9995:
        return quat_normalize(a + t * (b - a))
    theta0 = math.acos(max(-1.0, min(1.0, d)))
    theta = theta0 * t
    sin_theta0 = math.sin(theta0)
    s0 = math.sin(theta0 - theta) / sin_theta0
    s1 = math.sin(theta) / sin_theta0
    return quat_normalize(s0 * a + s1 * b)


def quat_angle_between(a: Quat, b: Quat) -> float:
    """Smallest angle (radians) between two orientations."""
    d = abs(quat_dot(quat_normalize(a), quat_normalize(b)))
    d = max(-1.0, min(1.0, d))
    return 2.0 * math.acos(d)


def clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value
