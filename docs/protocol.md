# Wire protocol

Two links: **UDP** between paddle and UNO Q (binary), **WebSocket** between the
UNO Q backend and the renderer (JSON).

## UDP (paddle ↔ UNO Q)

All packets are little-endian, versioned, with a trailing CRC32 over every
preceding byte. The authoritative definitions live in
`backend/edgepong/paddle/packets.py`; the ESP32 mirror is
`paddle-firmware/include/packets.h` (byte-for-byte identical).

| Message | Type | Direction | Port |
|---|---|---|---|
| Telemetry | 1 | paddle → UNO Q | 46000 |
| Haptic command | 2 | UNO Q → paddle | 46001 |
| LED command | 3 | UNO Q → paddle | 46001 |
| Heartbeat | 4 | paddle → UNO Q | 46000 |

Common header: `uint16 magic (0x5047)`, `uint8 version (1)`, `uint8 message_type`,
`uint32 sequence`. Telemetry adds a `uint64 paddle_time_us`, quaternion (wxyz),
gyro (rad/s), accel (m/s²), `uint16 battery_mv`, `uint8 button_bits`,
`uint8 status_bits`. Haptic adds `uint64 execute_time_us`, four `uint8` quadrant
intensities, `uint16 duration_ms`, `uint8 waveform`, `uint8 flags`.

The receiver tolerates lost/duplicated/out-of-order packets: it keeps only the
newest by sequence number and counts gaps as loss (spec §6.2, §6.4).

### Connection states (spec §6.8)

- **Connected**: valid packet within `disconnected_ms` (1 s)
- **Degraded**: no packet for `stale_ms`..`disconnected_ms` (100 ms – 1 s)
- **Disconnected**: no packet for > `disconnected_ms`

On Wi-Fi loss the firmware turns **all motors off** immediately.

## WebSocket (UNO Q → renderer)

`ws://<host>/ws`. The backend pushes a `state` message at
`renderer.state_rate_hz` (60 Hz) and an `impact` message per hit.

```jsonc
// state
{ "type": "state", "serverTimeUs": 123, "gameState": "PLAYING",
  "countdown": 0, "remainingMs": 43000,
  "paddle": { "position": [x,y,z], "quaternion": [w,x,y,z],
              "confidence": 0.92, "trackingState": "GOOD" },
  "balls": [ { "id": 7, "position": [..], "velocity": [..],
               "radius": 0.045, "type": "NORMAL", "state": "APPROACHING" } ],
  "score": 1200, "combo": 8, "bestCombo": 12, "accuracy": 0.9 }

// impact
{ "type": "impact", "id": 77, "ballId": 7, "timeUs": 123, "position": [x,y,z],
  "localX": 0.2, "localY": -0.1, "strength": 0.83, "quality": "PERFECT",
  "ballType": "SMASH", "scoreDelta": 250 }
```

### Renderer → backend (control)

`{ "type": "command", "command": "START_SESSION" }` — also `CALIBRATE`,
`SET_DIFFICULTY` (+ `value`), `RESET`. Commands are validated against an
allow-list. The same commands are available over `POST /api/command`.
