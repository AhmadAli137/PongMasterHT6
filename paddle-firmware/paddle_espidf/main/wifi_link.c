#include "wifi_link.h"

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "secrets.h"

static const char *TAG = "wifi_link";
static EventGroupHandle_t s_events;
#define CONNECTED_BIT BIT0
#define FAIL_BIT      BIT1
static volatile bool s_up = false;
static volatile uint32_t s_reconnects = 0;

static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = (wifi_event_sta_disconnected_t *)data;
        s_up = false;
        xEventGroupClearBits(s_events, CONNECTED_BIT);
        xEventGroupSetBits(s_events, FAIL_BIT);
        /* reason codes: 15=4WAY_HANDSHAKE_TIMEOUT/bad password, 201=NO_AP_FOUND,
         * 2/8=AUTH/ASSOC leave. Band-steering routers (see SketchBot notes)
         * reject the first auth on a band within ~10 ms and accept a retry. */
        ESP_LOGW(TAG, "disconnected (reason=%d) - reconnecting", d ? d->reason : -1);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ev = (ip_event_got_ip_t *)data;
        s_reconnects++;
        wifi_ap_record_t ap = { 0 };
        esp_wifi_sta_get_ap_info(&ap);
        ESP_LOGI(TAG, "========================================");
        ESP_LOGI(TAG, " WIFI UP  (connection #%u)", (unsigned)s_reconnects);
        ESP_LOGI(TAG, "   SSID    : %s", (char *)ap.ssid);
        ESP_LOGI(TAG, "   IP      : " IPSTR, IP2STR(&ev->ip_info.ip));
        ESP_LOGI(TAG, "   gateway : " IPSTR, IP2STR(&ev->ip_info.gw));
        ESP_LOGI(TAG, "   channel : %d   RSSI: %d dBm", ap.primary, ap.rssi);
        ESP_LOGI(TAG, "========================================");
        s_up = true;
        xEventGroupSetBits(s_events, CONNECTED_BIT);
    }
    (void)arg;
}

esp_err_t wifi_link_connect(uint32_t timeout_ms)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    s_events = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &on_wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &on_wifi_event, NULL));

    wifi_config_t wc = { 0 };
    strncpy((char *)wc.sta.ssid, WIFI_SSID, sizeof(wc.sta.ssid) - 1);
    strncpy((char *)wc.sta.password, WIFI_PASS, sizeof(wc.sta.password) - 1);
    wc.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* CRITICAL for this project, twice over:
     * 1. Modem power-save adds 100+ ms latency to INCOMING packets — haptic
     *    commands must land in <20 ms (spec §3.1).
     * 2. It also drops average current to ~20-40 mA, which lets power-bank
     *    boost boards (the DAOKI) decide we're unplugged and shut us off.
     * PS_NONE holds ~100 mA+ with Wi-Fi up: low latency AND keep-alive. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    ESP_LOGI(TAG, "connecting to '%s'...", WIFI_SSID);
    TickType_t ticks = (timeout_ms == UINT32_MAX) ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms);
    EventBits_t bits = xEventGroupWaitBits(s_events, CONNECTED_BIT, pdFALSE, pdTRUE, ticks);
    return (bits & CONNECTED_BIT) ? ESP_OK : ESP_ERR_TIMEOUT;
}

bool wifi_link_up(void)
{
    return s_up;
}

int wifi_link_rssi(void)
{
    if (!s_up) return 0;
    wifi_ap_record_t ap = { 0 };
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) return 0;
    return ap.rssi;
}

uint32_t wifi_link_reconnects(void)
{
    return s_reconnects;
}
