#include "imu.h"

#include <math.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "imu";

/* MPU6050 registers */
#define REG_SMPLRT_DIV   0x19
#define REG_CONFIG       0x1A
#define REG_GYRO_CONFIG  0x1B
#define REG_ACCEL_CONFIG 0x1C
#define REG_ACCEL_XOUT_H 0x3B
#define REG_PWR_MGMT_1   0x6B
#define REG_WHO_AM_I     0x75

/* Full-scale selects. Gyro ±1000 dps and accel ±8 g give a swung paddle plenty
 * of headroom before clipping while keeping usable resolution. */
#define GYRO_FS_SEL   2      /* 0:250 1:500 2:1000 3:2000 dps */
#define ACCEL_FS_SEL  2      /* 0:2 1:4 2:8 3:16 g */
#define GYRO_LSB_PER_DPS  32.8f   /* ±1000 dps */
#define ACCEL_LSB_PER_G   4096.0f /* ±8 g */
#define DEG2RAD  0.017453292519943295f
#define G_MPS2   9.80665f

/* 100 Hz keeps the loop period an exact FreeRTOS tick (CONFIG_FREERTOS_HZ=100)
 * so vTaskDelayUntil never rounds to a busy-loop; still ample for orientation. */
#define SAMPLE_HZ 100
#define MAHONY_KP 2.0f  /* accel-correction gain */

static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_dev;
static bool s_up = false;

/* fused state (accessed by the task; copied out under a spinlock) */
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static imu_sample_t s_latest = { .quat_w = 1.0f };
static float q0 = 1.0f, q1 = 0.0f, q2 = 0.0f, q3 = 0.0f;
static float s_bias_x = 0, s_bias_y = 0, s_bias_z = 0;
static bool s_calibrated = false;

/* ------------------------------------------------------------------ */
static esp_err_t reg_write(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = { reg, val };
    return i2c_master_transmit(s_dev, buf, sizeof(buf), 100);
}

static esp_err_t reg_read(uint8_t reg, uint8_t *data, size_t n)
{
    return i2c_master_transmit_receive(s_dev, &reg, 1, data, n, 100);
}

esp_err_t imu_init(void)
{
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = -1,                  /* auto-pick a free controller */
        .sda_io_num = IMU_SDA_GPIO,
        .scl_io_num = IMU_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true, /* the GY-521 also has its own */
    };
    ESP_ERROR_CHECK(i2c_new_master_bus(&bus_cfg, &s_bus));

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = IMU_I2C_ADDR,
        .scl_speed_hz = 400000,
    };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(s_bus, &dev_cfg, &s_dev));

    uint8_t who = 0;
    if (reg_read(REG_WHO_AM_I, &who, 1) != ESP_OK) {
        ESP_LOGE(TAG, "no reply on I2C (SDA=%d SCL=%d) - check wiring/power",
                 IMU_SDA_GPIO, IMU_SCL_GPIO);
        return ESP_ERR_NOT_FOUND;
    }
    ESP_LOGI(TAG, "WHO_AM_I = 0x%02x (expect 0x68)", who);
    if (who != 0x68 && who != 0x70 && who != 0x71 && who != 0x98) {
        ESP_LOGW(TAG, "unexpected WHO_AM_I - continuing anyway");
    }

    /* reset, wake on gyro-X PLL, DLPF ~44 Hz, 1 kHz sample base, set ranges */
    reg_write(REG_PWR_MGMT_1, 0x80);
    vTaskDelay(pdMS_TO_TICKS(100));
    reg_write(REG_PWR_MGMT_1, 0x01);
    vTaskDelay(pdMS_TO_TICKS(10));
    reg_write(REG_CONFIG, 0x03);
    reg_write(REG_SMPLRT_DIV, 0x00);
    reg_write(REG_GYRO_CONFIG, GYRO_FS_SEL << 3);
    reg_write(REG_ACCEL_CONFIG, ACCEL_FS_SEL << 3);
    vTaskDelay(pdMS_TO_TICKS(10));

    s_up = true;
    ESP_LOGI(TAG, "MPU6050 up: gyro +/-1000dps, accel +/-8g @ %d Hz", SAMPLE_HZ);
    return ESP_OK;
}

