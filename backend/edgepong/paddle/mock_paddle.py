"""Mock ESP32 paddle: streams telemetry over real UDP loopback (spec §30).

Reads ground-truth motion from :class:`SimPaddleModel`, adds light sensor noise
+ a gyro bias, and sends :class:`PaddleTelemetry` packets at the configured rate
to the gateway's telemetry port. This exercises the real encode/UDP/decode path
so nothing about transport is faked away.
"""

from __future__ import annotations

import socket
import threading
import time

import numpy as np

from ..clock import now_us
from ..config import PaddleConfig
from ..logging_setup import get_logger
from ..sim.paddle_model import SimPaddleModel
from .packets import PaddleTelemetry, STATUS_IMU_CALIBRATED, STATUS_WIFI_CONNECTED

log = get_logger("paddle.mock")


class MockPaddle:
    def __init__(self, cfg: PaddleConfig, model: SimPaddleModel, dest_port: int):
        self._cfg = cfg
        self._model = model
        self._dest = ("127.0.0.1", dest_port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._seq = 0
        self._gyro_bias = np.array([0.003, -0.002, 0.001])  # rad/s constant bias

    def start(self) -> None:
        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="mock-paddle", daemon=True)
        self._thread.start()
        log.info("mock paddle streaming telemetry to udp/%d", self._dest[1])

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._sock.close()

    def _loop(self) -> None:
        period = 1.0 / max(1, self._cfg.sim_packet_rate_hz)
        while self._running.is_set():
            gt = self._model.snapshot()
            self._seq = (self._seq + 1) & 0xFFFFFFFF

            q = gt.orientation
            gyro = gt.angular_velocity + self._gyro_bias
            # crude proper-acceleration model: gravity in world minus we ignore
            # linear accel double-derivative; good enough for the slice.
            accel = np.array([0.0, 9.81, 0.0])

            tel = PaddleTelemetry(
                sequence=self._seq,
                paddle_time_us=now_us(),
                quat_w=float(q[0]), quat_x=float(q[1]),
                quat_y=float(q[2]), quat_z=float(q[3]),
                gyro_x=float(gyro[0]), gyro_y=float(gyro[1]), gyro_z=float(gyro[2]),
                accel_x=float(accel[0]), accel_y=float(accel[1]), accel_z=float(accel[2]),
                battery_mv=3950,
                button_bits=0,
                status_bits=STATUS_IMU_CALIBRATED | STATUS_WIFI_CONNECTED,
            )
            try:
                self._sock.sendto(tel.encode(), self._dest)
            except OSError:
                pass
            time.sleep(period)
