"""Webcam hand tracking → paddle position (MediaPipe Hands).

Fills the same slot the AprilTag detector would: it emits :class:`TagObservation`
objects the fusion already knows how to consume as a *position* source. Only
position is produced here — the IMU keeps driving orientation — so this is the
2D build: the wrist landmark maps to the paddle's left/right + up/down, with
depth held at a fixed plane.

A background thread owns the camera + the MediaPipe graph so the fixed-step game
loop never blocks on a frame. ``poll()`` hands back the newest observation.
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

_MODEL_PATH = Path(__file__).parent / "assets" / "hand_landmarker.task"
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)


def _ensure_model() -> str:
    """Return the HandLandmarker model path, downloading it once if missing."""
    if not _MODEL_PATH.exists():
        _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)  # ~7.8 MB, one time
    return str(_MODEL_PATH)


class MediaPipeHandSource:
    """Tracks one hand's wrist and maps it into game-space paddle position."""

    def __init__(self, cfg: CameraConfig):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._latest: TagObservation | None = None
        self._seq = 0
        self._last_polled = -1
        self._smooth: np.ndarray | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        # rolling fps counters (read by the metrics aggregator)
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

    def camera_fps(self) -> float:
        return self._camera_fps

    def tag_fps(self) -> float:
        return self._tag_fps

    # ------------------------------------------------------------------ #
    def _map(self, nx: float, ny: float) -> np.ndarray:
        """Normalized image (x right, y down) → game position, with smoothing."""
        c = self._cfg
        gx = clamp(c.mp_x_sign * (0.5 - nx) * c.mp_x_span, -_X_LIMIT, _X_LIMIT)
        gy = clamp(c.mp_y_base + (0.5 - ny) * c.mp_y_span, _Y_MIN, _Y_MAX)
        pos = vec3(float(gx), float(gy), float(c.mp_z_plane))
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

    def _loop(self) -> None:
        try:
            import cv2
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
        except ImportError as exc:  # pragma: no cover - only when dep missing
            raise RuntimeError(
                "MediaPipe position tracking needs 'mediapipe' and 'opencv' "
                "installed: pip install mediapipe opencv-contrib-python"
            ) from exc

        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=_ensure_model()),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        landmarker = vision.HandLandmarker.create_from_options(options)
        cap = cv2.VideoCapture(self._cfg.mp_camera_index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
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
                ts_ms = max(last_ts + 1, t // 1000)  # must strictly increase
                last_ts = ts_ms
                res = landmarker.detect_for_video(image, ts_ms)
                if res.hand_landmarks:
                    wrist = res.hand_landmarks[0][0]  # landmark 0 = wrist
                    score = 0.9
                    if res.handedness:
                        score = float(res.handedness[0][0].score)
                    pos = self._map(wrist.x, wrist.y)
                    self._detections += 1
                    self._publish(TagObservation(
                        capture_time_us=t,
                        processed_time_us=t,
                        tag_ids=[0],
                        position_camera_m=pos,
                        orientation_camera_quat=quat_identity(),  # unused (IMU owns it)
                        reprojection_error_px=0.0,
                        decision_margin=60.0,
                        confidence=clamp(score, 0.0, 1.0),
                        valid=True,
                    ))
                else:
                    # hand not visible: an invalid obs so tracking decays to LOST
                    self._publish(TagObservation(
                        capture_time_us=t,
                        processed_time_us=t,
                        tag_ids=[],
                        position_camera_m=vec3(),
                        orientation_camera_quat=quat_identity(),
                        reprojection_error_px=99.0,
                        decision_margin=0.0,
                        confidence=0.0,
                        valid=False,
                    ))
        finally:
            landmarker.close()
            cap.release()

    def _update_fps(self, t: int) -> None:
        elapsed = (t - self._fps_mark_us) / 1e6
        if elapsed >= 0.5:
            self._camera_fps = self._frames / elapsed
            self._tag_fps = self._detections / elapsed
            self._frames = 0
            self._detections = 0
            self._fps_mark_us = t
