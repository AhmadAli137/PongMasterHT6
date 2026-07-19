// Paddle firmware configuration.
#pragma once

// ---- Wi-Fi ----
#define EP_WIFI_SSID     "edge-pong"
#define EP_WIFI_PASSWORD "edgepong123"

// UNO Q game service. Set to the console's IP (or use discovery, spec §6.8).
#define EP_HOST_IP        "192.168.4.1"
#define EP_TELEMETRY_PORT 46000  // paddle -> UNO Q
#define EP_COMMAND_PORT   46001  // UNO Q  -> paddle

// ---- Rates ----
#define EP_IMU_SAMPLE_HZ  200
#define EP_TELEMETRY_HZ   100
#define EP_HEARTBEAT_MS   500

// ---- Haptic safety limits (spec §5.6, §17.3) ----
#define EP_HAPTIC_PWM_FREQ_HZ     18000
#define EP_HAPTIC_MAX_DUTY        0.88f   // never exceed
#define EP_HAPTIC_MAX_DURATION_MS 250     // hard clamp
#define EP_HAPTIC_MAX_ONTIME_MS_PER_SEC 400  // rolling duty cap
#define EP_QUADRANT_COOLDOWN_MS   20
