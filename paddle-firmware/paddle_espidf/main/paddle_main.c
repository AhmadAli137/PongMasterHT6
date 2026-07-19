/* Edge Pong paddle firmware — milestone 1: Wi-Fi + UDP link + haptics.
 *
 * What this does today:
 *   - joins Wi-Fi (secrets.h), modem power-save OFF (latency + DAOKI keep-alive)
 *   - streams telemetry packets at 50 Hz to the backend (identity quaternion
 *     for now — the IMU lands in milestone 2; the backend link/health logic,
 *     sequence tracking and CRC path all get exercised for real)
 *   - listens for haptic commands on UDP :46001 and drives the four ERM
 *     quadrants through LEDC, with safety clamps
 *   - kills all motors the moment Wi-Fi drops (spec §17.3 / §22.4)
 *
 * Pair it with the game backend: run the backend on your PC, set
 * EDGEPONG_COMMAND_HOST=<this paddle's IP>, play with the mouse — every
 * impact you feel in your hand is the full pipeline running end-to-end.
 */

#include <string.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "haptics.h"
#include "imu.h"
#include "packets.h"
#include "secrets.h"
#include "wifi_link.h"

static const char *TAG = "paddle";

#define TELEMETRY_HZ 50
#define STATUS_EVERY_S 3

/* shared counters for the periodic health line */
static volatile uint32_t s_tx_count = 0;      /* telemetry packets sent */
static volatile uint32_t s_haptic_count = 0;  /* valid haptic commands received */
static volatile uint32_t s_bad_count = 0;     /* malformed packets dropped */

/* ------------------------------------------------------------------ */
static void telemetry_task(void *arg)
{
    (void)arg;
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    struct sockaddr_in dest = { 0 };
    dest.sin_family = AF_INET;
    dest.sin_port = htons(TELEMETRY_PORT);
    dest.sin_addr.s_addr = inet_addr(BACKEND_HOST);

    paddle_telemetry_t t = { 0 };
    t.quat_w = 1.0f; /* identity until the first IMU sample lands */
    t.accel_y = 9.81f;
    t.battery_mv = 3900;

    uint32_t seq = 0;
    TickType_t last_wake = xTaskGetTickCount();
    while (1) {
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(1000 / TELEMETRY_HZ));
        if (!wifi_link_up()) continue;

        /* pull the latest fused orientation from the IMU task */
        uint8_t status = STATUS_WIFI_CONNECTED;
        imu_sample_t s;
        if (imu_get(&s)) {
            t.quat_w = s.quat_w; t.quat_x = s.quat_x;
            t.quat_y = s.quat_y; t.quat_z = s.quat_z;
            t.gyro_x = s.gyro_x; t.gyro_y = s.gyro_y; t.gyro_z = s.gyro_z;
            t.accel_x = s.accel_x; t.accel_y = s.accel_y; t.accel_z = s.accel_z;
            if (s.calibrated) status |= STATUS_IMU_CALIBRATED;
        } else {
            status |= STATUS_SENSOR_FAULT; /* no IMU -> identity, link still alive */
        }
        t.status_bits = status;

        t.sequence = seq++;
        t.paddle_time_us = (uint64_t)esp_timer_get_time();
        telemetry_seal(&t);
        int n = sendto(sock, &t, sizeof(t), 0, (struct sockaddr *)&dest, sizeof(dest));
        if (n == sizeof(t)) s_tx_count++;
    }
}

