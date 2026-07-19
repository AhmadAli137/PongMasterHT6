// Edge Pong wire protocol — MUST match backend/edgepong/paddle/packets.py.
// Little-endian, versioned, trailing CRC32 over all preceding bytes.
#pragma once
#include <stdint.h>

static const uint16_t EP_MAGIC = 0x5047;  // "PG"
static const uint8_t  EP_VERSION = 1;

enum EpMsgType : uint8_t {
  EP_MSG_TELEMETRY = 1,
  EP_MSG_HAPTIC    = 2,
  EP_MSG_LED       = 3,
  EP_MSG_HEARTBEAT = 4,
};

// status_bits (telemetry)
static const uint8_t EP_STATUS_IMU_CALIBRATED = 1 << 0;
static const uint8_t EP_STATUS_WIFI_CONNECTED = 1 << 1;
static const uint8_t EP_STATUS_LOW_BATTERY    = 1 << 2;
static const uint8_t EP_STATUS_HAPTIC_LIMITED = 1 << 3;
static const uint8_t EP_STATUS_SENSOR_FAULT   = 1 << 4;

// haptic flags
static const uint8_t EP_HAPTIC_FLAG_FLASH_RGB = 1 << 0;
static const uint8_t EP_HAPTIC_FLAG_PERFECT   = 1 << 1;
static const uint8_t EP_HAPTIC_FLAG_ERROR     = 1 << 2;
static const uint8_t EP_HAPTIC_FLAG_CANCEL    = 1 << 3;

// waveforms
static const uint8_t EP_WAVE_PULSE      = 0;
static const uint8_t EP_WAVE_RAMP       = 1;
static const uint8_t EP_WAVE_DOUBLE_TAP = 2;

#pragma pack(push, 1)
struct PaddleTelemetryV1 {
  uint16_t magic;          // EP_MAGIC
  uint8_t  version;        // 1
  uint8_t  message_type;   // EP_MSG_TELEMETRY
  uint32_t sequence;
  uint64_t paddle_time_us;
  float    quat_w, quat_x, quat_y, quat_z;
  float    gyro_x, gyro_y, gyro_z;      // rad/s
  float    accel_x, accel_y, accel_z;   // m/s^2
  uint16_t battery_mv;
  uint8_t  button_bits;
  uint8_t  status_bits;
  uint32_t crc32;
};

struct HapticCommandV1 {
  uint16_t magic;
  uint8_t  version;
  uint8_t  message_type;   // EP_MSG_HAPTIC
  uint32_t command_sequence;
  uint64_t execute_time_us; // 0 = immediate
  uint8_t  q0, q1, q2, q3;  // 0..255 quadrant intensity
  uint16_t duration_ms;     // firmware clamps to safe max
  uint8_t  waveform;
  uint8_t  flags;
  uint32_t crc32;
};

struct LedCommandV1 {
  uint16_t magic;
  uint8_t  version;
  uint8_t  message_type;   // EP_MSG_LED
  uint32_t sequence;
  uint8_t  red, green, blue;
  uint8_t  effect;
  uint16_t duration_ms;
  uint16_t reserved;
  uint32_t crc32;
};
#pragma pack(pop)

uint32_t ep_crc32(const uint8_t* data, size_t len);
