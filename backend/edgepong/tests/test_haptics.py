"""Four-quadrant haptic mapping tests (spec §17, §24.1)."""

from edgepong.config import HapticsConfig
from edgepong.mathutil import vec3
from edgepong.paddle.haptics import (
    HapticDispatcher,
    build_haptic_command,
    quadrant_weights,
)
from edgepong.types import BallType, HitQuality, ImpactEvent


def test_weights_sum_to_one_center():
    w = quadrant_weights(0.0, 0.0)
    total = w.q0 + w.q1 + w.q2 + w.q3
    assert abs(total - 1.0) < 1e-9
    assert abs(w.q0 - 0.25) < 1e-9


def test_top_right_corner_dominant():
    # local x=+1 (right), y=+1 (top) -> q1 (top-right)
    w = quadrant_weights(1.0, 1.0)
    assert w.q1 == max(w.q0, w.q1, w.q2, w.q3)
    assert w.q1 > 0.99


def test_bottom_left_corner_dominant():
    w = quadrant_weights(-1.0, -1.0)
    assert w.q2 == max(w.q0, w.q1, w.q2, w.q3)


def _impact(lx: float, ly: float, strength: float, quality=HitQuality.GOOD) -> ImpactEvent:
    return ImpactEvent(
        id=1, ball_id=1, timestamp_us=0, position_m=vec3(),
        paddle_local_x=lx, paddle_local_y=ly, strength=strength,
        quality=quality, outgoing_velocity_mps=vec3(), score_delta=100,
        ball_type=BallType.NORMAL,
    )


def test_intensity_clamped_within_bounds():
    cfg = HapticsConfig()
    cmd = build_haptic_command(_impact(1.0, 1.0, 1.0), cfg, sequence=1)
    top = max(cmd.q0, cmd.q1, cmd.q2, cmd.q3) / 255.0
    assert top <= cfg.max_intensity + 1e-6


def test_dispatcher_emits_one_command_per_impact():
    sent: list[bytes] = []
    disp = HapticDispatcher(HapticsConfig(), sent.append)
    disp.on_impact(_impact(0.0, 0.0, 0.8))
    assert len(sent) == 1


def test_avoid_ball_emits_error_tap():
    sent: list[bytes] = []
    disp = HapticDispatcher(HapticsConfig(), sent.append)
    imp = _impact(0.0, 0.0, 0.8)
    imp.ball_type = BallType.AVOID
    cmd = disp.on_impact(imp)
    from edgepong.paddle.packets import HAPTIC_FLAG_ERROR
    assert cmd.flags & HAPTIC_FLAG_ERROR
