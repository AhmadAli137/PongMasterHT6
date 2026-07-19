"""Four-quadrant haptic mapping and UDP command dispatch (spec §17).

The mapping (bilinear quadrant weights + intensity/duration clamps) is pure and
unit tested. Actually sending the packet is delegated to a callable so the same
logic works against the real UDP sender or a capture list in tests.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable

from ..config import HapticsConfig
from ..mathutil import clamp
from ..types import HitQuality, ImpactEvent
from .packets import (
    HAPTIC_FLAG_ERROR,
    HAPTIC_FLAG_FLASH_RGB,
    HAPTIC_FLAG_PERFECT,
    WAVE_DOUBLE_TAP,
    WAVE_PULSE,
    HapticCommand,
)


@dataclass
class QuadrantIntensities:
    q0: float  # top-left
    q1: float  # top-right
    q2: float  # bottom-left
    q3: float  # bottom-right

    def as_bytes(self) -> tuple[int, int, int, int]:
        return (
            int(round(clamp(self.q0, 0.0, 1.0) * 255)),
            int(round(clamp(self.q1, 0.0, 1.0) * 255)),
            int(round(clamp(self.q2, 0.0, 1.0) * 255)),
            int(round(clamp(self.q3, 0.0, 1.0) * 255)),
        )


def quadrant_weights(local_x: float, local_y: float) -> QuadrantIntensities:
    """Bilinear split of a normalized contact point across four quadrants.

    Inputs are in paddle-local normalized coordinates in [-1, 1] where
    (-1,-1) is bottom-left and (+1,+1) is top-right. Converted to [0,1] with
    x=0 left / x=1 right and y=0 top / y=1 bottom to match spec §17.1.
    """
    x = clamp((local_x + 1.0) * 0.5, 0.0, 1.0)          # 0 left .. 1 right
    y = clamp((1.0 - (local_y + 1.0) * 0.5), 0.0, 1.0)  # 0 top  .. 1 bottom
    return QuadrantIntensities(
        q0=(1.0 - x) * (1.0 - y),  # top-left
        q1=x * (1.0 - y),          # top-right
        q2=(1.0 - x) * y,          # bottom-left
        q3=x * y,                  # bottom-right
    )


def build_haptic_command(
    impact: ImpactEvent,
    cfg: HapticsConfig,
    sequence: int,
    global_gain: float = 1.0,
) -> HapticCommand:
    """Turn an impact event into a clamped, safe haptic command."""
    weights = quadrant_weights(impact.paddle_local_x, impact.paddle_local_y)

    strength = clamp(impact.strength, 0.0, 1.0)
    if impact.quality is HitQuality.PERFECT:
        strength = clamp(strength * cfg.perfect_multiplier, 0.0, 1.0)

    span = cfg.max_intensity - cfg.min_intensity

    def scale(weight: float) -> float:
        raw = weight * strength * global_gain
        if raw <= 0.02:  # below-threshold neighbours stay silent (spec §17.1)
            return 0.0
        return cfg.min_intensity + span * clamp(raw, 0.0, 1.0)

    scaled = QuadrantIntensities(
        q0=scale(weights.q0), q1=scale(weights.q1),
        q2=scale(weights.q2), q3=scale(weights.q3),
    )
    q0, q1, q2, q3 = scaled.as_bytes()

    duration = int(
        cfg.min_duration_ms + (cfg.max_duration_ms - cfg.min_duration_ms) * strength
    )
    duration = int(clamp(duration, cfg.min_duration_ms, cfg.max_duration_ms))

    flags = HAPTIC_FLAG_FLASH_RGB
    waveform = WAVE_PULSE
    if impact.quality is HitQuality.PERFECT:
        flags |= HAPTIC_FLAG_PERFECT
        waveform = WAVE_DOUBLE_TAP

    return HapticCommand(
        command_sequence=sequence,
        q0=q0, q1=q1, q2=q2, q3=q3,
        duration_ms=duration,
        waveform=waveform,
        flags=flags,
    )


def build_error_command(sequence: int, cfg: HapticsConfig) -> HapticCommand:
    """A small, non-directional error tap (used for AVOID balls / misses)."""
    tap = int(round(cfg.min_intensity * 255))
    return HapticCommand(
        command_sequence=sequence,
        q0=tap, q1=tap, q2=tap, q3=tap,
        duration_ms=cfg.min_duration_ms,
        waveform=WAVE_PULSE,
        flags=HAPTIC_FLAG_ERROR,
    )


class HapticDispatcher:
    """Builds haptic commands from impacts and pushes them over a send callable.

    ``send`` receives the encoded packet bytes. Exactly one command is emitted
    per impact event; the sequence counter is monotonic for loss detection.
    """

    def __init__(self, cfg: HapticsConfig, send: Callable[[bytes], None]):
        self._cfg = cfg
        self._send = send
        self._seq = itertools.count(1)
        self.last_command: HapticCommand | None = None

    def on_impact(self, impact: ImpactEvent, global_gain: float = 1.0) -> HapticCommand:
        seq = next(self._seq)
        from ..types import BallType
        if impact.ball_type is BallType.AVOID:
            cmd = build_error_command(seq, self._cfg)
        else:
            cmd = build_haptic_command(impact, self._cfg, seq, global_gain)
        self.last_command = cmd
        self._send(cmd.encode())
        return cmd

    def edge_tick(self, local_x: float, local_y: float) -> HapticCommand:
        """Small directional tap when the balance ball rolls off an edge (spec §18.2)."""
        seq = next(self._seq)
        w = quadrant_weights(local_x, local_y)

        def val(weight: float) -> int:
            if weight <= 0.05:
                return 0
            return int(round(self._cfg.min_intensity * (0.6 + 0.8 * weight) * 255))

        cmd = HapticCommand(
            command_sequence=seq,
            q0=val(w.q0), q1=val(w.q1), q2=val(w.q2), q3=val(w.q3),
            duration_ms=self._cfg.min_duration_ms,
            waveform=WAVE_PULSE,
            flags=0,
        )
        self.last_command = cmd
        self._send(cmd.encode())
        return cmd
