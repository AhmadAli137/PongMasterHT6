"""Aggregate a single metrics dict from the running services (spec §23)."""

from __future__ import annotations

from typing import Callable


class MetricsAggregator:
    def __init__(
        self,
        gateway,
        game,
        camera_fps: Callable[[], float] | None = None,
        tag_fps: Callable[[], float] | None = None,
    ):
        self._gateway = gateway
        self._game = game
        self._camera_fps = camera_fps
        self._tag_fps = tag_fps

    def collect(self) -> dict:
        link = self._gateway.update_link_state()
        game_metrics = self._game.metrics()
        return {
            "gameState": self._game.state.value,
            "cameraFps": round(self._camera_fps(), 1) if self._camera_fps else None,
            "tagFps": round(self._tag_fps(), 1) if self._tag_fps else None,
            "imuPacketsReceived": link.packets_received,
            "packetLossPercent": round(link.loss_percent(), 2),
            "paddleConnected": link.connected,
            "paddleDegraded": link.degraded,
            "paddleAgeMs": round(self._gateway.paddle_age_ms(), 1),
            "physicsHz": game_metrics.get("physicsHz", 0.0),
            "lastImpactToCommandMs": game_metrics.get("lastImpactToCommandMs", 0.0),
        }
