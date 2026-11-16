idf_component_register(
    SRCS
        "src/rehab_cloud_fusion_client.c"
        "src/rehab_cloud_fusion_parser.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    REQUIRES
        rehab_capture
        esp_http_client
        cap_time
        wifi_manager
        esp_timer
        freertos
        heap
        app_config
)
