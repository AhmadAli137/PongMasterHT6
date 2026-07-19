// ESP32-S3 pin assignments for the smart paddle (adjust to your board).
#pragma once

// Four haptic quadrant MOSFET gates (LEDC PWM capable pins).
#define PIN_HAPTIC_Q0 4   // top-left
#define PIN_HAPTIC_Q1 5   // top-right
#define PIN_HAPTIC_Q2 6   // bottom-left
#define PIN_HAPTIC_Q3 7   // bottom-right

// Addressable RGB (WS2812) data pin.
#define PIN_RGB_LED   8
#define EP_RGB_COUNT  3

// Calibration / start button (active low, internal pullup).
#define PIN_BUTTON    9

// Battery voltage divider ADC.
#define PIN_BATTERY   10

// IMU I2C (or SPI — adapt for your part).
#define PIN_IMU_SDA   11
#define PIN_IMU_SCL   12

// LEDC channels for the four haptic quadrants.
#define CH_HAPTIC_Q0 0
#define CH_HAPTIC_Q1 1
#define CH_HAPTIC_Q2 2
#define CH_HAPTIC_Q3 3
