"""Quaternion math + scoring/combo tests (spec §24.1)."""

import math

import numpy as np

from edgepong.mathutil import (
    quat_from_axis_angle,
    quat_mul,
    quat_normalize,
    quat_rotate,
    vec3,
)
from edgepong.game.scoring import Scorer
from edgepong.types import BallType, HitQuality


def test_quat_normalize_unit():
    q = quat_normalize(np.array([2.0, 0.0, 0.0, 0.0]))
    assert abs(np.linalg.norm(q) - 1.0) < 1e-9


def test_rotate_90_about_z():
    q = quat_from_axis_angle(vec3(0, 0, 1), math.pi / 2)
    r = quat_rotate(q, vec3(1, 0, 0))
    assert abs(r[0]) < 1e-9
    assert abs(r[1] - 1.0) < 1e-9


def test_quat_mul_identity():
    q = quat_from_axis_angle(vec3(0, 1, 0), 0.3)
    ident = np.array([1.0, 0, 0, 0])
    assert np.allclose(quat_mul(q, ident), q)


def test_combo_increases_score_multiplier():
    scorer = Scorer()
    d1 = scorer.score_hit(HitQuality.GOOD, BallType.NORMAL, 0.5)
    for _ in range(5):
        d_last = scorer.score_hit(HitQuality.GOOD, BallType.NORMAL, 0.5)
    assert d_last > d1  # combo multiplier grew
    assert scorer.stats.best_combo == 6


def test_miss_resets_combo():
    scorer = Scorer()
    scorer.score_hit(HitQuality.GOOD, BallType.NORMAL, 0.5)
    scorer.score_hit(HitQuality.GOOD, BallType.NORMAL, 0.5)
    assert scorer.stats.combo == 2
    scorer.register_miss()
    assert scorer.stats.combo == 0
    assert scorer.stats.misses == 1


def test_avoid_ball_penalizes():
    scorer = Scorer()
    scorer.score_hit(HitQuality.GOOD, BallType.NORMAL, 0.5)
    delta = scorer.score_hit(HitQuality.GOOD, BallType.AVOID, 0.5)
    assert delta < 0
    assert scorer.stats.combo == 0
