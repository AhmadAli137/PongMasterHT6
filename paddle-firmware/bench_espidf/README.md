# Haptic driver bench test (ESP-IDF, ESP32-C5)

Standalone firmware to calibrate the four MOSFET quadrant drivers and ERM
motors before wiring the real paddle firmware. Runs a repeating six-phase
sequence with serial-log prompts.

## What it measures

| Phase | What you record |
|---|---|
| 1 Stiction sweep up | Duty % where the motor **starts** spinning (typ. 25–35 %) |
| 2 Drop-out sweep down | Duty % where it **stops** (lower — rotor inertia) |
| 3 Impact pulses | Do 40 ms hits feel like distinct taps? |
| 4 Overdrive kick A/B | B (15 ms 100 % kick) should feel much crisper than A |
| 5 Quadrant round-robin | Each corner buzzes alone, correct position |
| 6 All-quadrant burst | Worst-case current draw — **brownout detector** |

If the board reboots during phase 6, the next boot prints a loud warning
(reset reason = brownout): your bulk capacitor is too small, battery wires
too thin, or the cell's protection current limit is too low.

Phase 1/2 results feed straight into the game config:
`haptics.min_intensity ≈ start_duty% / 70` in `config/default.yaml`.

## Wiring (per quadrant)

```
GPIO ──150Ω── gate   (10kΩ gate→GND)
source → GND (common with battery −)
drain  → motor(−);  motor(+) → VBAT
1N5819 across motor pair, stripe → VBAT
470–1000 µF electrolytic across VBAT/GND near the drivers
```

Default GPIOs: **4, 5, 23, 24** (Q0 TL, Q1 TR, Q2 BL, Q3 BR) — all clear of
the ESP32-C5 reserved functions (strapping 2/7/25/27/28, SPI flash 16-22,
USB-JTAG 13/14, console 11/12, IMU 1/3). To use others, edit `QUAD_GPIO[]`
in `main/bench_main.c` and stay off those reserved pins.

Testing a single driver cell first? Set `SINGLE_QUADRANT_ONLY 1`.

## Wi-Fi keep-alive (for the DAOKI)

The bench connects to Wi-Fi at startup with modem-sleep **off** — not to send
data, but to hold the ESP32's current draw high enough that a power-bank boost
board (the DAOKI) doesn't decide it's unplugged and auto-shut-off during the
idle gaps between phases. Copy your credentials in:

```
cp main/secrets.example.h main/secrets.h   # then edit SSID + password
```

If you're bench-testing on **USB power** (no battery/boost), you don't need
this — set `KEEP_ALIVE_WIFI 0` at the top of `main/bench_main.c` and skip
`secrets.h`. Either way the bench runs; without Wi-Fi you just lose the
keep-alive.

## Build & flash

Requires **ESP-IDF v5.5 or newer** (first release with ESP32-C5 support).

```
cd paddle-firmware/bench_espidf
idf.py set-target esp32c5
idf.py build flash monitor
```

Safety notes baked in: duty is clamped to 70 % (3 V motors on a 4.2 V full
cell), the 100 % overdrive kick is limited to 15 ms, and all channels boot
at duty 0 so motors stay off during reset (spec §29).
