"""Complementary pose fusion + short-horizon prediction (spec §13).

Camera gives absolute (but delayed/jittery) position and long-term orientation;
IMU gives fast relative orientation. We:

* use camera for position (with outlier rejection + velocity smoothing),
* let IMU drive fast orientation and correct its drift slowly toward camera,
* predict a small horizon forward to hide sensing + projector latency,
* decay confidence and stop predicting after ``max_prediction_ms`` on tag loss.

This is intentionally a lightweight complementary filter, not an EKF — it is
robust, cheap, and easy to reason about for a hackathon.
"""

from __future__ import annotations

import numpy as np

from ..camera.apriltag_detector import TagObservation
from ..clock import now_us
from ..config import FusionConfig, PaddleConfig
from ..mathutil import (
    Quat,
    Vec3,
    clamp,
    quat_conjugate,
    quat_from_gyro,
    quat_mul,
    quat_normalize,
    quat_slerp,
    quat_angle_between,
    vec3,
)
from ..paddle.packets import PaddleTelemetry, STATUS_IMU_CALIBRATED
from ..types import PaddlePose, TrackingState

SOURCE_CAMERA = 1 << 0
SOURCE_IMU = 1 << 1
SOURCE_PREDICTED = 1 << 2

# Physical plausibility limits for outlier rejection.
_MAX_JUMP_M = 0.6           # per-camera-update position jump
_MAX_REPROJ_PX = 3.5


def _parse_axis_remap(spec: str) -> tuple[np.ndarray, float]:
    """Turn an "x,-y,z"-style spec into a signed-permutation matrix P (game_vec
    = P @ imu_vec) plus its determinant. Falls back to identity on bad input."""
    idx = {"x": 0, "y": 1, "z": 2}
    P = np.zeros((3, 3))
    try:
        parts = [p.strip().lower() for p in spec.split(",")]
        if len(parts) != 3:
            raise ValueError(spec)
        for i, p in enumerate(parts):
            sign = -1.0 if p.startswith(("-", "+")) and p[0] == "-" else 1.0
            P[i, idx[p[-1]]] = sign
        if not np.allclose(np.abs(P).sum(axis=0), 1.0):  # each source used once
            raise ValueError(spec)
    except (KeyError, ValueError, IndexError):
        P = np.eye(3)
    return P, float(np.linalg.det(P))


