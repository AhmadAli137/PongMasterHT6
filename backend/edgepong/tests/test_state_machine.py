"""Game state machine transition tests (spec §21.1, §24.1)."""

from edgepong.game.state_machine import GameState, GameStateMachine, Health


def _advance_to_ready(sm: GameStateMachine) -> None:
    healthy = Health(paddle_connected=True, tag_visible=True, camera_ok=True)
    # enough ticks to also cover the 1.5 s hold-steady calibration window
    for _ in range(40):
        sm.tick(0.1, healthy)
        if sm.state is GameState.READY:
            return


def test_boot_progresses_to_wait_for_paddle():
    sm = GameStateMachine()
    sm.tick(0.1, Health())
    assert sm.state is GameState.HARDWARE_CHECK
    sm.tick(0.1, Health())
    assert sm.state is GameState.WAIT_FOR_PADDLE


def test_reaches_ready_when_healthy():
    sm = GameStateMachine()
    _advance_to_ready(sm)
    assert sm.state is GameState.READY


def test_start_session_runs_countdown_then_playing():
    sm = GameStateMachine(countdown_s=1)
    _advance_to_ready(sm)
    sm.request("START_SESSION")
    healthy = Health(paddle_connected=True, tag_visible=True, camera_ok=True)
    sm.tick(0.01, healthy)
    assert sm.state is GameState.COUNTDOWN
    for _ in range(200):
        sm.tick(0.01, healthy)
        if sm.state is GameState.PLAYING:
            break
    assert sm.state is GameState.PLAYING


def test_round_ends_in_results():
    sm = GameStateMachine(countdown_s=0, round_duration_s=1)
    _advance_to_ready(sm)
    sm.request("START_SESSION")
    healthy = Health(paddle_connected=True, tag_visible=True, camera_ok=True)
    for _ in range(500):
        sm.tick(0.02, healthy)
        if sm.state is GameState.RESULTS:
            break
    assert sm.state is GameState.RESULTS


def test_calibration_requires_steady_hold():
    sm = GameStateMachine(calibration_hold_s=1.0)
    healthy = Health(paddle_connected=True, tag_visible=True, camera_ok=True)
    # advance to CALIBRATION
    for _ in range(10):
        sm.tick(0.05, healthy)
        if sm.state is GameState.CALIBRATION:
            break
    assert sm.state is GameState.CALIBRATION

    # partial hold: progress grows but not complete
    for _ in range(10):
        sm.tick(0.05, healthy)  # 0.5 s of 1.0 s
    assert sm.state is GameState.CALIBRATION
    assert 0.3 < sm.calibration_progress < 0.9

    # tag dropout resets progress
    sm.tick(0.05, Health(paddle_connected=True, tag_visible=False, camera_ok=True))
    assert sm.calibration_progress == 0.0

    # full steady hold completes calibration
    for _ in range(25):
        sm.tick(0.05, healthy)
        if sm.state is GameState.READY:
            break
    assert sm.state is GameState.READY


def test_start_queued_during_calibration_fires_when_ready():
    sm = GameStateMachine(countdown_s=1, calibration_hold_s=0.3)
    healthy = Health(paddle_connected=True, tag_visible=True, camera_ok=True)
    for _ in range(10):
        sm.tick(0.05, healthy)
        if sm.state is GameState.CALIBRATION:
            break
    sm.request("START_SESSION")  # queued while calibrating
    for _ in range(30):
        sm.tick(0.05, healthy)
        if sm.state is GameState.COUNTDOWN:
            break
    assert sm.state is GameState.COUNTDOWN


def test_restart_from_results():
    """SPACE on the results screen must start a new round (was a dead end)."""
    sm = GameStateMachine(countdown_s=1, round_duration_s=1, calibration_hold_s=0.2)
    healthy = Health(paddle_connected=True, tag_visible=True, camera_ok=True)
    _advance_to_ready(sm)
    sm.request("START_SESSION")
    for _ in range(60):  # play the whole 1 s round out
        sm.tick(0.05, healthy)
        if sm.state is GameState.RESULTS:
            break
    assert sm.state is GameState.RESULTS

    sm.request("START_SESSION")
    for _ in range(10):
        sm.tick(0.05, healthy)
        if sm.state is GameState.COUNTDOWN:
            break
    assert sm.state is GameState.COUNTDOWN
    assert sm.round_remaining_ms == 1000  # fresh round timer


def test_pause_freezes_round_and_resumes_via_countdown():
    sm = GameStateMachine(countdown_s=1, round_duration_s=10, calibration_hold_s=0.2)
    healthy = Health(paddle_connected=True, tag_visible=True, camera_ok=True)
    _advance_to_ready(sm)
    sm.request("START_SESSION")
    for _ in range(40):
        sm.tick(0.05, healthy)
        if sm.state is GameState.PLAYING:
            break
    assert sm.state is GameState.PLAYING
    sm.tick(0.05, healthy)  # burn a little round time
    remaining = sm.round_remaining_ms
    assert remaining < 10_000

    sm.request("PAUSE")
    assert sm.state is GameState.PAUSED
    for _ in range(20):  # 1 s of wall time passes while paused
        sm.tick(0.05, healthy)
    assert sm.round_remaining_ms == remaining  # timer frozen

    sm.request("PAUSE")  # resume
    assert sm.state is GameState.COUNTDOWN
    assert sm.round_remaining_ms == remaining  # not reset by the countdown
    for _ in range(30):
        sm.tick(0.05, healthy)
        if sm.state is GameState.PLAYING:
            break
    assert sm.state is GameState.PLAYING
    assert sm.round_remaining_ms == remaining  # still intact at resume


def test_pause_ignored_outside_playing():
    sm = GameStateMachine(calibration_hold_s=0.2)
    _advance_to_ready(sm)
    sm.request("PAUSE")
    assert sm.state is GameState.READY


def test_paddle_disconnect_pauses_and_recovers():
    sm = GameStateMachine(countdown_s=0, round_duration_s=60)
    _advance_to_ready(sm)
    sm.request("START_SESSION")
    healthy = Health(paddle_connected=True, tag_visible=True, camera_ok=True)
    sm.tick(0.01, healthy)  # -> PLAYING
    for _ in range(5):
        sm.tick(0.01, healthy)
    assert sm.state is GameState.PLAYING
    sm.tick(0.01, Health(paddle_connected=False, tag_visible=True, camera_ok=True))
    assert sm.state is GameState.PADDLE_DISCONNECTED
    sm.tick(0.01, healthy)
    assert sm.state is GameState.READY
