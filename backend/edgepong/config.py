"""Typed configuration loading.

Loads ``config/default.yaml`` (or a path from ``EDGEPONG_CONFIG``), applies a
handful of environment-variable overrides for ports/hosts, and exposes typed
dataclasses so the rest of the code never indexes raw dicts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

import yaml


# --------------------------------------------------------------------------- #
# Dataclasses mirroring config/default.yaml
# --------------------------------------------------------------------------- #
@dataclass
class SystemConfig:
    mode: str = "arcade"
    log_level: str = "INFO"
    metrics_enabled: bool = True
    # "sim" runs everything with mocks; "hardware" wires the real camera/paddle.
    hardware_mode: str = "sim"


@dataclass
class CameraConfig:
    device: str = "/dev/video0"
    width: int = 1280
    height: int = 720
    fps: int = 60
    pixel_format: str = "MJPG"
    exposure_mode: str = "manual"
    exposure_value: int = -6
    # sim knobs — calm defaults; raise to stress-test tracking robustness
    sim_jitter_m: float = 0.0015
    sim_latency_ms: float = 20.0
    sim_dropout_prob: float = 0.004


@dataclass
class ApriltagConfig:
    family: str = "tagStandard41h12"
    tag_size_m: float = 0.115
    quad_decimate: float = 1.5
    max_hamming: int = 1


@dataclass
class PaddleConfig:
    telemetry_port: int = 46000
    command_host: str = "127.0.0.1"
    command_port: int = 46001
    discovery_port: int = 46002
    stale_ms: int = 100
    disconnected_ms: int = 1000
    max_prediction_ms: int = 120
    # sim knobs
    sim_enabled: bool = True
    sim_autoplay: bool = True  # mock paddle intercepts balls so hits happen
    sim_packet_rate_hz: int = 100


@dataclass
class FusionConfig:
    camera_position_alpha: float = 0.65
    camera_orientation_alpha: float = 0.25
    imu_orientation_weight: float = 0.80
    max_angular_prediction_ms: float = 45.0
    output_rate_hz: int = 240
    # No camera wired? Let the paddle's IMU drive orientation directly and hold
    # a steady "tracked" state, so real paddle rotation shows up on screen.
    imu_only: bool = False


@dataclass
class GameConfig:
    physics_rate_hz: int = 240
    round_duration_s: int = 75
    countdown_s: int = 3
    paddle_width_m: float = 0.19
    paddle_height_m: float = 0.19
    paddle_thickness_m: float = 0.015
    collision_scale_easy: float = 1.55
    collision_scale_normal: float = 1.18
    collision_scale_hard: float = 1.05
    hit_grace_ms: int = 42
    min_swing_speed_rad_s: float = 0.8
    max_ball_count: int = 3
    difficulty: str = "EASY"
    # world layout (metres)
    wall_z_m: float = 0.0
    player_z_m: float = 2.0
    paddle_plane_z_m: float = 1.8
    # table geometry — must match the frontend arena (scene/arena.ts)
    table_width_m: float = 1.5
    table_length_m: float = 1.7
    table_z0_m: float = 0.1
    table_height_m: float = 0.76
    net_height_m: float = 0.1525
    table_restitution: float = 0.85


@dataclass
class HapticsConfig:
    # 0.30 -> ~21% duty floor, just above measured coin-ERM stiction (~18-20%)
    min_intensity: float = 0.30
    max_intensity: float = 1.0
    min_duration_ms: int = 22
    max_duration_ms: int = 65
    perfect_multiplier: float = 1.15


@dataclass
class RendererConfig:
    http_port: int = 8080
    websocket_port: int = 8081  # served on the same FastAPI app path in this MVP
    metrics_port: int = 8082
    state_rate_hz: int = 60
    projector_latency_compensation_ms: float = 25.0


@dataclass
class Config:
    system: SystemConfig = field(default_factory=SystemConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    apriltag: ApriltagConfig = field(default_factory=ApriltagConfig)
    paddle: PaddleConfig = field(default_factory=PaddleConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    game: GameConfig = field(default_factory=GameConfig)
    haptics: HapticsConfig = field(default_factory=HapticsConfig)
    renderer: RendererConfig = field(default_factory=RendererConfig)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _from_dict(cls: type, data: dict[str, Any]) -> Any:
    """Build a (possibly nested) dataclass from a dict, ignoring unknown keys."""
    if not is_dataclass(cls):
        return data
    kwargs: dict[str, Any] = {}
    # ``from __future__ import annotations`` stores field types as strings, so
    # resolve them to real classes before checking for nested dataclasses.
    hints = get_type_hints(cls)
    valid = {f.name: f for f in fields(cls)}
    for key, value in (data or {}).items():
        if key not in valid:
            continue
        ftype = hints.get(key, valid[key].type)
        if is_dataclass(ftype) and isinstance(value, dict):
            kwargs[key] = _from_dict(ftype, value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


def _default_config_path() -> Path:
    env = os.environ.get("EDGEPONG_CONFIG")
    if env:
        return Path(env)
    # repo_root/config/default.yaml relative to this file (backend/edgepong/config.py)
    return Path(__file__).resolve().parents[2] / "config" / "default.yaml"


def _apply_env_overrides(cfg: Config) -> Config:
    env = os.environ

    def as_int(name: str, current: int) -> int:
        return int(env[name]) if name in env else current

    def as_str(name: str, current: str) -> str:
        return env[name] if name in env else current

    def as_bool(name: str, current: bool) -> bool:
        if name not in env:
            return current
        return env[name].strip().lower() in ("1", "true", "yes", "on")

    # Set EDGEPONG_SIM_PADDLE=0 to silence the mock telemetry generator when a
    # real ESP32 paddle is streaming to the same port (avoids sequence-number
    # contention). The mock camera / sim ground truth still drive gameplay.
    cfg.paddle.sim_enabled = as_bool("EDGEPONG_SIM_PADDLE", cfg.paddle.sim_enabled)
    cfg.paddle.telemetry_port = as_int("EDGEPONG_TELEMETRY_PORT", cfg.paddle.telemetry_port)
    cfg.paddle.command_port = as_int("EDGEPONG_COMMAND_PORT", cfg.paddle.command_port)
    cfg.paddle.command_host = as_str("EDGEPONG_COMMAND_HOST", cfg.paddle.command_host)
    cfg.renderer.http_port = as_int("EDGEPONG_HTTP_PORT", cfg.renderer.http_port)
    cfg.system.hardware_mode = as_str("EDGEPONG_HARDWARE_MODE", cfg.system.hardware_mode)
    cfg.system.log_level = as_str("EDGEPONG_LOG_LEVEL", cfg.system.log_level)
    # With no camera (hardware mode) the paddle's IMU is the only pose source, so
    # let it drive orientation directly. Override explicitly if ever needed.
    cfg.fusion.imu_only = as_bool(
        "EDGEPONG_IMU_ONLY", cfg.system.hardware_mode == "hardware"
    )
    return cfg


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else _default_config_path()
    data: dict[str, Any] = {}
    if p.exists():
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    cfg = _from_dict(Config, data)
    return _apply_env_overrides(cfg)
