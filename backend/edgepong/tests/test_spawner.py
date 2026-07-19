"""Spawner serve-preview tests (opponent telegraph)."""

from edgepong.config import GameConfig
from edgepong.game.spawner import Spawner


def test_serve_preview_matches_spawned_ball():
    sp = Spawner(GameConfig(), seed=42)
    balls = sp.update(0.01, active_ball_count=0)
    assert balls == []  # first spawn is scheduled at 1.0 s
    preview = sp.serve_preview()
    assert preview is not None
    assert preview["inMs"] > 0

    # run until the planned ball is released; it must launch from the preview spot
    spawned = []
    for _ in range(300):
        spawned = sp.update(0.01, active_ball_count=0)
        if spawned:
            break
    assert spawned, "ball never spawned"
    b = spawned[0]
    assert abs(float(b.position[0]) - preview["position"][0]) < 1e-9
    assert abs(float(b.position[1]) - preview["position"][1]) < 1e-9


def test_preview_replans_after_each_spawn():
    sp = Spawner(GameConfig(), seed=1)
    seen_positions = set()
    for _ in range(2000):
        spawned = sp.update(0.01, active_ball_count=0)
        if spawned:
            p = sp.serve_preview()
            assert p is not None  # next serve is planned immediately
            seen_positions.add(tuple(p["position"]))
        if len(seen_positions) >= 3:
            break
    assert len(seen_positions) >= 3


def test_preview_none_only_between_release_and_next_plan():
    sp = Spawner(GameConfig(), seed=1)
    sp.update(0.01, active_ball_count=0)
    assert sp.serve_preview() is not None
    sp.reset()
    assert sp.serve_preview() is None  # cleared until the next update plans one
