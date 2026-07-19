"""Scoring, combo, and session statistics (spec §15).

Deterministic and side-effect free so it is easy to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..types import BallType, HitQuality

_BASE_SCORE = {
    HitQuality.LATE: 50,
    HitQuality.GOOD: 100,
    HitQuality.PERFECT: 250,
}
_TYPE_BONUS = {
    BallType.NORMAL: 0,
    BallType.SMASH: 150,
    BallType.BACKHAND: 100,
    BallType.AVOID: 0,
}


@dataclass
class SessionStats:
    score: int = 0
    combo: int = 0
    best_combo: int = 0
    hits: int = 0
    misses: int = 0
    perfects: int = 0
    avoid_penalties: int = 0
    strongest_swing: float = 0.0
    forehand_hits: int = 0
    backhand_hits: int = 0
    reaction_times_ms: list[float] = field(default_factory=list)

    def accuracy(self) -> float:
        total = self.hits + self.misses
        return 0.0 if total == 0 else self.hits / total

    def avg_reaction_ms(self) -> float:
        rts = self.reaction_times_ms
        return 0.0 if not rts else sum(rts) / len(rts)


class Scorer:
    def __init__(self) -> None:
        self.stats = SessionStats()

    def reset(self) -> None:
        self.stats = SessionStats()

    def score_hit(
        self,
        quality: HitQuality,
        ball_type: BallType,
        strength: float,
        reaction_ms: float | None = None,
        is_backhand: bool = False,
    ) -> int:
        s = self.stats
        if ball_type is BallType.AVOID:
            # hitting an AVOID ball is a penalty and breaks combo
            s.combo = 0
            s.avoid_penalties += 1
            delta = -150
            s.score = max(0, s.score + delta)
            return delta

        s.combo += 1
        s.best_combo = max(s.best_combo, s.combo)
        s.hits += 1
        if quality is HitQuality.PERFECT:
            s.perfects += 1
        if is_backhand:
            s.backhand_hits += 1
        else:
            s.forehand_hits += 1
        s.strongest_swing = max(s.strongest_swing, strength)
        if reaction_ms is not None:
            s.reaction_times_ms.append(reaction_ms)

        combo_mult = 1.0 + min(s.combo - 1, 9) * 0.1  # +10% per combo, capped x2
        base = _BASE_SCORE[quality] + _TYPE_BONUS.get(ball_type, 0)
        delta = int(base * combo_mult)
        s.score += delta
        return delta

    def register_miss(self) -> None:
        self.stats.combo = 0
        self.stats.misses += 1

    def register_fault(self) -> None:
        """Your return hit the net, flew out, or fell on your own half."""
        self.stats.combo = 0
        self.stats.misses += 1

    def register_landed(self) -> int:
        """Your return bounced on the opponent half: rally continues, points now."""
        s = self.stats
        delta = int(50 * (1.0 + min(s.combo, 10) * 0.1))
        s.score += delta
        return delta

    def register_winner(self) -> int:
        """The opponent failed to return your shot."""
        delta = 150
        self.stats.score += delta
        return delta
