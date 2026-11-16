idf_component_register(
    SRCS
        "src/rehab_target_follow.c"
        "src/rehab_target_follow_state.c"
        "src/rehab_target_follow_capabilities.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    REQUIRES
        rehab_car
        rehab_capture
        rehab_cloud_fusion
        console
        claw_cap
        json
        freertos
)
