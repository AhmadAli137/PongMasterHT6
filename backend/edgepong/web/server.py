"""FastAPI app: static frontend, control API, WebSocket state stream, metrics.

The WebSocket pushes authoritative world state at ``renderer.state_rate_hz`` and
forwards impact events as they happen. Incoming commands are validated against a
small allow-list before touching the game service (spec §19.4).
"""

from __future__ import annotations

import asyncio
import collections
import json
from pathlib import Path
from typing import Any, Deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config
from ..logging_setup import get_logger
from ..types import ImpactEvent

log = get_logger("web")

_ALLOWED_COMMANDS = {"START_SESSION", "CALIBRATE", "SET_DIFFICULTY", "RESET", "BALANCE_MODE", "PAUSE"}


def _sanitize(value: object, default: float | None = None, limit: float = 4.0) -> float | None:
    """Coerce an untrusted WS number to a finite, clamped float (or default)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default
    f = float(value)
    if f != f or f in (float("inf"), float("-inf")):
        return default
    return max(-limit, min(limit, f))


def _impact_to_json(impact: ImpactEvent) -> dict[str, Any]:
    return {
        "type": "impact",
        "id": impact.id,
        "ballId": impact.ball_id,
        "timeUs": impact.timestamp_us,
        "position": [float(impact.position_m[0]), float(impact.position_m[1]), float(impact.position_m[2])],
        "localX": round(impact.paddle_local_x, 3),
        "localY": round(impact.paddle_local_y, 3),
        "strength": round(impact.strength, 3),
        "quality": impact.quality.value,
        "ballType": impact.ball_type.value,
        "scoreDelta": impact.score_delta,
    }


class WebServer:
    def __init__(self, cfg: Config, game, metrics_provider):
        self._cfg = cfg
        self._game = game
        self._metrics_provider = metrics_provider
        self.app = FastAPI(title="Edge Pong")
        self._clients: set[WebSocket] = set()
        self._impact_queue: Deque[dict[str, Any]] = collections.deque(maxlen=128)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._register()

    # -- called from the game thread ---------------------------------------- #
    def push_impact(self, impact: ImpactEvent) -> None:
        self.push_event(_impact_to_json(impact))

    def push_event(self, payload: dict[str, Any]) -> None:
        """Queue any typed event (impact, rally, …) for the WS broadcast loop."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._impact_queue.append, payload)

    # -- routes ------------------------------------------------------------- #
    def _register(self) -> None:
        app = self.app

        @app.on_event("startup")
        async def _startup() -> None:
            self._loop = asyncio.get_running_loop()
            asyncio.create_task(self._broadcast_loop())

        @app.get("/api/health")
        async def health() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "gameState": self._game.state.value,
                    "mode": self._cfg.system.hardware_mode,
                }
            )

        @app.get("/api/metrics")
        async def metrics() -> JSONResponse:
            return JSONResponse(self._metrics_provider())

        @app.get("/api/results")
        async def results() -> JSONResponse:
            return JSONResponse(self._game.results())

        @app.post("/api/command")
        async def command(body: dict[str, Any]) -> JSONResponse:
            cmd = str(body.get("command", "")).upper()
            if cmd not in _ALLOWED_COMMANDS:
                return JSONResponse({"error": "unknown command"}, status_code=400)
            self._game.command(cmd, body.get("value"))
            return JSONResponse({"ok": True})

        @app.websocket("/ws")
        async def ws(websocket: WebSocket) -> None:
            await websocket.accept()
            self._clients.add(websocket)
            log.info("ws client connected (%d total)", len(self._clients))
            try:
                while True:
                    # receive optional commands from the renderer
                    raw = await websocket.receive_text()
                    self._handle_ws_command(raw)
            except WebSocketDisconnect:
                pass
            except Exception as exc:  # noqa: BLE001
                log.debug("ws error: %s", exc)
            finally:
                self._clients.discard(websocket)
                log.info("ws client disconnected (%d total)", len(self._clients))

        # static frontend (built) — mounted last so /api and /ws win.
        dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
        if dist.exists():
            app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")
        else:
            @app.get("/")
            async def index() -> JSONResponse:
                return JSONResponse(
                    {
                        "message": "Edge Pong backend running. Build the frontend "
                        "(cd frontend && npm run build) or run the Vite dev server.",
                        "ws": "/ws",
                    }
                )

    def _handle_ws_command(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        mtype = msg.get("type")
        if mtype == "command":
            cmd = str(msg.get("command", "")).upper()
            if cmd in _ALLOWED_COMMANDS:
                self._game.command(cmd, msg.get("value"))
        elif mtype == "input":
            power = _sanitize(msg.get("power"))
            self._game.external_input(
                x=_sanitize(msg.get("x")),
                y=_sanitize(msg.get("y")),
                tilt_delta=_sanitize(msg.get("tilt"), default=0.0) or 0.0,
                yaw_delta=_sanitize(msg.get("yaw"), default=0.0) or 0.0,
                vx=_sanitize(msg.get("vx"), limit=20.0),
                vy=_sanitize(msg.get("vy"), limit=20.0),
                strike=bool(msg.get("strike", False)),
                power=None if power is None else max(0.0, min(1.0, power)),
                flip=bool(msg.get("flip", False)),
            )

    # -- broadcast ---------------------------------------------------------- #
    async def _broadcast_loop(self) -> None:
        period = 1.0 / max(1, self._cfg.renderer.state_rate_hz)
        while True:
            await asyncio.sleep(period)
            if not self._clients:
                # still drain impacts so the queue never grows unbounded
                self._impact_queue.clear()
                continue

            messages: list[str] = [json.dumps(self._game.snapshot())]
            while self._impact_queue:
                messages.append(json.dumps(self._impact_queue.popleft()))

            dead: list[WebSocket] = []
            for client in list(self._clients):
                try:
                    for m in messages:
                        await client.send_text(m)
                except Exception:  # noqa: BLE001
                    dead.append(client)
            for d in dead:
                self._clients.discard(d)
