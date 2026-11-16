set(src_dirs
            COMM
            COMMON
            IMU
            ADS1292
            LED)
 
set(include_dirs
            COMM
            COMMON
            IMU
            ADS1292
            LED)
 
set(requires
            esp_timer
            esp_common
            log
            driver
            esp_wifi
            esp_event
            nvs_flash
            lwip)
 
idf_component_register(SRC_DIRS ${src_dirs}
                      INCLUDE_DIRS ${include_dirs} REQUIRES ${requires})
 
component_compile_options(-ffast-math -O3 -Wno-error=format -Wno-format)