idf_component_register(
    SRCS
        "src/rehab_environment_scan.c"
        "src/rehab_environment_scan_capabilities.c"
    INCLUDE_DIRS "include"
    REQUIRES
        claw_cap
        json
        rehab_business_audio
        rehab_car
        rehab_scan_capture
        rehab_scan_transfer
    PRIV_REQUIRES
        console
        freertos
        log
        rehab_follow
)
