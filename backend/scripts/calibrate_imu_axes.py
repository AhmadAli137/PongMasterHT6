"""Figure out the paddle's IMU axis remap by measuring three real motions.

The MPU6050 can be glued on in any orientation, so a physical pitch/yaw/roll
may land on a different sensor axis (and sign) than the game expects. Rather
than guess, this walks you through one motion per game axis, watches the gyro,
and prints the exact ``EDGEPONG_IMU_AXES`` string to paste into the task.

Run it with the backend STOPPED (it needs the telemetry port to itself):

    python scripts/calibrate_imu_axes.py

Then do each motion as one smooth push when prompted.
"""

from __future__ import annotations

import os
import socket
import time

import numpy as np

from edgepong.paddle.packets import PacketError, PaddleTelemetry

PORT = int(os.environ.get("EDGEPONG_TELEMETRY_PORT", "46000"))
AXIS_NAMES = ("x", "y", "z")


def _capture(sock: socket.socket, seconds: float) -> np.ndarray:
    """Collect gyro (rad/s) samples for a few seconds."""
    out: list[list[float]] = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            data, _ = sock.recvfrom(128)
        except socket.timeout:
            continue
        try:
            tel = PaddleTelemetry.decode(data)
        except PacketError:
            continue
        out.append([tel.gyro_x, tel.gyro_y, tel.gyro_z])
    return np.asarray(out, dtype=np.float64)


def _dominant(samples: np.ndarray) -> tuple[int, float, float]:
    """Return (axis index, sign, peak magnitude) of the strongest rotation."""
    if len(samples) == 0:
        return -1, 1.0, 0.0
    peak_per_axis = np.max(np.abs(samples), axis=0)
    j = int(np.argmax(peak_per_axis))
    k = int(np.argmax(np.abs(samples[:, j])))  # the peak sample on that axis
    sign = 1.0 if samples[k, j] >= 0 else -1.0
    return j, sign, float(peak_per_axis[j])


def _measure(sock: socket.socket, label: str, how: str) -> tuple[int, float]:
    input(f"\n{label}: {how}\n  Press Enter, then do it as ONE smooth motion...")
    print("  recording ~3 s — go!")
    samples = _capture(sock, 3.0)
    j, sign, peak = _dominant(samples)
    if j < 0 or peak < 0.5:
        print("  ⚠ barely saw any rotation — is the paddle streaming? try again bigger.")
    else:
        print(f"  got it: sensor {AXIS_NAMES[j]} axis, peak {peak:.1f} rad/s")
    return j, sign


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", PORT))
    sock.settimeout(0.4)
    print(f"listening for paddle telemetry on udp/{PORT} (backend must be stopped)")
    print("waiting for packets — move the paddle a little...")
    if len(_capture(sock, 3.0)) == 0:
        print("no telemetry seen. Is the paddle powered and on Wi-Fi? Aborting.")
        return

    # one motion per game axis: game x=pitch, y=yaw, z=roll
    results = [
        _measure(sock, "PITCH", "tilt the top of the paddle AWAY from you (face up)"),
        _measure(sock, "YAW", "turn the handle to your LEFT (like shaking 'no')"),
        _measure(sock, "ROLL", "twist the face CLOCKWISE (like a doorknob)"),
    ]

    used = [j for j, _ in results if j >= 0]
    if len(set(used)) != 3:
        print("\n⚠ two motions landed on the same sensor axis — the readings were")
        print("  ambiguous. Re-run and make each motion bigger and more isolated.")
        return

    parts = []
    for j, sign in results:
        parts.append(("-" if sign < 0 else "") + AXIS_NAMES[j])
    spec = ",".join(parts)
    print("\n" + "=" * 56)
    print(f"  EDGEPONG_IMU_AXES = {spec}")
    print("=" * 56)
    print("Paste that value into the 'Backend: Run (real paddle IMU ...)' task")
    print("env, then relaunch. If one motion still looks reversed, flip just")
    print("that letter's sign (e.g. y -> -y).")


if __name__ == "__main__":
    main()
