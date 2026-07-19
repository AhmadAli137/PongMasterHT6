# Edge Pong paddle firmware (ESP-IDF, ESP32-C5)

Milestone 1: Wi-Fi + UDP link + haptics. The paddle joins your network,
streams telemetry the backend already understands, and buzzes its motors on
real game impacts.

- Wi-Fi STA connect (pattern borrowed from SketchBot's `network_hal.cpp`),
  with `WIFI_PS_NONE` — required both for <20 ms haptic latency and to keep
  power-bank boost boards (DAOKI) from auto-shutting off
- 50 Hz telemetry (identity quaternion until the IMU lands in milestone 2) —
  byte-identical to `backend/edgepong/paddle/packets.py`, CRC32 and all
- Haptic command receive on UDP :46001 → LEDC on GPIO 4/5/23/24, duty clamped to
  70 %, pulses clamped to 250 ms, **all motors off on Wi-Fi loss**
- Motors are pinned OFF before anything else initialises (spec §29)

## Setup

```
cd paddle-firmware/paddle_espidf
cp main/secrets.example.h main/secrets.h    # then edit: SSID, password, PC's IP
idf.py set-target esp32c5
idf.py build flash monitor
```

Needs ESP-IDF v5.5+. Board: Waveshare ESP32-C5-WiFi6-Kit-N16R4 — see
`sdkconfig.defaults` for two board-specific gotchas (PSRAM erratum, stack).

## End-to-end test with the game

1. Flash and note the paddle's IP from the monitor (`got ip 192.168.x.y`).
2. On the PC, run the backend with haptics aimed at the paddle:
   `EDGEPONG_COMMAND_HOST=192.168.x.y python -m edgepong.main`
3. Open the game, play with the mouse. Every ball you hit should buzz the
   paddle — quadrant-weighted by where on the virtual blade you struck it.
4. `curl localhost:8080/api/metrics` — `imuPacketsPerSecond` ≈ 50 and
   `packetLossPercent` near 0 confirm the uplink.

Milestone 2 (next): real IMU over I²C (sample → quaternion → telemetry), the
RGB status LED, and the battery ADC.
