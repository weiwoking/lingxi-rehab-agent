idf_component_register(
    SRCS
        "src/rehab_ui.c"
        "src/rehab_ui_environment_scan_lua.c"
        "src/rehab_ui_person_follow_lua.c"
        "src/rehab_ui_task_decision_lua.c"
    INCLUDE_DIRS "include"
    REQUIRES
        rehab_environment_scan
        rehab_person_follow
        rehab_task_decision
    PRIV_REQUIRES
        cap_lua
        claw_core
        lua_module_image
        json
        log
)
