idf_component_register(
    SRCS
        "src/rehab_scan_capture.c"
        "src/rehab_scan_capture_pair.c"
        "src/rehab_scan_capture_contract.c"
        "src/rehab_scan_capture_storage.c"
        "src/rehab_scan_capture_console.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    PRIV_REQUIRES
        rehab_follow
        rehab_tof
        json
        esp_timer
        freertos
        heap
        console
)
