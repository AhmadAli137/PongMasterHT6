"""Webcam tracking → paddle position (MediaPipe), in two flavours.

Fills the same slot the AprilTag detector would: it emits :class:`TagObservation`
objects the fusion consumes as a *position* source (the IMU still drives
orientation). Two modes:

* ``hands`` — the finger skeleton + a gesture label; wrist maps to left/right +
  up/down, depth held fixed. Light and precise for the paddle hand.
* ``pose``  — the upper body (shoulders/elbows/wrists); the tracked wrist's 3D
  world-z gives real reach **depth** on top of left/right + up/down.

A background thread owns the camera + graph so the game loop never blocks.
``poll()`` returns the newest observation; ``latest_jpeg()`` the annotated frame.
"""

from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

import numpy as np

from ..clock import now_us
from ..config import CameraConfig
from ..mathutil import clamp, quat_identity, vec3
from .apriltag_detector import TagObservation

# clamp the mapped position into a sane reach envelope around the table
_X_LIMIT = 0.72
_Y_MIN, _Y_MAX = 0.85, 1.70

_ASSETS = Path(__file__).parent / "assets"
_MODELS = {
    "hands": (
        "hand_landmarker.task",
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
        "hand_landmarker/float16/1/hand_landmarker.task",
    ),
    "pose": (
        "pose_landmarker.task",
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    ),
}


def _ensure_model(kind: str) -> str:
    """Return the model path for 'hands'/'pose', downloading it once if missing."""
    name, url = _MODELS[kind]
    path = _ASSETS / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, path)  # one-time, few MB
    return str(path)


# hand topology (21 landmarks)
_HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)
_FINGERS = {"index": (8, 6), "middle": (12, 10), "ring": (16, 14), "pinky": (20, 18)}

# upper-body pose topology (subset of the 33 landmarks we care about)
_L_SH, _R_SH, _L_EL, _R_EL, _L_WR, _R_WR, _L_HIP, _R_HIP = 11, 12, 13, 14, 15, 16, 23, 24
_POSE_CONNECTIONS = (
    (_L_SH, _R_SH), (_L_SH, _L_EL), (_L_EL, _L_WR),
    (_R_SH, _R_EL), (_R_EL, _R_WR),
    (_L_SH, _L_HIP), (_R_SH, _R_HIP), (_L_HIP, _R_HIP),
)
_POSE_DRAW = (_L_SH, _R_SH, _L_EL, _R_EL, _L_WR, _R_WR, _L_HIP, _R_HIP)


def _classify_gesture(lm) -> str:
    """Cheap hand-shape label from landmark geometry (fun overlay, not exact)."""
    import math

    def d(a, b):
        return math.hypot(lm[a].x - lm[b].x, lm[a].y - lm[b].y)

    palm = max(1e-6, d(0, 9))
    if d(4, 8) < 0.35 * palm:
        return "Pinch"
    extended = sum(1 for tip, pip in _FINGERS.values() if d(tip, 0) > d(pip, 0) * 1.15)
    thumb_out = d(4, 0) > d(3, 0) * 1.1
    total = extended + (1 if thumb_out else 0)
    return {0: "Fist", 1: "Point", 2: "Two", 3: "Three", 4: "Four", 5: "Open"}.get(
        total, f"{total} fingers"
    )


