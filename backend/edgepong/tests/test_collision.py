"""Swept collision + rebound tests (spec §24.1)."""

import numpy as np

from edgepong.game.collision import build_collider, reflect_velocity, sweep_ball
from edgepong.mathutil import quat_identity, vec3
from edgepong.types import PaddlePose, TrackingState


def _centered_pose() -> PaddlePose:
    return PaddlePose(
        timestamp_us=0,
        position_m=vec3(0.0, 0.0, 0.0),
        orientation=quat_identity(),  # face normal +Z
        linear_velocity_mps=vec3(),
        angular_velocity_rad_s=vec3(),
        confidence=1.0,
        tracking_state=TrackingState.GOOD,
    )


def test_swept_hit_through_plane():
    paddle = build_collider(_centered_pose(), 0.1, 0.1, 1.0)
    # ball travels along -Z through the paddle at the centre
    res = sweep_ball(vec3(0.0, 0.0, 0.2), vec3(0.0, 0.0, -0.2), 0.02, paddle)
    assert res.hit
    assert abs(res.local_x) < 0.05
    assert abs(res.local_y) < 0.05


def test_no_tunneling_fast_ball():
    paddle = build_collider(_centered_pose(), 0.1, 0.1, 1.0)
    # a very fast ball that starts in front and ends well behind the paddle
    res = sweep_ball(vec3(0.0, 0.05, 1.0), vec3(0.0, 0.05, -1.0), 0.02, paddle)
    assert res.hit  # swept test catches it even though it never overlaps at a step


def test_miss_outside_bounds():
    paddle = build_collider(_centered_pose(), 0.1, 0.1, 1.0)
    res = sweep_ball(vec3(0.5, 0.5, 0.2), vec3(0.5, 0.5, -0.2), 0.02, paddle)
    assert not res.hit


def test_local_coordinates_map_corners():
    paddle = build_collider(_centered_pose(), 0.1, 0.1, 1.0)
    res = sweep_ball(vec3(0.09, 0.09, 0.2), vec3(0.09, 0.09, -0.2), 0.02, paddle)
    assert res.hit
    assert res.local_x > 0.5
    assert res.local_y > 0.5


def test_reflect_reverses_normal_component():
    out = reflect_velocity(vec3(0.0, 0.0, -5.0), vec3(), vec3(0.0, 0.0, 1.0))
    assert out[2] > 0.0  # bounced back along +Z


def test_reflect_speed_clamped():
    out = reflect_velocity(
        vec3(0.0, 0.0, -5.0), vec3(0.0, 0.0, -100.0), vec3(0.0, 0.0, 1.0), max_speed=22.0
    )
    assert np.linalg.norm(out) <= 22.0 + 1e-6
