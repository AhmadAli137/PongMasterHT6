/*
 * Edge Pong — haptic driver bench test (ESP-IDF, ESP32-C5)
 *
 * Exercises the four MOSFET quadrant drivers so you can calibrate the real
 * motors before wiring up the game firmware:
 *
 *   Phase 1  Stiction sweep UP     — find the duty where motors START spinning
 *   Phase 2  Drop-out sweep DOWN   — find the duty where motors STOP
 *   Phase 3  Impact pulses         — game-style 40 ms hits at 30/50/70/90 %
 *   Phase 4  Overdrive kick A/B    — same pulse with/without a 15 ms 100 % kick
 *   Phase 5  Quadrant round-robin  — spatial check, one quadrant at a time
 *   Phase 6  All-quadrant burst    — worst-case current draw (brownout test)
 *
 * If the board browns out during Phase 6 it will reboot and print a giant
 * warning (reset reason is checked at startup): that means your bulk
 * capacitor is too small / battery wiring too thin.
 *
 * Phase 1/2 findings map directly to config/default.yaml:
 *   haptics.min_intensity ≈ (start duty %) / MAX_DUTY_PCT
 *
 * Wiring per quadrant (see docs/hardware in the repo):
 *   GPIO -> 150R -> gate, 10k gate->GND, source->GND,
 *   drain -> motor(-), motor(+) -> VBAT, 1N5819 stripe->VBAT across motors.
 *
 * Build:  idf.py set-target esp32c5 && idf.py build flash monitor
 * Needs ESP-IDF v5.5+ (first release with ESP32-C5 support).
 */

#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/ledc.h"
#include "esp_log.h"
#include "esp_system.h"

static const char *TAG = "haptic-bench";

/* ---- adjust to your wiring. All four are clear of ESP32-C5 reserved
 * functions: strapping (2/7/25/27/28), SPI flash/PSRAM (16-22), USB-JTAG
 * (13/14), console UART (11/12), IMU I2C (1/3). GPIO7 was dropped because a
 * gate pulldown on a strapping pin can force the wrong boot mode. ---- */
#define NUM_QUADRANTS 4
static const int QUAD_GPIO[NUM_QUADRANTS] = { 4, 5, 23, 24 }; /* Q0 TL, Q1 TR, Q2 BL, Q3 BR */

/* ---- PWM setup ---- */
#define PWM_FREQ_HZ   15000              /* above audible whine */
#define PWM_RES       LEDC_TIMER_10_BIT  /* duty 0..1023 */
#define PWM_MAX_RAW   ((1 << 10) - 1)

/* Safety: 3 V motors on a 4.2 V (full) Li-ion rail — never exceed this duty.
 * 70 % duty of 4.2 V ≈ 3.0 V equivalent power. */
#define MAX_DUTY_PCT  70

/* Set to 1 while probing a single test cell so only Q0 fires. */
#define SINGLE_QUADRANT_ONLY 0

/* -------------------------------------------------------------------- */

static void set_duty_pct(int quadrant, int pct)
{
    if (pct < 0) pct = 0;
    if (pct > MAX_DUTY_PCT) pct = MAX_DUTY_PCT;
    uint32_t raw = (uint32_t)pct * PWM_MAX_RAW / 100;
    ledc_set_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)quadrant, raw);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)quadrant);
}

/* raw (clamp-free) variant used ONLY for the deliberate 100 % overdrive kick,
 * which is safe because it lasts 15 ms */
static void set_duty_pct_kick(int quadrant, int pct)
{
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    uint32_t raw = (uint32_t)pct * PWM_MAX_RAW / 100;
    ledc_set_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)quadrant, raw);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)quadrant);
}

static void all_off(void)
{
    for (int q = 0; q < NUM_QUADRANTS; q++) set_duty_pct(q, 0);
}

static void all_set(int pct)
{
    int n = SINGLE_QUADRANT_ONLY ? 1 : NUM_QUADRANTS;
    for (int q = 0; q < n; q++) set_duty_pct(q, pct);
}

static void delay_ms(int ms) { vTaskDelay(pdMS_TO_TICKS(ms)); }

/* -------------------------------------------------------------------- */

static void phase_banner(int n, const char *title, const char *instructions)
{
    ESP_LOGI(TAG, "");
    ESP_LOGI(TAG, "==================================================");
    ESP_LOGI(TAG, " PHASE %d: %s", n, title);
    ESP_LOGI(TAG, " %s", instructions);
    ESP_LOGI(TAG, "==================================================");
    delay_ms(2000);
}

static void phase1_stiction_sweep_up(void)
{
    phase_banner(1, "STICTION SWEEP UP",
                 "Note the duty %% where the motor STARTS spinning");
    for (int pct = 10; pct <= MAX_DUTY_PCT; pct += 2) {
        ESP_LOGI(TAG, "  duty = %2d %%", pct);
        all_set(pct);
        delay_ms(600);
    }
    all_off();
    delay_ms(1000);
}

static void phase2_dropout_sweep_down(void)
{
    phase_banner(2, "DROP-OUT SWEEP DOWN",
                 "Note the duty %% where the motor STOPS (lower than start, inertia)");
    all_set(MAX_DUTY_PCT);
    delay_ms(800);
    for (int pct = MAX_DUTY_PCT; pct >= 8; pct -= 2) {
        ESP_LOGI(TAG, "  duty = %2d %%", pct);
        all_set(pct);
        delay_ms(600);
    }
    all_off();
    delay_ms(1000);
}

