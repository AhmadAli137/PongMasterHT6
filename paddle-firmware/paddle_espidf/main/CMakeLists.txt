idf_component_register(SRCS "paddle_main.c" "wifi_link.c" "haptics.c"
                       INCLUDE_DIRS "."
                       PRIV_REQUIRES nvs_flash esp_wifi esp_netif esp_event esp_driver_ledc esp_timer lwip)
