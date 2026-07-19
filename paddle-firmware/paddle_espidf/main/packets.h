/* Binary wire protocol — MUST stay byte-identical to
 * backend/edgepong/paddle/packets.py (little-endian, trailing CRC32).
 * The ESP32 is little-endian, so packed structs map directly.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_rom_crc.h"

#define PKT_MAGIC 0x5047 /* "PG" */
#define PKT_VERSION 1

#define MSG_TELEMETRY 1
#define MSG_HAPTIC 2
#define MSG_LED 3
#define MSG_HEARTBEAT 4

/* status_bits */
#define STATUS_IMU_CALIBRATED (1 << 0)
#define STATUS_WIFI_CONNECTED (1 << 1)
#define STATUS_LOW_BATTERY (1 << 2)
#define STATUS_HAPTIC_LIMITED (1 << 3)
#define STATUS_SENSOR_FAULT (1 << 4)

/* haptic flags */
#define HAPTIC_FLAG_FLASH_RGB (1 << 0)
#define HAPTIC_FLAG_PERFECT (1 << 1)
#define HAPTIC_FLAG_ERROR (1 << 2)
#define HAPTIC_FLAG_CANCEL (1 << 3)

typedef struct __attribute__((packed)) {
    uint16_t magic;          /* 0x5047 */
    uint8_t version;         /* 1 */
    uint8_t message_type;    /* 1 */
    uint32_t sequence;
    uint64_t paddle_time_us;
    float quat_w, quat_x, quat_y, quat_z;
    float gyro_x, gyro_y, gyro_z;      /* rad/s */
    float accel_x, accel_y, accel_z;   /* m/s^2 */
    uint16_t battery_mv;
    uint8_t button_bits;
    uint8_t status_bits;
    uint32_t crc32;          /* over all preceding bytes */
} paddle_telemetry_t;
_Static_assert(sizeof(paddle_telemetry_t) == 64, "telemetry layout drifted");

typedef struct __attribute__((packed)) {
    uint16_t magic;
    uint8_t version;
    uint8_t message_type;    /* 2 */
    uint32_t command_sequence;
    uint64_t execute_time_us; /* 0 = immediate */
    uint8_t q0, q1, q2, q3;   /* intensity 0..255 */
    uint16_t duration_ms;
    uint8_t waveform;         /* 0 pulse, 1 ramp, 2 double-tap */
    uint8_t flags;
    uint32_t crc32;
} haptic_command_t;
_Static_assert(sizeof(haptic_command_t) == 28, "haptic layout drifted");

/* zlib-compatible CRC32 (matches Python's zlib.crc32). */
static inline uint32_t pkt_crc32(const void *data, size_t len)
{
    return esp_rom_crc32_le(0, (const uint8_t *)data, len);
}

static inline void telemetry_seal(paddle_telemetry_t *t)
{
    t->magic = PKT_MAGIC;
    t->version = PKT_VERSION;
    t->message_type = MSG_TELEMETRY;
    t->crc32 = pkt_crc32(t, sizeof(*t) - sizeof(uint32_t));
}

/* Returns true if a received buffer is a valid haptic command. */
static inline bool haptic_decode(const uint8_t *buf, size_t len, haptic_command_t *out)
{
    if (len != sizeof(haptic_command_t)) return false;
    const haptic_command_t *c = (const haptic_command_t *)buf;
    if (c->magic != PKT_MAGIC || c->version != PKT_VERSION || c->message_type != MSG_HAPTIC) return false;
    if (pkt_crc32(buf, sizeof(*c) - sizeof(uint32_t)) != c->crc32) return false;
    *out = *c;
    return true;
}