/* Periodic one-line health summary — the thing you actually watch. */
static void status_task(void *arg)
{
    (void)arg;
    uint32_t last_tx = 0, last_hap = 0;
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(STATUS_EVERY_S * 1000));
        uint32_t tx = s_tx_count, hap = s_haptic_count;
        uint32_t tx_rate = (tx - last_tx) / STATUS_EVERY_S;
        uint32_t hap_new = hap - last_hap;
        last_tx = tx; last_hap = hap;

        if (wifi_link_up()) {
            ESP_LOGI(TAG, "[status] WIFI ok  rssi=%d dBm  tx=%u (%u/s)  haptics=%u (+%u)  bad=%u  reconnects=%u  up=%llus",
                     wifi_link_rssi(), (unsigned)tx, (unsigned)tx_rate,
                     (unsigned)hap, (unsigned)hap_new, (unsigned)s_bad_count,
                     (unsigned)wifi_link_reconnects(),
                     (unsigned long long)(esp_timer_get_time() / 1000000ULL));
        } else {
            ESP_LOGW(TAG, "[status] WIFI DOWN - reconnecting  (tx=%u haptics=%u up=%llus)",
                     (unsigned)tx, (unsigned)hap,
                     (unsigned long long)(esp_timer_get_time() / 1000000ULL));
        }
    }
}

/* ------------------------------------------------------------------ */
static void haptic_rx_task(void *arg)
{
    (void)arg;
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    struct sockaddr_in bind_addr = { 0 };
    bind_addr.sin_family = AF_INET;
    bind_addr.sin_port = htons(COMMAND_PORT);
    bind_addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(sock, (struct sockaddr *)&bind_addr, sizeof(bind_addr)) != 0) {
        ESP_LOGE(TAG, "bind :%d failed - haptic rx dead", COMMAND_PORT);
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "haptic rx listening on udp/%d", COMMAND_PORT);

    /* recv timeout so we notice Wi-Fi loss and cut motors even with no traffic */
    struct timeval tv = { .tv_sec = 0, .tv_usec = 200000 };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    uint8_t buf[64];
    bool was_up = true;
    while (1) {
        int len = recv(sock, buf, sizeof(buf), 0);

        /* spec §17.3: on Wi-Fi loss, all motors off — checked every wakeup */
        bool up = wifi_link_up();
        if (was_up && !up) {
            ESP_LOGW(TAG, "wifi down - motors off");
            haptics_all_off();
        }
        was_up = up;
        if (len <= 0) continue;

        haptic_command_t cmd;
        if (!haptic_decode(buf, (size_t)len, &cmd)) {
            s_bad_count++;
            ESP_LOGW(TAG, "dropped invalid packet (%d B) - magic/version/crc mismatch", len);
            continue;
        }
        s_haptic_count++;
        if (cmd.flags & HAPTIC_FLAG_CANCEL) {
            haptics_all_off();
            continue;
        }
        ESP_LOGI(TAG, "haptic #%u q=[%u %u %u %u] %ums wf=%u flags=0x%02x",
                 (unsigned)cmd.command_sequence, cmd.q0, cmd.q1, cmd.q2, cmd.q3,
                 cmd.duration_ms, cmd.waveform, cmd.flags);
        haptics_pulse(cmd.q0, cmd.q1, cmd.q2, cmd.q3, cmd.duration_ms);
    }
}

/* ------------------------------------------------------------------ */
void app_main(void)
{
    ESP_ERROR_CHECK(haptics_init()); /* motors pinned OFF before anything else */

    /* IMU is optional: if it doesn't answer we still run (identity orientation,
     * SENSOR_FAULT flagged) so the Wi-Fi + haptic path keeps working. */
    if (imu_init() == ESP_OK) {
        imu_task_start();
        ESP_LOGI(TAG, "imu online - hold the paddle still for ~2 s to zero the gyro");
    } else {
        ESP_LOGW(TAG, "imu not found - sending identity orientation");
    }

    ESP_ERROR_CHECK(wifi_link_connect(UINT32_MAX));
    ESP_LOGI(TAG, "wifi up - starting telemetry (%d Hz) and haptic rx", TELEMETRY_HZ);

    xTaskCreate(telemetry_task, "telemetry", 4096, NULL, 5, NULL);
    xTaskCreate(haptic_rx_task, "haptic_rx", 4096, NULL, 6, NULL);
    xTaskCreate(status_task, "status", 3072, NULL, 3, NULL);
}