static void phase3_impact_pulses(void)
{
    phase_banner(3, "IMPACT PULSES",
                 "Game-style 40 ms hits: should feel like distinct taps, not buzzes");
    const int levels[] = { 30, 50, 70, 90 };
    for (int i = 0; i < 4; i++) {
        int pct = levels[i] * MAX_DUTY_PCT / 100; /* scaled into the safe window */
        ESP_LOGI(TAG, "  pulse: %d %% of range (%d %% duty), 40 ms", levels[i], pct);
        all_set(pct);
        delay_ms(40);
        all_off();
        delay_ms(1200);
    }
}

static void phase4_overdrive_kick(void)
{
    phase_banner(4, "OVERDRIVE KICK A/B",
                 "Same 40 ms pulse: A = plain 45 %%, B = 15 ms 100 %% kick then 45 %%. B should feel much crisper");
    for (int rep = 0; rep < 3; rep++) {
        ESP_LOGI(TAG, "  A: plain pulse");
        all_set(45);
        delay_ms(40);
        all_off();
        delay_ms(900);

        ESP_LOGI(TAG, "  B: kick + pulse");
        int n = SINGLE_QUADRANT_ONLY ? 1 : NUM_QUADRANTS;
        for (int q = 0; q < n; q++) set_duty_pct_kick(q, 100);
        delay_ms(15);
        all_set(45);
        delay_ms(25);
        all_off();
        delay_ms(900);
    }
}

static void phase5_quadrant_roundrobin(void)
{
    if (SINGLE_QUADRANT_ONLY) {
        ESP_LOGI(TAG, "PHASE 5 skipped (SINGLE_QUADRANT_ONLY=1)");
        return;
    }
    phase_banner(5, "QUADRANT ROUND-ROBIN",
                 "Q0 TL -> Q1 TR -> Q2 BL -> Q3 BR: confirm each buzzes alone, correct corner");
    for (int q = 0; q < NUM_QUADRANTS; q++) {
        ESP_LOGI(TAG, "  quadrant Q%d (GPIO %d)", q, QUAD_GPIO[q]);
        set_duty_pct(q, 55);
        delay_ms(350);
        set_duty_pct(q, 0);
        delay_ms(500);
    }
}

static void phase6_all_quadrant_burst(void)
{
    phase_banner(6, "ALL-QUADRANT MAX BURST (brownout test)",
                 "250 ms of everything at max duty. If the board reboots, your bulk cap/wiring is inadequate");
    ESP_LOGI(TAG, "  burst in 3.. 2.. 1..");
    delay_ms(3000);
    all_set(MAX_DUTY_PCT);
    delay_ms(250);
    all_off();
    ESP_LOGI(TAG, "  survived the burst — supply is healthy");
    delay_ms(1500);
}

/* -------------------------------------------------------------------- */

void app_main(void)
{
    /* If the previous run ended in a brownout, say so LOUDLY. */
    esp_reset_reason_t why = esp_reset_reason();
    if (why == ESP_RST_BROWNOUT) {
        for (int i = 0; i < 5; i++) {
            ESP_LOGE(TAG, "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!");
            ESP_LOGE(TAG, "!! LAST RUN ENDED IN A BROWNOUT RESET           !!");
            ESP_LOGE(TAG, "!! -> bigger bulk cap (470-1000uF) at the rails !!");
            ESP_LOGE(TAG, "!! -> thicker/shorter battery wires             !!");
            ESP_LOGE(TAG, "!! -> check battery protection current limit    !!");
            ESP_LOGE(TAG, "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!");
        }
    } else {
        ESP_LOGI(TAG, "reset reason: %d (1=power-on)", (int)why);
    }

    /* one LEDC timer shared by all four channels */
    ledc_timer_config_t timer_cfg = {
        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .duty_resolution = PWM_RES,
        .timer_num       = LEDC_TIMER_0,
        .freq_hz         = PWM_FREQ_HZ,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer_cfg));

    for (int q = 0; q < NUM_QUADRANTS; q++) {
        ledc_channel_config_t ch_cfg = {
            .gpio_num   = QUAD_GPIO[q],
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel    = (ledc_channel_t)q,
            .intr_type  = LEDC_INTR_DISABLE,
            .timer_sel  = LEDC_TIMER_0,
            .duty       = 0,   /* motors OFF at boot — spec §29 */
            .hpoint     = 0,
        };
        ESP_ERROR_CHECK(ledc_channel_config(&ch_cfg));
    }
    all_off();

    ESP_LOGI(TAG, "haptic bench ready: %d quadrant(s), %d Hz PWM, duty clamp %d %%",
             SINGLE_QUADRANT_ONLY ? 1 : NUM_QUADRANTS, PWM_FREQ_HZ, MAX_DUTY_PCT);
    ESP_LOGI(TAG, "starting in 3 s — have a notepad ready for phases 1 and 2");
    delay_ms(3000);

    while (true) {
        phase1_stiction_sweep_up();
        phase2_dropout_sweep_down();
        phase3_impact_pulses();
        phase4_overdrive_kick();
        phase5_quadrant_roundrobin();
        phase6_all_quadrant_burst();

        ESP_LOGI(TAG, "");
        ESP_LOGI(TAG, "cycle complete — repeating in 5 s (Ctrl+] to exit monitor)");
        ESP_LOGI(TAG, "record: start duty (P1), stop duty (P2), whether B beat A (P4)");
        delay_ms(5000);
    }
}
