"""Difficulty helpers (spec §15.3).

Maps a difficulty label to a forgiving collision scale, and offers a very small
adaptive nudge based on the player's recent hit rate (stretch goal §2.2.5).
"""

from __future__ import annotations

from ..config import GameConfig


def collision_scale(cfg: GameConfig) -> float:
    d = cfg.difficulty.upper()
    if d == "EASY":
        return cfg.collision_scale_easy
    if d == "HARD":
        return cfg.collision_scale_hard
    return cfg.collision_scale_normal


def adapt(current: str, recent_hit_rate: float) -> str:
    """Gently move difficulty toward keeping the player at ~70-85% hit rate."""
    order = ["EASY", "NORMAL", "HARD"]
    idx = order.index(current.upper()) if current.upper() in order else 0
    if recent_hit_rate > 0.9 and idx < len(order) - 1:
        idx += 1
    elif recent_hit_rate < 0.5 and idx > 0:
        idx -= 1
    return order[idx]
