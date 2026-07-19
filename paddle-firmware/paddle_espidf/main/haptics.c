#include "haptics.h"

#include "driver/ledc.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "haptics";

/* Same pin map as bench_espidf — Q0 TL, Q1 TR, Q2 BL, Q3 BR.
 * All four are clear of the ESP32-C5 reserved functions: strapping pins
 * (2/7/25/27/28), SPI flash/PSRAM (16-22), USB-JTAG (13/14), console UART
 * (11/12), and the IMU I2C bus (SDA 10 / SCL 26). GPIO7 was deliberately
 * dropped — it's a strapping pin, and a gate pulldown would disrupt boot. */
#define NUM_QUADRANTS 4
static const int QUAD_GPIO[NUM_QUADRANTS] = { 4, 5, 23, 24 };

#define PWM_FREQ_HZ 15000
#define PWM_RES LEDC_TIMER_10_BIT
#define PWM_MAX_RAW ((1 << 10) - 1)

/* Safety clamps (spec §17.3): 3 V motors on a 3.0-4.2 V cell. */
#define MAX_DUTY_PCT 70
#define MAX_PULSE_MS 250

static esp_timer_handle_t s_off_timer;

static void off_timer_cb(void *arg)
{
    (void)arg;
    haptics_all_off();
}

esp_err_t haptics_init(void)
{
    ledc_timer_config_t timer_cfg = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = PWM_RES,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = PWM_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer_cfg));

    for (int q = 0; q < NUM_QUADRANTS; q++) {
        ledc_channel_config_t ch = {
            .gpio_num = QUAD_GPIO[q],
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = (ledc_channel_t)q,
            .intr_type = LEDC_INTR_DISABLE,
            .timer_sel = LEDC_TIMER_0,
            .duty = 0, /* motors OFF at boot — spec §29 */
            .hpoint = 0,
        };
        ESP_ERROR_CHECK(ledc_channel_config(&ch));
    }

    const esp_timer_create_args_t targs = {
        .callback = &off_timer_cb,
        .name = "haptic_off",
    };
    ESP_ERROR_CHECK(esp_timer_create(&targs, &s_off_timer));
    ESP_LOGI(TAG, "ready: 4ch %d Hz, duty clamp %d%%, pulse clamp %d ms",
             PWM_FREQ_HZ, MAX_DUTY_PCT, MAX_PULSE_MS);
    return ESP_OK;
}

static void set_quadrant(int q, uint8_t intensity)
{
    /* intensity 0..255 maps into 0..MAX_DUTY_PCT of full scale */
    uint32_t duty = (uint32_t)intensity * PWM_MAX_RAW * MAX_DUTY_PCT / (255 * 100);
    ledc_set_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)q, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)q);
}

void haptics_pulse(uint8_t q0, uint8_t q1, uint8_t q2, uint8_t q3, uint16_t duration_ms)
{
    if (duration_ms == 0) {
        haptics_all_off();
        return;
    }
    if (duration_ms > MAX_PULSE_MS) duration_ms = MAX_PULSE_MS;

    set_quadrant(0, q0);
    set_quadrant(1, q1);
    set_quadrant(2, q2);
    set_quadrant(3, q3);

    /* one-shot auto-off: re-arm replaces any pending expiry */
    esp_timer_stop(s_off_timer);
    ESP_ERROR_CHECK(esp_timer_start_once(s_off_timer, (uint64_t)duration_ms * 1000ULL));
}

void haptics_all_off(void)
{
    for (int q = 0; q < NUM_QUADRANTS; q++) set_quadrant(q, 0);
}
