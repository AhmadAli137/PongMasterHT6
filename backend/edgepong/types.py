"""Shared runtime types used across services (spec §7.3)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

import numpy as np

from .mathutil import Vec3, Quat, quat_identity, vec3


class TrackingState(str, enum.Enum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    LOST = "LOST"


class BallType(str, enum.Enum):
    NORMAL = "NORMAL"
    SMASH = "SMASH"
    BACKHAND = "BACKHAND"
    AVOID = "AVOID"


class BallLifecycle(str, enum.Enum):
    APPROACHING = "APPROACHING"  # travelling toward the player (opponent hit last)
    RETURNED = "RETURNED"        # travelling toward the opponent (player hit last)
    HIT = "HIT"                  # legacy terminal state (kept for compatibility)
    MISSED = "MISSED"
    EXPIRED = "EXPIRED"


class HitQuality(str, enum.Enum):
    LATE = "LATE"
    GOOD = "GOOD"
    PERFECT = "PERFECT"


@dataclass
class PaddlePose:
    timestamp_us: int
    position_m: Vec3 = field(default_factory=lambda: vec3())
    orientation: Quat = field(default_factory=quat_identity)
    linear_velocity_mps: Vec3 = field(default_factory=lambda: vec3())
    angular_velocity_rad_s: Vec3 = field(default_factory=lambda: vec3())
    confidence: float = 0.0
    source_flags: int = 0
    tracking_state: TrackingState = TrackingState.LOST

    def face_normal(self) -> Vec3:
        # Paddle face normal points along +Z of the paddle frame (spec §5.8).
        from .mathutil import quat_rotate
        return quat_rotate(self.orientation, vec3(0.0, 0.0, 1.0))


@dataclass
class Ball:
    id: int
    spawn_time_us: int
    position: Vec3
    velocity: Vec3
    radius: float
    type: BallType
    target_zone: Vec3
    state: BallLifecycle = BallLifecycle.APPROACHING
    prev_position: Vec3 | None = None
    # rally bookkeeping
    last_hit_by: str = "OPPONENT"          # "OPPONENT" | "PLAYER"
    bounces_player: int = 0                # tabletop bounces on the player half
    bounces_opponent: int = 0              # tabletop bounces on the opponent half
    opponent_will_return: bool | None = None  # decided when a return lands in
    linger_s: float = 0.0                  # dead-ball visual dribble time left (sim dt)
    # player-serve toss — cleared the moment the player strikes it
    is_toss: bool = False                  # ball is a hovering/falling serve toss
    freeze_s: float = 0.0                  # hover countdown, decremented by sim dt


@dataclass
class ImpactEvent:
    id: int
    ball_id: int
    timestamp_us: int
    position_m: Vec3
    paddle_local_x: float  # normalized -1..1
    paddle_local_y: float  # normalized -1..1
    strength: float        # 0..1
    quality: HitQuality
    outgoing_velocity_mps: Vec3
    score_delta: int
    ball_type: BallType
