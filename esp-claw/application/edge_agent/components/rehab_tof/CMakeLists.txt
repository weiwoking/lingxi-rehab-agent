idf_component_register(
    SRCS
        "src/rehab_tof_provider.c"
        "src/rehab_tof_platform.c"
        "src/rehab_tof_frame_core.c"
        "src/rehab_tof_startup_core.c"
        "vl53l5cx_uld/vl53l5cx_api.c"
    INCLUDE_DIRS
        "include"
    PRIV_INCLUDE_DIRS
        "src"
        "vl53l5cx_uld"
    REQUIRES
        esp_driver_i2c
        esp_timer
        freertos
        log
)

target_compile_options(${COMPONENT_LIB} PRIVATE
    -Wno-unused-parameter
    -Wno-sign-compare
)