class PoseFusion:
    def __init__(self, fusion_cfg: FusionConfig, paddle_cfg: PaddleConfig):
        self._f = fusion_cfg
        self._p = paddle_cfg
        self._imu_only = fusion_cfg.imu_only
        # imu-only: the first still orientation becomes "neutral" so however you
        # hold the paddle at startup reads as a flat, forward-facing blade.
        self._imu_ref: Quat | None = None
        # axis remap for the sensor's mounting (see FusionConfig.imu_axes)
        self._axis_P, self._axis_det = _parse_axis_remap(fusion_cfg.imu_axes)
        self._position = vec3(0.0, 1.2, 1.8)
        self._orientation = quat_normalize(np.array([1.0, 0.0, 0.0, 0.0]))
        self._imu_orientation = self._orientation.copy()
        self._correction = quat_normalize(np.array([1.0, 0.0, 0.0, 0.0]))
        self._linear_velocity = vec3()
        self._angular_velocity = vec3()
        self._confidence = 0.0
        self._last_camera_us = 0
        self._last_camera_pos: Vec3 | None = None
        self._last_camera_time: int | None = None
        self._last_imu_us = 0
        self._tracking = TrackingState.LOST

    # ------------------------------------------------------------------ #
    def on_camera(self, obs: TagObservation) -> None:
        if not obs.valid or not obs.tag_ids:
            return
        if obs.reprojection_error_px > _MAX_REPROJ_PX:
            return

        pos = np.asarray(obs.position_camera_m, dtype=np.float64)

        # outlier rejection: reject implausible position jumps
        if self._last_camera_pos is not None:
            jump = float(np.linalg.norm(pos - self._last_camera_pos))
            if jump > _MAX_JUMP_M:
                # down-weight rather than snap
                return

        # velocity from timestamped camera positions
        if self._last_camera_pos is not None and self._last_camera_time is not None:
            dt = (obs.capture_time_us - self._last_camera_time) / 1e6
            if dt > 1e-4:
                inst_v = (pos - self._last_camera_pos) / dt
                self._linear_velocity = 0.5 * self._linear_velocity + 0.5 * inst_v
        self._last_camera_pos = pos
        self._last_camera_time = obs.capture_time_us

        # blend fused position toward camera position
        a = self._f.camera_position_alpha
        self._position = (1.0 - a) * self._position + a * pos

        # slowly correct IMU orientation drift toward camera orientation
        cam_q = quat_normalize(np.asarray(obs.orientation_camera_quat, dtype=np.float64))
        self._orientation = quat_slerp(
            self._orientation, cam_q, self._f.camera_orientation_alpha
        )
        self._last_camera_us = obs.processed_time_us
        # boost confidence toward the observation confidence
        self._confidence = clamp(0.4 * self._confidence + 0.6 * obs.confidence, 0.0, 1.0)

    def on_imu(self, tel: PaddleTelemetry) -> None:
        # A paddle that hasn't calibrated its IMU (or has none wired yet) sends
        # an identity quaternion / zero gyro — using it would drag orientation
        # to "flat" and stall angular prediction. Honour the calibrated flag:
        # mark the link alive but leave orientation to the camera until the
        # real IMU comes online.
        if not (tel.status_bits & STATUS_IMU_CALIBRATED):
            self._last_imu_us = now_us()
            return
        q = quat_normalize(
            np.array([tel.quat_w, tel.quat_x, tel.quat_y, tel.quat_z], dtype=np.float64)
        )
        gyro = vec3(tel.gyro_x, tel.gyro_y, tel.gyro_z)
        # remap for the sensor's mounting: rotate the quaternion's axis (and the
        # gyro pseudovector) into the game frame. det factor handles mirrored maps.
        vec_g = self._axis_det * (self._axis_P @ q[1:])
        q = quat_normalize(np.array([q[0], vec_g[0], vec_g[1], vec_g[2]]))
        gyro = self._axis_det * (self._axis_P @ gyro)
        self._imu_orientation = q
        self._angular_velocity = gyro
        now = now_us()
        self._last_imu_us = now

        if self._imu_only:
            # No camera to correct against: the IMU IS the tracker. Take its
            # orientation directly (responsive) and hold a steady tracked state
            # so gameplay runs. Position stays at the fixed home point.
            if self._imu_ref is None:
                self._imu_ref = quat_conjugate(q)  # capture neutral pose
            self._orientation = quat_normalize(quat_mul(self._imu_ref, q))
            self._last_camera_us = now
            self._confidence = 0.9
            return

        # IMU provides fast orientation; camera corrects it in on_camera().
        w = self._f.imu_orientation_weight
        self._orientation = quat_slerp(self._orientation, q, w * 0.15)

    def recenter(self) -> None:
        """Re-capture the current paddle pose as neutral (imu-only mode)."""
        self._imu_ref = None

    # ------------------------------------------------------------------ #
    def step(self, now: int | None = None) -> PaddlePose:
        now = now if now is not None else now_us()
        cam_age_ms = (now - self._last_camera_us) / 1000.0 if self._last_camera_us else 1e9
        imu_age_ms = (now - self._last_imu_us) / 1000.0 if self._last_imu_us else 1e9

        source = 0
        if cam_age_ms < self._p.stale_ms:
            source |= SOURCE_CAMERA
        if imu_age_ms < self._p.stale_ms:
            source |= SOURCE_IMU

        # ---- tracking state machine (spec §11.2) ---------------------- #
        if cam_age_ms > self._p.max_prediction_ms or self._confidence < 0.30:
            self._tracking = TrackingState.LOST
        elif cam_age_ms > self._p.stale_ms or self._confidence < 0.75:
            self._tracking = TrackingState.DEGRADED
        else:
            self._tracking = TrackingState.GOOD

        # confidence decays while the tag is missing
        if cam_age_ms > self._p.stale_ms:
            decay = clamp(1.0 - (cam_age_ms - self._p.stale_ms) / 400.0, 0.0, 1.0)
            self._confidence = min(self._confidence, decay)

        # ---- short-horizon prediction --------------------------------- #
        horizon_ms = clamp(
            imu_age_ms + self._f.max_angular_prediction_ms,
            0.0,
            self._f.max_angular_prediction_ms,
        )
        horizon_s = horizon_ms / 1000.0

        pred_orient = self._orientation
        if imu_age_ms < self._p.stale_ms and np.linalg.norm(self._angular_velocity) > 1e-4:
            dq = quat_from_gyro(self._angular_velocity, horizon_s)
            pred_orient = quat_normalize(quat_mul(self._orientation, dq))
            source |= SOURCE_PREDICTED

        pred_pos = self._position
        if self._tracking is not TrackingState.LOST:
            # conservative, damped position extrapolation
            pred_pos = self._position + self._linear_velocity * horizon_s * 0.5
            source |= SOURCE_PREDICTED
        else:
            # stop extrapolating; also decay stale linear velocity
            self._linear_velocity *= 0.9

        return PaddlePose(
            timestamp_us=now,
            position_m=pred_pos.copy(),
            orientation=pred_orient.copy(),
            linear_velocity_mps=self._linear_velocity.copy(),
            angular_velocity_rad_s=self._angular_velocity.copy(),
            confidence=self._confidence,
            source_flags=source,
            tracking_state=self._tracking,
        )
