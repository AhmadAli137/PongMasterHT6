"""'Ball rolling on paddle' balance mode (spec §18).

A virtual ball rests on the virtual paddle. Gravity is transformed into
paddle-local coordinates; only the components parallel to the face accelerate
the ball, which is integrated in 2D with rolling damping and released when it
crosses the paddle boundary. This constraint model is far more stable than
unconstrained rigid-body contact (spec §18.2).

Pure state + math — the game service drives it and maps it back to world space.
"""

from __future__ import annotations

from ..mathutil import Quat, Vec3, quat_conjugate, quat_rotate, vec3
from ..types import PaddlePose

_GRAVITY_WORLD = vec3(0.0, -9.81, 0.0)
_ROLLING_FACTOR = 5.0 / 7.0     # solid-sphere rolling acceleration on an incline
_DAMPING_PER_S = 1.2            # rolling friction / drag
_RESPAWN_S = 1.2                # delay before the ball reappears at centre


class BalanceSim:
    def __init__(self, half_w: float, half_h: float, radius: float = 0.02):
        self.half_w = half_w
        self.half_h = half_h
        self.radius = radius
        self.lx = 0.0   # paddle-local metres, +x toward local right
        self.ly = 0.0   # paddle-local metres, +y toward local top
        self.vx = 0.0
        self.vy = 0.0
        self.on_paddle = True
        self._respawn_s = 0.0

    def reset(self) -> None:
        self.lx = self.ly = self.vx = self.vy = 0.0
        self.on_paddle = True
        self._respawn_s = 0.0

    def step(self, dt: float, orientation: Quat) -> str | None:
        """Advance one step. Returns "edge" once when the ball rolls off."""
        if not self.on_paddle:
            self._respawn_s -= dt
            if self._respawn_s <= 0.0:
                self.reset()
            return None

        # gravity in paddle-local coordinates; keep only in-plane components
        g_local = quat_rotate(quat_conjugate(orientation), _GRAVITY_WORLD)
        ax = float(g_local[0]) * _ROLLING_FACTOR
        ay = float(g_local[1]) * _ROLLING_FACTOR

        self.vx += ax * dt
        self.vy += ay * dt
        damp = max(0.0, 1.0 - _DAMPING_PER_S * dt)
        self.vx *= damp
        self.vy *= damp
        self.lx += self.vx * dt
        self.ly += self.vy * dt

        if abs(self.lx) > self.half_w or abs(self.ly) > self.half_h:
            self.on_paddle = False
            self._respawn_s = _RESPAWN_S
            return "edge"
        return None

    def world_position(self, pose: PaddlePose) -> Vec3:
        """Ball centre in world space, resting on the paddle face."""
        right = quat_rotate(pose.orientation, vec3(1.0, 0.0, 0.0))
        up = quat_rotate(pose.orientation, vec3(0.0, 1.0, 0.0))
        normal = quat_rotate(pose.orientation, vec3(0.0, 0.0, 1.0))
        return (
            pose.position_m
            + right * self.lx
            + up * self.ly
            + normal * self.radius
        )
