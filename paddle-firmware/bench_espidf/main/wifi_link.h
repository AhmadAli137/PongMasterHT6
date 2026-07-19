/* Minimal STA Wi-Fi link — same as the paddle firmware's, included here only
 * to keep the ESP32's current draw up (WIFI_PS_NONE) so power-bank boost
 * boards like the DAOKI don't auto-shut-off during a long bench session.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

/* Blocks until connected (or timeout_ms; UINT32_MAX = forever). */
esp_err_t wifi_link_connect(uint32_t timeout_ms);

/* True while associated with an IP. */
bool wifi_link_up(void);

/* Current AP signal strength in dBm (0 if not connected). */
int wifi_link_rssi(void);

/* Count of successful (re)connections since boot. */
uint32_t wifi_link_reconnects(void);
