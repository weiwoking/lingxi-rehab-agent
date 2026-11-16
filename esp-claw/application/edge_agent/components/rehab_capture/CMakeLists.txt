idf_component_register(
    SRCS
        "src/rehab_capture_core.c"
        "src/rehab_capture_packet.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    REQUIRES rehab_follow rehab_tof esp_timer freertos heap
)

