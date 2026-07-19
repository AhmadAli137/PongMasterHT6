/* MPU6050 (GY-521) driver + on-device orientation fusion.
 *
 * I2C on SDA=GPIO10, SCL=GPIO26 (both clear of the C5's reserved functions and
 * the four motor pins 4/5/23/24). A background task samples the sensor at
 * 100 Hz, runs a Mahony complementary filter (gyro integrated, gravity from
 * the accelerometer corrects pitch/roll drift), and publishes the latest fused
 * orientation quaternion for the telemetry task to ship to the backend.
 */
#pragma once

#include <stdbool.h>
#include "esp_err.h"

#define IMU_SDA_GPIO 10
#define IMU_SCL_GPIO 26
#define IMU_I2C_ADDR 0x68  /* AD0 low */

typedef struct {
    float quat_w, quat_x, quat_y, quat_z; /* fused orientation, w-first */
    float gyro_x, gyro_y, gyro_z;         /* rad/s, bias-corrected */
    float accel_x, accel_y, accel_z;      /* m/s^2 */
    bool calibrated;                      /* gyro bias captured -> trust it */
} imu_sample_t;

/* Bring up I2C and the MPU6050; verifies WHO_AM_I. Returns ESP_OK on success,
 * ESP_ERR_NOT_FOUND if the sensor doesn't answer (caller can keep running with
 * an identity orientation). */
esp_err_t imu_init(void);

/* Start the sampling + fusion task (call once, after imu_init succeeds). */
void imu_task_start(void);

/* Copy the most recent fused sample. Returns false if the IMU never came up. */
bool imu_get(imu_sample_t *out);
