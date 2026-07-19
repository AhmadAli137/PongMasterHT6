/* Copy to secrets.h (gitignored) and fill in.
 * Pattern borrowed from SketchBot_clubHacks26/firmware. */
#pragma once

#define WIFI_SSID "YOUR_WIFI_SSID"
#define WIFI_PASS "YOUR_WIFI_PASSWORD"

/* The PC running the Edge Pong backend (find with `ipconfig` on it). */
#define BACKEND_HOST "192.168.1.100"
#define TELEMETRY_PORT 46000   /* paddle -> backend (must match config/default.yaml) */
#define COMMAND_PORT 46001     /* backend -> paddle haptic commands */