/* Read the 14-byte accel/temp/gyro block into physical units. */
static bool read_raw(float *ax, float *ay, float *az,
                     float *gx, float *gy, float *gz)
{
    uint8_t b[14];
    if (reg_read(REG_ACCEL_XOUT_H, b, sizeof(b)) != ESP_OK) return false;
    int16_t rax = (int16_t)((b[0] << 8) | b[1]);
    int16_t ray = (int16_t)((b[2] << 8) | b[3]);
    int16_t raz = (int16_t)((b[4] << 8) | b[5]);
    /* b[6..7] = temperature, ignored */
    int16_t rgx = (int16_t)((b[8] << 8) | b[9]);
    int16_t rgy = (int16_t)((b[10] << 8) | b[11]);
    int16_t rgz = (int16_t)((b[12] << 8) | b[13]);

    *ax = (float)rax / ACCEL_LSB_PER_G * G_MPS2;
    *ay = (float)ray / ACCEL_LSB_PER_G * G_MPS2;
    *az = (float)raz / ACCEL_LSB_PER_G * G_MPS2;
    *gx = (float)rgx / GYRO_LSB_PER_DPS * DEG2RAD;
    *gy = (float)rgy / GYRO_LSB_PER_DPS * DEG2RAD;
    *gz = (float)rgz / GYRO_LSB_PER_DPS * DEG2RAD;
    return true;
}

/* Average a few hundred still samples to learn the gyro's zero-rate offset. */
static void calibrate_bias(void)
{
    const int N = 200;   /* ~2 s of "hold still" at one 10 ms tick per sample */
    float sx = 0, sy = 0, sz = 0, ax, ay, az, gx, gy, gz;
    int got = 0;
    for (int i = 0; i < N; i++) {
        if (read_raw(&ax, &ay, &az, &gx, &gy, &gz)) {
            sx += gx; sy += gy; sz += gz; got++;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (got > 0) { s_bias_x = sx / got; s_bias_y = sy / got; s_bias_z = sz / got; }
    ESP_LOGI(TAG, "gyro bias rad/s: %.4f %.4f %.4f (n=%d) - hold still done",
             s_bias_x, s_bias_y, s_bias_z, got);
    s_calibrated = true;
}

/* Mahony explicit complementary filter: gyro drives rotation, the gravity
 * vector from the accelerometer nudges pitch/roll back so they don't drift.
 * (Yaw is unobservable without a magnetometer and will slowly wander.) */
static void mahony_update(float gx, float gy, float gz,
                          float ax, float ay, float az, float dt)
{
    float n = sqrtf(ax * ax + ay * ay + az * az);
    if (n > 1e-6f) {
        ax /= n; ay /= n; az /= n;
        float vx = 2.0f * (q1 * q3 - q0 * q2);
        float vy = 2.0f * (q0 * q1 + q2 * q3);
        float vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3;
        float ex = ay * vz - az * vy;
        float ey = az * vx - ax * vz;
        float ez = ax * vy - ay * vx;
        gx += MAHONY_KP * ex;
        gy += MAHONY_KP * ey;
        gz += MAHONY_KP * ez;
    }
    float qa = q0, qb = q1, qc = q2;
    q0 += (-qb * gx - qc * gy - q3 * gz) * 0.5f * dt;
    q1 += (qa * gx + qc * gz - q3 * gy) * 0.5f * dt;
    q2 += (qa * gy - qb * gz + q3 * gx) * 0.5f * dt;
    q3 += (qa * gz + qb * gy - qc * gx) * 0.5f * dt;
    n = sqrtf(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3);
    if (n > 1e-6f) { q0 /= n; q1 /= n; q2 /= n; q3 /= n; }
}

static void imu_task(void *arg)
{
    (void)arg;
    calibrate_bias();

    int64_t last = esp_timer_get_time();
    TickType_t wake = xTaskGetTickCount();
    while (1) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(1000 / SAMPLE_HZ));
        float ax, ay, az, gx, gy, gz;
        if (!read_raw(&ax, &ay, &az, &gx, &gy, &gz)) continue;

        int64_t now = esp_timer_get_time();
        float dt = (float)(now - last) / 1e6f;
        last = now;
        if (dt <= 0.0f || dt > 0.1f) dt = 1.0f / SAMPLE_HZ;

        gx -= s_bias_x; gy -= s_bias_y; gz -= s_bias_z;
        mahony_update(gx, gy, gz, ax, ay, az, dt);

        portENTER_CRITICAL(&s_mux);
        s_latest.quat_w = q0; s_latest.quat_x = q1;
        s_latest.quat_y = q2; s_latest.quat_z = q3;
        s_latest.gyro_x = gx; s_latest.gyro_y = gy; s_latest.gyro_z = gz;
        s_latest.accel_x = ax; s_latest.accel_y = ay; s_latest.accel_z = az;
        s_latest.calibrated = s_calibrated;
        portEXIT_CRITICAL(&s_mux);
    }
}

void imu_task_start(void)
{
    if (!s_up) return;
    xTaskCreate(imu_task, "imu", 4096, NULL, 6, NULL);
}

bool imu_get(imu_sample_t *out)
{
    if (!s_up) return false;
    portENTER_CRITICAL(&s_mux);
    *out = s_latest;
    portEXIT_CRITICAL(&s_mux);
    return true;
}
