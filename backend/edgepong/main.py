"""Edge Pong backend entry point.

Wires the services together and starts the web server. In ``sim`` mode (default)
it spins up the mock camera + mock paddle so the full pipeline runs with no
hardware; in ``hardware`` mode those are swapped for the real camera/ESP32
(left as integration points).

Run:  python -m edgepong.main   (or the ``edgepong`` console script)
"""

from __future__ import annotations

import itertools
import signal
import sys

import uvicorn

from .camera.apriltag_detector import MockTagDetector
from .config import Config, load_config
from .fusion.filter import PoseFusion
from .game.service import GameService, HealthProvider
from .logging_setup import get_logger, setup_logging
from .metrics.health import MetricsAggregator
from .paddle.haptics import HapticDispatcher
from .paddle.mock_paddle import MockPaddle
from .paddle.udp_gateway import PaddleGateway
from .sim.paddle_model import SimPaddleModel
from .types import ImpactEvent
from .web.server import WebServer

log = get_logger("main")


class Application:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.gateway = PaddleGateway(cfg.paddle)
        self.fusion = PoseFusion(cfg.fusion, cfg.paddle)
        self._haptic_seq = itertools.count(1)

        self.sim_model: SimPaddleModel | None = None
        self.mock_paddle: MockPaddle | None = None
        self.detector: MockTagDetector | None = None

        if cfg.system.hardware_mode == "sim":
            self.sim_model = SimPaddleModel(cfg.game, cfg.paddle)
            self.detector = MockTagDetector(cfg.camera, self.sim_model)
            if cfg.paddle.sim_enabled:
                self.mock_paddle = MockPaddle(cfg.paddle, self.sim_model, cfg.paddle.telemetry_port)

        # haptic dispatcher sends commands out over the gateway's UDP socket
        self.haptics = HapticDispatcher(cfg.haptics, self.gateway.send_command)

        self.game = GameService(
            cfg=cfg,
            fusion=self.fusion,
            health=HealthProvider(
                paddle_connected=lambda: self.gateway.update_link_state().connected,
                camera_ok=lambda: True,
            ),
            on_impact=self._on_impact,
            camera_poll=self.detector.poll if self.detector else None,
            imu_latest=self.gateway.latest,
            sim_model=self.sim_model,
            on_balance_edge=self.haptics.edge_tick,
            on_rally=lambda event: self.web.push_event(event),
        )

        self.metrics = MetricsAggregator(
            self.gateway,
            self.game,
            camera_fps=self.detector.camera_fps if self.detector else None,
            tag_fps=self.detector.tag_fps if self.detector else None,
        )
        self.web = WebServer(cfg, self.game, self.metrics.collect)

    def _on_impact(self, impact: ImpactEvent) -> None:
        # authoritative haptic path (never routed through the browser)
        self.haptics.on_impact(impact)
        # mirror to the renderer for audio/particles
        self.web.push_impact(impact)

    def start_services(self) -> None:
        self.gateway.start()
        if self.detector:
            self.detector.start()
        if self.mock_paddle:
            self.mock_paddle.start()
        self.game.start()
        log.info("edge pong services started (mode=%s)", self.cfg.system.hardware_mode)

    def stop_services(self) -> None:
        self.game.stop()
        if self.mock_paddle:
            self.mock_paddle.stop()
        if self.detector:
            self.detector.stop()
        self.gateway.stop()
        log.info("edge pong services stopped")


def build_app(cfg: Config | None = None) -> Application:
    cfg = cfg or load_config()
    setup_logging(cfg.system.log_level)
    app = Application(cfg)
    return app


def run() -> None:
    cfg = load_config()
    setup_logging(cfg.system.log_level)
    application = build_app(cfg)
    application.start_services()

    def _shutdown(*_a) -> None:
        application.stop_services()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)
    except ValueError:
        pass  # not in main thread (e.g. tests)

    # FastAPI startup hook wires the broadcast loop; uvicorn owns the event loop.
    uvicorn.run(
        application.web.app,
        host="0.0.0.0",
        port=cfg.renderer.http_port,
        log_level=cfg.system.log_level.lower(),
    )
    application.stop_services()


if __name__ == "__main__":
    run()
