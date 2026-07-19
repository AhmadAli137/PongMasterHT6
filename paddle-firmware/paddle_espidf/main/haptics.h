/* Four-quadrant ERM drive via LEDC, with firmware safety clamps (spec §17.3):
 * duty ceiling for 3 V motors on a 4.2 V cell, max pulse duration, and a
 * hard all-off used on Wi-Fi loss.
 */
#pragma once

#include <stdint.h>
#include "esp_err.h"

esp_err_t haptics_init(void);

/* Apply one command's intensities (0..255 each) for duration_ms (clamped). */
void haptics_pulse(uint8_t q0, uint8_t q1, uint8_t q2, uint8_t q3, uint16_t duration_ms);

/* Immediate all-motors-off (also called automatically when a pulse expires). */
void haptics_all_off(void);