class MediaPipeHandSource:
    """Tracks the paddle hand (or upper body) and maps it into paddle position."""

    def __init__(self, cfg: CameraConfig):
        self._cfg = cfg
        self._mode = cfg.mp_mode if cfg.mp_mode in _MODELS else "hands"
        self._lock = threading.Lock()
        self._latest: TagObservation | None = None
        self._seq = 0
        self._last_polled = -1
        self._smooth: np.ndarray | None = None
        self._jpeg: bytes | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._frames = 0
        self._detections = 0
        self._fps_mark_us = now_us()
        self._camera_fps = 0.0
        self._tag_fps = 0.0

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="mediapipe", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def poll(self) -> TagObservation | None:
        with self._lock:
            if self._seq == self._last_polled:
                return None
            self._last_polled = self._seq
            return self._latest

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def camera_fps(self) -> float:
        return self._camera_fps

    def tag_fps(self) -> float:
        return self._tag_fps

    # ------------------------------------------------------------------ #
    def _map(self, nx: float, ny: float, gz: float | None = None) -> np.ndarray:
        """Normalized image (x right, y down) + optional depth → game position."""
        c = self._cfg
        gx = clamp(c.mp_x_sign * (0.5 - nx) * c.mp_x_span, -_X_LIMIT, _X_LIMIT)
        gy = clamp(c.mp_y_base + (0.5 - ny) * c.mp_y_span, _Y_MIN, _Y_MAX)
        z = c.mp_z_plane if gz is None else gz
        pos = vec3(float(gx), float(gy), float(z))
        a = clamp(1.0 - c.mp_smoothing, 0.05, 1.0)
        if self._smooth is None:
            self._smooth = pos
        else:
            self._smooth = (1.0 - a) * self._smooth + a * pos
        return self._smooth.copy()

    def _publish(self, obs: TagObservation) -> None:
        with self._lock:
            self._latest = obs
            self._seq += 1

    def _hit(self, pos, conf) -> None:
        self._detections += 1
        self._publish(TagObservation(
            capture_time_us=now_us(), processed_time_us=now_us(), tag_ids=[0],
            position_camera_m=pos, orientation_camera_quat=quat_identity(),
            reprojection_error_px=0.0, decision_margin=60.0,
            confidence=clamp(conf, 0.0, 1.0), valid=True,
        ))

    def _miss(self) -> None:
        self._publish(TagObservation(
            capture_time_us=now_us(), processed_time_us=now_us(), tag_ids=[],
            position_camera_m=vec3(), orientation_camera_quat=quat_identity(),
            reprojection_error_px=99.0, decision_margin=0.0, confidence=0.0, valid=False,
        ))

    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        try:
            import cv2
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "MediaPipe tracking needs 'mediapipe' and 'opencv' installed: "
                "pip install mediapipe opencv-contrib-python"
            ) from exc

        base = mp_python.BaseOptions(model_asset_path=_ensure_model(self._mode))
        if self._mode == "pose":
            opts = vision.PoseLandmarkerOptions(
                base_options=base, running_mode=vision.RunningMode.VIDEO, num_poses=1)
            landmarker = vision.PoseLandmarker.create_from_options(opts)
        else:
            opts = vision.HandLandmarkerOptions(
                base_options=base, running_mode=vision.RunningMode.VIDEO, num_hands=1,
                min_hand_detection_confidence=0.5, min_tracking_confidence=0.5)
            landmarker = vision.HandLandmarker.create_from_options(opts)

        cap = cv2.VideoCapture(self._cfg.mp_camera_index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # newest frame only (low lag)
        last_ts = -1
        try:
            while self._running:
                ok, frame = cap.read()
                if not ok:
                    continue
                t = now_us()
                self._frames += 1
                self._update_fps(t)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                ts_ms = max(last_ts + 1, t // 1000)
                last_ts = ts_ms
                if self._mode == "pose":
                    self._step_pose(cv2, frame, landmarker.detect_for_video(image, ts_ms))
                else:
                    self._step_hands(cv2, frame, landmarker.detect_for_video(image, ts_ms))
        finally:
            landmarker.close()
            cap.release()

    def _step_hands(self, cv2, frame, res) -> None:
        gesture = ""
        if res.hand_landmarks:
            lm = res.hand_landmarks[0]
            score = float(res.handedness[0][0].score) if res.handedness else 0.9
            gesture = _classify_gesture(lm)
            self._hit(self._map(lm[0].x, lm[0].y), score)
        else:
            self._miss()
        pts = [lm for lm in res.hand_landmarks[0]] if res.hand_landmarks else None
        self._render(cv2, frame, pts, _HAND_CONNECTIONS, 0, gesture or "no hand")

    def _step_pose(self, cv2, frame, res) -> None:
        c = self._cfg
        wr = _R_WR if c.mp_pose_wrist == "right" else _L_WR
        label = "no body"
        if res.pose_landmarks:
            lm = res.pose_landmarks[0]
            wz = 0.0
            if res.pose_world_landmarks:
                wz = float(res.pose_world_landmarks[0][wr].z)
            gz = clamp(c.mp_z_plane + wz * c.mp_depth_scale, c.mp_depth_min, c.mp_depth_max)
            conf = getattr(lm[wr], "visibility", 0.9) or 0.9
            self._hit(self._map(lm[wr].x, lm[wr].y, gz), conf)
            label = f"{'R' if wr == _R_WR else 'L'} wrist  depth {gz:.2f}"
            self._render(cv2, frame, lm, _POSE_CONNECTIONS, wr, label, _POSE_DRAW)
        else:
            self._miss()
            self._render(cv2, frame, None, _POSE_CONNECTIONS, wr, label)

    def _render(self, cv2, frame, lms, connections, wrist_idx, label, draw_only=None) -> None:
        """Mirror the frame, draw the skeleton + tracked-wrist highlight + label."""
        h, w = frame.shape[:2]
        view = cv2.flip(frame, 1)
        if lms is not None:
            pts = [(w - int(p.x * w), int(p.y * h)) for p in lms]
            for a, b in connections:
                cv2.line(view, pts[a], pts[b], (0, 230, 0), 2)
            for i in (draw_only if draw_only is not None else range(len(pts))):
                cv2.circle(view, pts[i], 4, (0, 160, 255), -1)
            cv2.circle(view, pts[wrist_idx], 9, (255, 90, 90), 2)  # tracked point
            color = (255, 255, 255)
        else:
            color = (120, 120, 120)
        cv2.putText(view, label, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with self._lock:
                self._jpeg = buf.tobytes()

    def _update_fps(self, t: int) -> None:
        elapsed = (t - self._fps_mark_us) / 1e6
        if elapsed >= 0.5:
            self._camera_fps = self._frames / elapsed
            self._tag_fps = self._detections / elapsed
            self._frames = 0
            self._detections = 0
            self._fps_mark_us = t
