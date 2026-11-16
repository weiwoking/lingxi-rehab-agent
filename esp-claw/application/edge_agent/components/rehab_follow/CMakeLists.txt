idf_component_register(
    SRCS
        "src/rehab_camera_provider.c"
        "src/rehab_camera_preview.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    REQUIRES
        esp_new_jpeg
        esp_timer
        claw_video
        freertos
        heap
)
