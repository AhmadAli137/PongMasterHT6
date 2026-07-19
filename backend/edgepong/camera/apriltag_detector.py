"""AprilTag detector interface + a mock implementation (spec §11).

The interface publishes a :class:`TagObservation` per processed frame. The mock
derives observations from the simulation ground truth (with camera latency,
jitter and random dropout). A real implementation would wrap OpenCV capture +
``pupil_apriltags`` and populate the same structure — the rest of the pipeline
does not care which one is running.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..clock import now_us
from ..config import CameraConfig
from ..mathutil import Quat, Vec3, quat_normalize, vec3
from ..sim.paddle_model import SimPaddleModel


@dataclass
class TagObservation:
    capture_time_us: int
    processed_time_us: int
    tag_ids: list[int]
    position_camera_m: Vec3
    orientation_camera_quat: Quat
    reprojection_error_px: float
    decision_margin: float
    confidence: float
    valid: bool = True


class TagDetector(Protocol):
    def poll(self) -> TagObservation | None:
        """Return the newest observation, or None if no new frame/tag."""
        ...

    def start(self) -> None: ...
    def stop(self) -> None: ...


class MockTagDetector:
    """Produces observations from the sim ground truth at the camera FPS."""

    def __init__(self, cfg: CameraConfig, model: SimPaddleModel, seed: int = 1234):
        self._cfg = cfg
        self._model = model
        self._rng = random.Random(seed)
        self._np_rng = np.random.default_rng(seed)
        self._period_us = int(1_000_000 / max(1, cfg.fps))
        self._next_capture_us = now_us()
        # rolling fps counters (read by the metrics aggregator)
        self._frames = 0
        self._detections = 0
        self._fps_mark_us = now_us()
        self._camera_fps = 0.0
        self._tag_fps = 0.0

    def start(self) -> None:  # nothing to open in sim
        self._next_capture_us = now_us()
        self._fps_mark_us = now_us()

    def stop(self) -> None:
        pass

    def poll(self) -> TagObservation | None:
        t = now_us()
        if t < self._next_capture_us:
            return None
        self._next_capture_us += self._period_us
        self._frames += 1
        self._update_fps(t)
        capture_us = t - int(self._cfg.sim_latency_ms * 1000)

        # random tag dropout (occlusion / motion blur)
        if self._rng.random() < self._cfg.sim_dropout_prob:
            return TagObservation(
                capture_time_us=capture_us,
                processed_time_us=t,
                tag_ids=[],
                position_camera_m=vec3(),
                orientation_camera_quat=quat_normalize(np.array([1.0, 0, 0, 0])),
                reprojection_error_px=99.0,
                decision_margin=0.0,
                confidence=0.0,
                valid=False,
            )

        gt = self._model.snapshot()
        jitter = self._np_rng.normal(0.0, self._cfg.sim_jitter_m, size=3)
        pos = gt.position + jitter
        # tiny orientation jitter
        oj = self._np_rng.normal(0.0, 0.01, size=4)
        orient = quat_normalize(gt.orientation + oj)

        self._detections += 1
        n_tags = self._rng.choice([1, 2, 3, 3])  # bundle usually gives several
        margin = 40.0 + self._rng.random() * 40.0
        reproj = 0.4 + self._rng.random() * 0.8
        conf = _confidence(margin, n_tags, reproj)

        return TagObservation(
            capture_time_us=capture_us,
            processed_time_us=t,
            tag_ids=list(range(n_tags)),
            position_camera_m=pos,
            orientation_camera_quat=orient,
            reprojection_error_px=reproj,
            decision_margin=margin,
            confidence=conf,
            valid=True,
        )


    def _update_fps(self, t: int) -> None:
        elapsed = (t - self._fps_mark_us) / 1e6
        if elapsed >= 0.5:
            self._camera_fps = self._frames / elapsed
            self._tag_fps = self._detections / elapsed
            self._frames = 0
            self._detections = 0
            self._fps_mark_us = t

    def camera_fps(self) -> float:
        return self._camera_fps

    def tag_fps(self) -> float:
        return self._tag_fps


def _confidence(decision_margin: float, n_tags: int, reproj_px: float) -> float:
    """Blend detector-quality signals into a 0..1 confidence (spec §11.2)."""
    margin_term = min(1.0, decision_margin / 60.0)
    tag_term = min(1.0, n_tags / 3.0)
    reproj_term = max(0.0, 1.0 - reproj_px / 3.0)
    conf = 0.5 * margin_term + 0.2 * tag_term + 0.3 * reproj_term
    return float(max(0.0, min(1.0, conf)))
