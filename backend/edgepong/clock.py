"""Monotonic time helpers.

The whole simulation is driven by a monotonic microsecond clock so that
timestamps never move backwards (e.g. across NTP steps) and every service
agrees on a single time base. Never use wall-clock time for game timing.
"""

from __future__ import annotations

import time


def now_us() -> int:
    """Monotonic timestamp in microseconds."""
    return time.monotonic_ns() // 1_000


def now_ms() -> float:
    """Monotonic timestamp in milliseconds (float)."""
    return time.monotonic_ns() / 1_000_000.0


def us_to_ms(us: int | float) -> float:
    return us / 1000.0


def ms_to_us(ms: int | float) -> int:
    return int(ms * 1000.0)
