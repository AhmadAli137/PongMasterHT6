"""Headless end-to-end demo: run the full pipeline in sim mode with no browser.

Drives a short session and prints impacts + results. Handy for CI and for
verifying the pipeline after changes without a projector attached.

    python scripts/headless_demo.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgepong.config import load_config  # noqa: E402
from edgepong.main import build_app  # noqa: E402


def main() -> int:
    cfg = load_config()
    cfg.system.hardware_mode = "sim"
    cfg.game.countdown_s = 0
    cfg.game.round_duration_s = 8

    app = build_app(cfg)
    impacts: list = []
    original = app.game._on_impact

    def spy(imp):
        impacts.append(imp)
        original(imp)

    app.game._on_impact = spy

    app.start_services()
    time.sleep(1.0)
    app.game.command("CALIBRATE")
    time.sleep(0.4)
    app.game.command("START_SESSION")

    deadline = time.time() + 12
    last = None
    while time.time() < deadline:
        snap = app.game.snapshot()
        if snap["gameState"] != last:
            print(f"[{snap['gameState']}] score={snap['score']} combo={snap['combo']}")
            last = snap["gameState"]
        if snap["gameState"] == "RESULTS":
            break
        time.sleep(0.2)

    time.sleep(0.3)
    print(f"\nimpacts: {len(impacts)}")
    print("results:", app.game.results())
    print("metrics:", app.metrics.collect())
    app.stop_services()
    return 0 if impacts else 1


if __name__ == "__main__":
    raise SystemExit(main())
