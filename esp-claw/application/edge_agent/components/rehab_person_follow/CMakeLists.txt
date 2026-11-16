idf_component_register(
    SRCS
        "src/rehab_person_follow.c"
        "src/rehab_person_follow_state.c"
        "src/rehab_person_follow_mock.c"
        "src/rehab_person_follow_http.c"
        "src/rehab_person_follow_parser.c"
        "src/rehab_person_follow_selection.c"
        "src/rehab_person_follow_perception.c"
        "src/rehab_person_follow_snapshot.c"
        "src/rehab_person_follow_capabilities.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    REQUIRES
        rehab_car
        rehab_business_audio
        rehab_follow
        claw_cap
        json
        freertos
        esp_timer
        esp_http_client
        heap
)
