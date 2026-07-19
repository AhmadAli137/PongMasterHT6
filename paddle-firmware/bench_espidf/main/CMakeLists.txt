idf_component_register(SRCS "bench_main.c" "wifi_link.c"
                       INCLUDE_DIRS "."
                       PRIV_REQUIRES esp_driver_ledc nvs_flash esp_wifi esp_netif esp_event)
