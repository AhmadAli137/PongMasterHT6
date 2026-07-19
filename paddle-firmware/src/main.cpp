// Edge Pong smart paddle firmware (ESP32-S3).
//
// Responsibilities (spec §5.3, §12.1, §17.3):
//   - sample IMU, produce an orientation quaternion + gyro + accel
//   - stream telemetry over UDP at EP_TELEMETRY_HZ (newest-only)
//   - receive haptic/LED commands, drive four PWM quadrants with hard safety
//     clamps, and turn all motors OFF on Wi-Fi loss
//
// The IMU read is stubbed — drop in your BMI270 / ICM-42688 / MPU-6050 driver
// where marked. Everything else (transport, PWM, safety) is wired up.

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <FastLED.h>

#include "config.h"
#include "pins.h"
#include "packets.h"

static WiFiUDP udp;
static CRGB leds[EP_RGB_COUNT];
static uint32_t txSeq = 0;

// rolling on-time budget per quadrant for the duty cap
static uint32_t quadOnTimeMs[4] = {0, 0, 0, 0};
static uint32_t lastBudgetReset = 0;
static uint32_t quadOffAtMs[4] = {0, 0, 0, 0};

// ---- CRC32 (matches Python zlib.crc32) ---------------------------------- //
uint32_t ep_crc32(const uint8_t* data, size_t len) {
  uint32_t crc = 0xFFFFFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (int b = 0; b < 8; b++)
      crc = (crc >> 1) ^ (0xEDB88320 & (-(int32_t)(crc & 1)));
  }
  return ~crc;
}

// ---- Haptics ------------------------------------------------------------- //
static const uint8_t HAPTIC_CH[4] = {CH_HAPTIC_Q0, CH_HAPTIC_Q1, CH_HAPTIC_Q2, CH_HAPTIC_Q3};

static void hapticsInit() {
  const uint8_t pins[4] = {PIN_HAPTIC_Q0, PIN_HAPTIC_Q1, PIN_HAPTIC_Q2, PIN_HAPTIC_Q3};
  for (int i = 0; i < 4; i++) {
    ledcSetup(HAPTIC_CH[i], EP_HAPTIC_PWM_FREQ_HZ, 8);
    ledcAttachPin(pins[i], HAPTIC_CH[i]);
    ledcWrite(HAPTIC_CH[i], 0);
  }
}

static void hapticsAllOff() {
  for (int i = 0; i < 4; i++) ledcWrite(HAPTIC_CH[i], 0);
}

// Fire one quadrant with safety clamps applied.
static void hapticFire(int q, uint8_t intensity, uint16_t durationMs) {
  uint32_t now = millis();
  // duty cap (rolling 1s budget)
  if (now - lastBudgetReset > 1000) {
    for (int i = 0; i < 4; i++) quadOnTimeMs[i] = 0;
    lastBudgetReset = now;
  }
  uint16_t dur = min(durationMs, (uint16_t)EP_HAPTIC_MAX_DURATION_MS);
  if (quadOnTimeMs[q] + dur > EP_HAPTIC_MAX_ONTIME_MS_PER_SEC) return;  // thermal guard

  uint8_t maxDuty = (uint8_t)(EP_HAPTIC_MAX_DUTY * 255.0f);
  uint8_t duty = min(intensity, maxDuty);
  ledcWrite(HAPTIC_CH[q], duty);
  quadOffAtMs[q] = now + dur;
  quadOnTimeMs[q] += dur;
}

static void hapticsTick() {
  uint32_t now = millis();
  for (int i = 0; i < 4; i++)
    if (quadOffAtMs[i] && now >= quadOffAtMs[i]) {
      ledcWrite(HAPTIC_CH[i], 0);
      quadOffAtMs[i] = 0;
    }
}

// ---- IMU (STUB) ---------------------------------------------------------- //
struct ImuSample { float qw, qx, qy, qz, gx, gy, gz, ax, ay, az; };

static void imuInit() { /* TODO: init your IMU + calibrate gyro bias at rest */ }

static ImuSample imuRead() {
  // TODO: replace with a real driver + Madgwick/Mahony fusion (spec §12.1).
  ImuSample s{};
  s.qw = 1.0f; s.qx = s.qy = s.qz = 0.0f;
  s.gx = s.gy = s.gz = 0.0f;
  s.ax = 0.0f; s.ay = 9.81f; s.az = 0.0f;
  return s;
}

