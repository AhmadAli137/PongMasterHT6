"""UDP paddle gateway (spec §7.2 paddle_gateway).

Receives telemetry packets on a background thread, validates/decodes them,
keeps only the newest state (never queues old telemetry), tracks packet loss
via sequence numbers, and exposes a latched connection state. Also owns the
outbound command socket used by the haptic dispatcher.

Designed to tolerate lost / duplicated / out-of-order packets.
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from typing import Optional

from ..clock import now_us
from ..config import PaddleConfig
from ..logging_setup import get_logger
from .packets import PacketError, PaddleTelemetry, peek_message_type, MSG_TELEMETRY, MSG_HEARTBEAT

log = get_logger("paddle.udp")


@dataclass
class PaddleLink:
    connected: bool = False
    degraded: bool = False
    last_packet_us: int = 0
    last_sequence: int = -1
    packets_received: int = 0
    packets_lost: int = 0
    peer_addr: Optional[tuple[str, int]] = None

    def loss_percent(self) -> float:
        total = self.packets_received + self.packets_lost
        return 0.0 if total == 0 else 100.0 * self.packets_lost / total


class PaddleGateway:
    def __init__(self, cfg: PaddleConfig):
        self._cfg = cfg
        self._rx_sock: socket.socket | None = None
        self._tx_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._latest: PaddleTelemetry | None = None
        self.link = PaddleLink()

    # -- lifecycle ---------------------------------------------------------- #
    def start(self) -> None:
        self._rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._rx_sock.bind(("0.0.0.0", self._cfg.telemetry_port))
        self._rx_sock.settimeout(0.2)
        self._tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._running.set()
        self._thread = threading.Thread(target=self._rx_loop, name="paddle-rx", daemon=True)
        self._thread.start()
        log.info("paddle gateway listening on udp/%d", self._cfg.telemetry_port)

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=1.0)
        for s in (self._rx_sock, self._tx_sock):
            if s:
                s.close()

    # -- receive ------------------------------------------------------------ #
    def _rx_loop(self) -> None:
        assert self._rx_sock is not None
        while self._running.is_set():
            try:
                data, addr = self._rx_sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            self._handle(data, addr)

    def _handle(self, data: bytes, addr: tuple[str, int]) -> None:
        mtype = peek_message_type(data)
        if mtype == MSG_HEARTBEAT:
            with self._lock:
                self.link.last_packet_us = now_us()
                self.link.peer_addr = addr
            return
        if mtype != MSG_TELEMETRY:
            return
        try:
            tel = PaddleTelemetry.decode(data)
        except PacketError as exc:
            log.debug("dropping bad telemetry from %s: %s", addr, exc)
            return

        with self._lock:
            # out-of-order / duplicate: keep newest by sequence only
            if self._latest is not None and _seq_older_or_equal(tel.sequence, self.link.last_sequence):
                self.link.packets_received += 1
                return
            if self.link.last_sequence >= 0:
                gap = _seq_gap(self.link.last_sequence, tel.sequence)
                if gap > 1:
                    self.link.packets_lost += gap - 1
            self.link.last_sequence = tel.sequence
            self.link.last_packet_us = now_us()
            self.link.packets_received += 1
            self.link.peer_addr = addr
            self._latest = tel
            # auto-learn command host from telemetry source if configured loopback
            if self._cfg.command_host in ("0.0.0.0", "auto"):
                self._cfg.command_host = addr[0]

    def latest(self) -> PaddleTelemetry | None:
        with self._lock:
            return self._latest

    def update_link_state(self) -> PaddleLink:
        """Recompute connected/degraded flags from elapsed time. Call periodically."""
        with self._lock:
            age_ms = (now_us() - self.link.last_packet_us) / 1000.0 if self.link.last_packet_us else 1e9
            self.link.connected = age_ms < self._cfg.disconnected_ms
            self.link.degraded = self._cfg.stale_ms <= age_ms < self._cfg.disconnected_ms
            return self.link

    def paddle_age_ms(self) -> float:
        with self._lock:
            if not self.link.last_packet_us:
                return 1e9
            return (now_us() - self.link.last_packet_us) / 1000.0

    # -- send --------------------------------------------------------------- #
    def send_command(self, payload: bytes) -> None:
        if not self._tx_sock:
            return
        host = self._cfg.command_host
        port = self._cfg.command_port
        try:
            self._tx_sock.sendto(payload, (host, port))
        except OSError as exc:
            log.debug("command send failed to %s:%d: %s", host, port, exc)


def _seq_gap(prev: int, current: int) -> int:
    """Forward distance in 32-bit sequence space."""
    return (current - prev) & 0xFFFFFFFF


def _seq_older_or_equal(seq: int, last: int) -> bool:
    if last < 0:
        return False
    # within a half-window forward => newer; otherwise older/equal
    return _seq_gap(last, seq) == 0 or _seq_gap(last, seq) > 0x7FFFFFFF
