idf_component_register(
    SRCS
        "src/rehab_scan_transfer.c"
        "src/rehab_scan_transfer_scan.c"
        "src/rehab_scan_transfer_multipart.c"
        "src/rehab_scan_transfer_http.c"
        "src/rehab_scan_transfer_contract.c"
        "src/rehab_scan_transfer_storage.c"
        "src/rehab_scan_transfer_console.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    PRIV_REQUIRES
        esp_http_client
        json
        mbedtls
        wifi_manager
        esp_timer
        freertos
        heap
        console
)