// ---- Telemetry ----------------------------------------------------------- //
static void sendTelemetry(const ImuSample& s) {
  PaddleTelemetryV1 t{};
  t.magic = EP_MAGIC; t.version = EP_VERSION; t.message_type = EP_MSG_TELEMETRY;
  t.sequence = ++txSeq;
  t.paddle_time_us = (uint64_t)micros();
  t.quat_w = s.qw; t.quat_x = s.qx; t.quat_y = s.qy; t.quat_z = s.qz;
  t.gyro_x = s.gx; t.gyro_y = s.gy; t.gyro_z = s.gz;
  t.accel_x = s.ax; t.accel_y = s.ay; t.accel_z = s.az;
  t.battery_mv = (uint16_t)(analogReadMilliVolts(PIN_BATTERY) * 2);  // divider
  t.button_bits = digitalRead(PIN_BUTTON) == LOW ? 1 : 0;
  t.status_bits = EP_STATUS_IMU_CALIBRATED |
                  (WiFi.status() == WL_CONNECTED ? EP_STATUS_WIFI_CONNECTED : 0);
  t.crc32 = ep_crc32((const uint8_t*)&t, sizeof(t) - sizeof(uint32_t));

  udp.beginPacket(EP_HOST_IP, EP_TELEMETRY_PORT);
  udp.write((const uint8_t*)&t, sizeof(t));
  udp.endPacket();
}

// ---- Command receive ----------------------------------------------------- //
static void handleHaptic(const HapticCommandV1& c) {
  if (c.flags & EP_HAPTIC_FLAG_FLASH_RGB) {
    CRGB color = (c.flags & EP_HAPTIC_FLAG_PERFECT) ? CRGB(0, 220, 255)
               : (c.flags & EP_HAPTIC_FLAG_ERROR)   ? CRGB(255, 40, 60)
               : CRGB(200, 255, 220);
    fill_solid(leds, EP_RGB_COUNT, color);
    FastLED.show();
  }
  const uint8_t q[4] = {c.q0, c.q1, c.q2, c.q3};
  for (int i = 0; i < 4; i++)
    if (q[i] > 0) hapticFire(i, q[i], c.duration_ms);
}

static void pollCommands() {
  uint8_t buf[64];
  int n = udp.parsePacket();
  while (n > 0) {
    int len = udp.read(buf, sizeof(buf));
    if (len >= 4) {
      uint16_t magic; memcpy(&magic, buf, 2);
      uint8_t type = buf[3];
      if (magic == EP_MAGIC && type == EP_MSG_HAPTIC && len == (int)sizeof(HapticCommandV1)) {
        HapticCommandV1 c; memcpy(&c, buf, sizeof(c));
        uint32_t crc = ep_crc32((const uint8_t*)&c, sizeof(c) - sizeof(uint32_t));
        if (crc == c.crc32) handleHaptic(c);
      }
    }
    n = udp.parsePacket();
  }
}

// ---- Arduino lifecycle --------------------------------------------------- //
void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  FastLED.addLeds<WS2812, PIN_RGB_LED, GRB>(leds, EP_RGB_COUNT);
  fill_solid(leds, EP_RGB_COUNT, CRGB(0, 0, 40));  // booting
  FastLED.show();

  hapticsInit();
  hapticsAllOff();
  imuInit();

  WiFi.mode(WIFI_STA);
  WiFi.begin(EP_WIFI_SSID, EP_WIFI_PASSWORD);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) delay(200);
  udp.begin(EP_COMMAND_PORT);
  fill_solid(leds, EP_RGB_COUNT, CRGB(0, 120, 0));  // ready
  FastLED.show();
}

void loop() {
  static uint32_t lastTx = 0;
  uint32_t now = millis();

  // SAFETY: kill motors immediately if Wi-Fi drops (spec §17.3, §22.4).
  if (WiFi.status() != WL_CONNECTED) {
    hapticsAllOff();
    fill_solid(leds, EP_RGB_COUNT, CRGB(60, 0, 0));
    FastLED.show();
  }

  pollCommands();
  hapticsTick();

  if (now - lastTx >= (1000 / EP_TELEMETRY_HZ)) {
    lastTx = now;
    sendTelemetry(imuRead());
  }
}
