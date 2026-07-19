/* Minimal STA Wi-Fi link for the paddle.
 *
 * Connect logic follows the proven pattern from
 * SketchBot_clubHacks26/firmware/src/network_hal.cpp (dev/hardcoded path):
 * event group + GOT_IP bit, auto-reconnect from the event handler, retry
 * loop tolerant of band-steering routers that reject the first auth.
 */
#pragma once

#include <stdbool.h>
#include "esp_err.h"

/* Blocks until connected (or timeout_ms; UINT32_MAX = forever). */
esp_err_t wifi_link_connect(uint32_t timeout_ms);

/* True while associated with an IP. */
bool wifi_link_up(void);

/* Current AP signal strength in dBm (0 if not connected). */
int wifi_link_rssi(void);

/* Count of successful (re)connections since boot. */
uint32_t wifi_link_reconnects(void);

