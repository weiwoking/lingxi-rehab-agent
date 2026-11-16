idf_component_register(
    SRCS "rehab_car_capabilities.c" "rehab_car.c"
    INCLUDE_DIRS "include"
    REQUIRES console lwip claw_cap json esp_driver_uart
)
