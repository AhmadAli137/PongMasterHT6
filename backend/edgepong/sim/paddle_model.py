"""Ground-truth paddle motion for simulation mode.

Holds a single authoritative pose that both the mock camera and mock IMU derive
their (noisy, delayed) observations from. In ``autoplay`` the paddle moves to
intercept the nearest approaching ball so the vertical slice produces real hits
with no human in the loop — great for a headless demo or CI.

A human can take over at any time via :meth:`set_external_input` (mouse control
from the browser): the external target replaces the autoplay brain while input
stays fresh, and the AI resumes after ``EXTERNAL_TIMEOUT_S`` of silence. The
mock camera/IMU pipeline is untouched either way, so fusion and collision are
exercised identically.

Thread-safe: the game loop calls :meth:`update` while mock sender threads call
:meth:`snapshot` and the web thread calls :meth:`set_external_input`.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

import numpy as np

from ..clock import now_us
from ..config import GameConfig, PaddleConfig
from ..mathutil import (
    Quat,
    Vec3,
    quat_from_axis_angle,
    quat_mul,
    quat_normalize,
    vec3,
)
from ..types import Ball, BallLifecycle, BallType


@dataclass
class GroundTruth:
    timestamp_us: int
    position: Vec3
    orientation: Quat
    linear_velocity: Vec3
    angular_velocity: Vec3


def _base_orientation() -> Quat:
    # Face the wall: rotate identity 180° about world +Y so the +Z face normal
    # points toward -Z (toward incoming balls).
    return quat_from_axis_angle(vec3(0.0, 1.0, 0.0), math.pi)


class SimPaddleModel:
    def __init__(self, game_cfg: GameConfig, paddle_cfg: PaddleConfig):
        self._game = game_cfg
        self._paddle = paddle_cfg
        self._lock = threading.Lock()
        self._pos = vec3(0.0, 1.2, game_cfg.paddle_plane_z_m)
        self._target = self._pos.copy()
        self._vel = vec3()
        self._base_q = _base_orientation()
        self._orient = self._base_q.copy()
        self._ang_vel = vec3()
        self._prev_orient = self._orient.copy()
        self._t = now_us()
        # idle wander phase so the paddle is never perfectly still when no balls
        self._phase = 0.0
        # balance mode: hold the paddle face-up like a tray with a gentle wobble
        self.balance_mode = False
        # serve mode (set by the game service on the player's serve): the face
        # opens under the ball and the strike brushes up-forward like a real serve
        self.serve_mode = False
        # external (mouse) control — see set_external_input()
        self._ext_target = vec3(0.0, 1.2, game_cfg.paddle_plane_z_m)
        self._ext_tilt = 0.0          # wrist pitch about paddle X (wheel / right-drag)
        self._ext_yaw = 0.0           # wrist yaw about the handle axis (right-drag)
        self._ext_backhand = False    # quick right-click toggles the stance
        self._ext_last_us = 0         # 0 = never received input
        self._ext_vel = vec3()        # reported hand (swing) velocity, world m/s
        self._ext_vel_us = 0          # freshness of the swing sample
        self._strike_t = 0.0          # forward-lunge time remaining (sim seconds)
        self._strike_dur = self.STRIKE_DURATION_S
        self._strike_power = 1.0      # 0..1 derived from swing speed at strike

    EXTERNAL_TIMEOUT_S = 3.0   # AI resumes after this much input silence
    STRIKE_DURATION_S = 0.14
    STRIKE_REACH_M = 0.45      # full-power lunge depth toward the wall
    MAX_TILT_RAD = 1.2         # ~69° each way — enough to fully open/close the face
    MAX_YAW_RAD = 0.7          # wrist twist for lateral aim
    SWING_DECAY_S = 0.15       # reported hand velocity fades to zero this fast
    SWING_FULL_MPS = 5.0       # hand speed that counts as a full-power swing
    SERVE_PITCH_RAD = 0.7      # face opens ~40° under the toss on your serve
    SERVE_UP_REACH_M = 0.30    # serve strike brushes upward this far
    SERVE_STRIKE_S = 0.22      # a serve stroke is longer/loopier than a punch

    # ------------------------------------------------------------------ #
    def set_external_input(
        self,
        x: float | None = None,
        y: float | None = None,
        tilt_delta: float = 0.0,
        yaw_delta: float = 0.0,
        vx: float | None = None,
        vy: float | None = None,
        strike: bool = False,
        strike_power: float | None = None,
        flip: bool = False,
    ) -> None:
        """Feed one mouse-input sample (world-frame x/y on the paddle plane).

        ``vx/vy`` is the measured hand (swing) velocity in world m/s; strike
        power derives from it unless explicitly given, so power comes from the
        actual swing, not a button timer.
        """
        with self._lock:
            now = now_us()
            if x is not None:
                self._ext_target[0] = float(np.clip(x, -0.9, 0.9))
            if y is not None:
                self._ext_target[1] = float(np.clip(y, 0.35, 1.9))
            if vx is not None or vy is not None:
                self._ext_vel[0] = float(np.clip(vx if vx is not None else 0.0, -12.0, 12.0))
                self._ext_vel[1] = float(np.clip(vy if vy is not None else 0.0, -12.0, 12.0))
                self._ext_vel_us = now
            if tilt_delta:
                self._ext_tilt = float(
                    np.clip(self._ext_tilt + tilt_delta, -self.MAX_TILT_RAD, self.MAX_TILT_RAD)
                )
            if yaw_delta:
                self._ext_yaw = float(
                    np.clip(self._ext_yaw + yaw_delta, -self.MAX_YAW_RAD, self.MAX_YAW_RAD)
                )
            if flip:
                self._ext_backhand = not self._ext_backhand
            if strike:
                if strike_power is None:
                    # power comes from how fast the hand is actually moving
                    speed = float(np.linalg.norm(self._hand_velocity(now)))
                    strike_power = 0.25 + speed / self.SWING_FULL_MPS
                self._strike_power = float(np.clip(strike_power, 0.0, 1.0))
                base_dur = self.SERVE_STRIKE_S if self.serve_mode else self.STRIKE_DURATION_S
                self._strike_dur = base_dur + 0.06 * self._strike_power
                self._strike_t = self._strike_dur
            self._ext_last_us = now

    def _hand_velocity(self, now: int) -> Vec3:
        """Reported swing velocity, decayed by sample age (stale = stopped)."""
        if self._ext_vel_us == 0:
            return vec3()
        age_s = (now - self._ext_vel_us) / 1e6
        factor = max(0.0, 1.0 - age_s / self.SWING_DECAY_S)
        return self._ext_vel * factor

    @property
    def backhand(self) -> bool:
        return self._ext_backhand

    @property
    def striking(self) -> bool:
        return self._strike_t > 0.0

    def ai_strike(self, power: float = 0.7) -> None:
        """Autoplay strike (e.g. hitting its own toss serve).

        Unlike set_external_input this does NOT refresh the external-control
        timestamp, so triggering it never steals control from / hands control
        to the mouse.
        """
        with self._lock:
            self._strike_power = float(np.clip(power, 0.0, 1.0))
            self._strike_dur = self.STRIKE_DURATION_S + 0.06 * self._strike_power
            self._strike_t = self._strike_dur

    @property
    def external_active(self) -> bool:
        if self._ext_last_us == 0:
            return False
        return (now_us() - self._ext_last_us) < self.EXTERNAL_TIMEOUT_S * 1e6

    # ------------------------------------------------------------------ #
    def update(self, dt: float, balls: list[Ball]) -> None:
        if dt <= 0.0:
            return
        with self._lock:
            self._phase += dt
            external = self.external_active
            if self.balance_mode:
                self._update_balance(dt, external)
                return

            if external:
                target = self._ext_target.copy()
                max_speed = 14.0  # fast enough that a genuine flick registers
            else:
                target = self._compute_target(balls)
                max_speed = 6.0  # m/s of ground-truth paddle travel

            # strike lunge: punch toward the wall and recover, giving the
            # paddle real forward velocity. Applies to mouse strikes AND the
            # AI hitting its own toss serve. Power comes from the swing speed
            # (or ai_strike's argument). Driven by sim dt so fixed-step is exact.
            if self._strike_t > 0.0:
                lunge = math.sin(math.pi * (1.0 - self._strike_t / self._strike_dur))
                scale = 0.35 + 0.65 * self._strike_power
                if self.serve_mode:
                    # serve stroke: brush up-and-forward under the falling toss
                    target[1] = target[1] - 0.10 + self.SERVE_UP_REACH_M * scale * lunge
                    target[2] = self._game.paddle_plane_z_m - 0.6 * self.STRIKE_REACH_M * scale * lunge
                else:
                    target[2] = self._game.paddle_plane_z_m - self.STRIKE_REACH_M * scale * lunge
                self._strike_t = max(0.0, self._strike_t - dt)

            to_target = target - self._pos
            dist = float(np.linalg.norm(to_target))
            if dist > 1e-6:
                step = min(dist, max_speed * dt)
                direction = to_target / dist
                new_pos = self._pos + direction * step
            else:
                new_pos = self._pos
            self._vel = (new_pos - self._pos) / dt
            self._pos = new_pos

            if external:
                # swing realism: the reported hand velocity IS the paddle's
                # lateral velocity (what impact strength and rebound transfer
                # feel), blended with the position-follow component
                hand = self._hand_velocity(now_us())
                self._vel[0] = 0.35 * self._vel[0] + 0.65 * hand[0]
                self._vel[1] = 0.35 * self._vel[1] + 0.65 * hand[1]

                # wrist: pitch about local X (wheel / right-drag vertical) and
                # yaw about the handle axis (right-drag horizontal). Backhand
                # flips the blade 180° about the handle; both wrist signs flip
                # with it so the controls keep meaning the same thing.
                sign = -1.0 if self._ext_backhand else 1.0
                # on your serve the face opens automatically under the toss;
                # the wheel still fine-tunes the angle on top
                pitch = self._ext_tilt + (self.SERVE_PITCH_RAD if self.serve_mode else 0.0)
                pitch = float(np.clip(pitch, -self.MAX_TILT_RAD, 1.35))
                # negative local-X rotation opens the striking face upward for
                # positive pitch, in both stances (local X flips with backhand)
                tilt = quat_from_axis_angle(vec3(1.0, 0.0, 0.0), -pitch * sign)
                yaw = quat_from_axis_angle(vec3(0.0, 1.0, 0.0), self._ext_yaw * sign)
                stance = (
                    quat_from_axis_angle(vec3(0.0, 1.0, 0.0), math.pi)
                    if self._ext_backhand
                    else quat_from_axis_angle(vec3(0.0, 1.0, 0.0), 0.0)
                )
                new_orient = quat_normalize(
                    quat_mul(self._base_q, quat_mul(stance, quat_mul(yaw, tilt)))
                )
            else:
                # small orientation tilt proportional to lateral velocity for realism
                tilt = quat_from_axis_angle(
                    vec3(0.0, 0.0, 1.0), float(np.clip(-self._vel[0] * 0.12, -0.4, 0.4))
                )
                # open face slightly (like a real player) so returns lift over
                # the net — and much wider under its own serve toss
                open_face = quat_from_axis_angle(
                    vec3(1.0, 0.0, 0.0), 0.55 if self.serve_mode else 0.22
                )
                new_orient = quat_normalize(
                    quat_mul(open_face, quat_mul(self._base_q, tilt))
                )
            self._ang_vel = self._angular_velocity(self._prev_orient, new_orient, dt)
            self._prev_orient = new_orient
            self._orient = new_orient
            self._t = now_us()

    def _update_balance(self, dt: float, external: bool = False) -> None:
        """Hold the paddle level like a tray; wobble it (AI) or steer it (mouse)."""
        target = vec3(0.0, 1.1, self._game.paddle_plane_z_m)
        to_target = target - self._pos
        dist = float(np.linalg.norm(to_target))
        if dist > 1e-6:
            step = min(dist, 1.5 * dt)
            new_pos = self._pos + (to_target / dist) * step
        else:
            new_pos = self._pos
        self._vel = (new_pos - self._pos) / dt
        self._pos = new_pos

        # face-up base: rotate -90° about X so the +Z face normal points up
        base = quat_from_axis_angle(vec3(1.0, 0.0, 0.0), -math.pi / 2)
        if external:
            # mouse steers the tray: offset from neutral maps to tilt so the
            # player actively balances the rolling ball
            # mouse right → +x edge dips → ball rolls right; mouse up → far edge
            # dips → ball rolls away (signs verified against the gravity math)
            max_tilt = 0.30
            tilt_z = float(np.clip(-(self._ext_target[0]) * 0.8, -max_tilt, max_tilt))
            tilt_x = float(np.clip(-(self._ext_target[1] - 1.1) * 1.2, -max_tilt, max_tilt))
        else:
            tilt_x = 0.10 * math.sin(self._phase * 0.7)
            tilt_z = 0.10 * math.sin(self._phase * 0.9 + 1.3)
        wobble = quat_mul(
            quat_from_axis_angle(vec3(1.0, 0.0, 0.0), tilt_x),
            quat_from_axis_angle(vec3(0.0, 0.0, 1.0), tilt_z),
        )
        new_orient = quat_normalize(quat_mul(wobble, base))
        self._ang_vel = self._angular_velocity(self._prev_orient, new_orient, dt)
        self._prev_orient = new_orient
        self._orient = new_orient
        self._t = now_us()

    def _compute_target(self, balls: list[Ball]) -> Vec3:
        plane_z = self._game.paddle_plane_z_m
        best: tuple[float, Vec3] | None = None
        for b in balls:
            if b.state is not BallLifecycle.APPROACHING:
                continue
            if b.type is BallType.AVOID:
                continue
            # rally balls bounce en route, so linear prediction is wrong — the
            # spawner/opponent pre-computed the true hittable point instead
            est = (plane_z - float(b.position[2])) / max(float(b.velocity[2]), 0.5)
            est = max(0.0, est)
            target = vec3(float(b.target_zone[0]), float(b.target_zone[1]), plane_z)
            if best is None or est < best[0]:
                best = (est, target)
        if best is not None and self._paddle.sim_autoplay:
            return best[1]
        # idle: gentle lissajous wander around neutral so tracking looks alive
        cx = 0.35 * math.sin(self._phase * 0.9)
        cy = 1.2 + 0.25 * math.sin(self._phase * 0.6 + 1.0)
        return vec3(cx, cy, plane_z)

    @staticmethod
    def _angular_velocity(q_prev: Quat, q_now: Quat, dt: float) -> Vec3:
        # relative rotation q_prev^-1 * q_now -> axis*angle / dt
        from ..mathutil import quat_conjugate

        dq = quat_mul(quat_conjugate(q_prev), q_now)
        w = max(-1.0, min(1.0, dq[0]))
        angle = 2.0 * math.acos(w)
        s = math.sqrt(max(0.0, 1.0 - w * w))
        if s < 1e-6 or dt <= 0.0:
            return vec3()
        axis = dq[1:4] / s
        return axis * (angle / dt)

    # ------------------------------------------------------------------ #
    def snapshot(self) -> GroundTruth:
        with self._lock:
            return GroundTruth(
                timestamp_us=self._t,
                position=self._pos.copy(),
                orientation=self._orient.copy(),
                linear_velocity=self._vel.copy(),
                angular_velocity=self._ang_vel.copy(),
            )
