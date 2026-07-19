"""Console game state machine (spec §21.1).

Pure transition logic with no I/O so it is trivially unit testable. The game
service calls :meth:`request` for external commands and :meth:`tick` each frame
with the current elapsed time / hardware health.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class GameState(str, enum.Enum):
    BOOT = "BOOT"
    HARDWARE_CHECK = "HARDWARE_CHECK"
    WAIT_FOR_PADDLE = "WAIT_FOR_PADDLE"
    WAIT_FOR_TAG = "WAIT_FOR_TAG"
    CALIBRATION = "CALIBRATION"
    READY = "READY"
    COUNTDOWN = "COUNTDOWN"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    RESULTS = "RESULTS"
    # error / recovery
    CAMERA_ERROR = "CAMERA_ERROR"
    PADDLE_DISCONNECTED = "PADDLE_DISCONNECTED"
    TRACKING_LOST = "TRACKING_LOST"
    SAFE_MODE = "SAFE_MODE"


@dataclass
class Health:
    paddle_connected: bool = False
    tag_visible: bool = False
    camera_ok: bool = True


class GameStateMachine:
    def __init__(
        self,
        countdown_s: int = 3,
        round_duration_s: int = 75,
        calibration_hold_s: float = 1.5,
    ):
        self.state = GameState.BOOT
        self._countdown_s = countdown_s
        self._round_s = round_duration_s
        self._hold_s = calibration_hold_s
        self.countdown_remaining_s: float = 0.0
        self.round_remaining_ms: int = round_duration_s * 1000
        self._pending_start = False
        self._calibrated = False
        self._calib_time = 0.0

    @property
    def calibration_progress(self) -> float:
        """0..1 progress toward the hold-steady calibration target (spec §10.3)."""
        if self._hold_s <= 0:
            return 1.0
        return min(1.0, self._calib_time / self._hold_s)

    # -- external commands -------------------------------------------------- #
    def request(self, command: str) -> None:
        cmd = command.upper()
        if cmd == "START_SESSION":
            # allowed during CALIBRATION too: queues the start for when READY
            if self.state in (GameState.READY, GameState.RESULTS, GameState.CALIBRATION):
                self._pending_start = True
        elif cmd == "CALIBRATE":
            if self.state in (GameState.WAIT_FOR_TAG, GameState.READY, GameState.RESULTS):
                self.state = GameState.CALIBRATION
                self._calib_time = 0.0
        elif cmd == "PAUSE":
            # toggle: freeze mid-round, resume through a short 1 s countdown
            # (round_remaining_ms deliberately untouched in both directions)
            if self.state is GameState.PLAYING:
                self.state = GameState.PAUSED
            elif self.state is GameState.PAUSED:
                self.countdown_remaining_s = 1.0
                self.state = GameState.COUNTDOWN
        elif cmd == "RESET":
            self._pending_start = False
            self.round_remaining_ms = self._round_s * 1000
            self.state = GameState.READY if self._calibrated else GameState.WAIT_FOR_PADDLE

    def mark_calibrated(self) -> None:
        self._calibrated = True
        if self.state is GameState.CALIBRATION:
            self.state = GameState.READY

    # -- per-frame tick ----------------------------------------------------- #
    def tick(self, dt: float, health: Health) -> None:
        s = self.state

        if s is GameState.BOOT:
            self.state = GameState.HARDWARE_CHECK
        elif s is GameState.HARDWARE_CHECK:
            self.state = GameState.WAIT_FOR_PADDLE
        elif s is GameState.WAIT_FOR_PADDLE:
            if health.paddle_connected:
                self.state = GameState.WAIT_FOR_TAG
        elif s is GameState.WAIT_FOR_TAG:
            if not health.paddle_connected:
                self.state = GameState.WAIT_FOR_PADDLE
            elif health.tag_visible:
                self.state = GameState.CALIBRATION
                self._calib_time = 0.0
        elif s is GameState.CALIBRATION:
            # hold-steady: require continuous good tracking for _hold_s (spec §10.3);
            # any dropout resets progress with clear feedback in the UI
            if health.paddle_connected and health.tag_visible:
                self._calib_time += dt
                if self._calib_time >= self._hold_s:
                    self.mark_calibrated()
            else:
                self._calib_time = 0.0
        elif s in (GameState.READY, GameState.RESULTS):
            # RESULTS must also honour a queued start (spec §21.1: RESULTS → READY);
            # without this, "press SPACE to play again" is a dead end
            if self._pending_start:
                self._pending_start = False
                self.countdown_remaining_s = float(self._countdown_s)
                self.round_remaining_ms = self._round_s * 1000
                self.state = GameState.COUNTDOWN
        elif s is GameState.COUNTDOWN:
            self.countdown_remaining_s -= dt
            if self.countdown_remaining_s <= 0.0:
                self.countdown_remaining_s = 0.0
                self.state = GameState.PLAYING
        elif s is GameState.PLAYING:
            self.round_remaining_ms -= int(dt * 1000)
            if self.round_remaining_ms <= 0:
                self.round_remaining_ms = 0
                self.state = GameState.RESULTS

        # ---- recovery overrides (do not fire from RESULTS) --------------- #
        if self.state in (GameState.PLAYING, GameState.COUNTDOWN):
            if not health.camera_ok:
                self.state = GameState.CAMERA_ERROR
            elif not health.paddle_connected:
                self.state = GameState.PADDLE_DISCONNECTED

        # auto-recover error states when hardware returns
        if self.state is GameState.PADDLE_DISCONNECTED and health.paddle_connected:
            self.state = GameState.READY
        elif self.state is GameState.CAMERA_ERROR and health.camera_ok:
            self.state = GameState.READY

    @property
    def is_playing(self) -> bool:
        return self.state is GameState.PLAYING
