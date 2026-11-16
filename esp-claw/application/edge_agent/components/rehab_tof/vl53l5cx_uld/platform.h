#ifndef VL53L5CX_ESP_IDF_PLATFORM_H
#define VL53L5CX_ESP_IDF_PLATFORM_H

#include <stdint.h>
#include <string.h>

#include "driver/i2c_master.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct
{
    uint16_t address;
    i2c_master_bus_handle_t bus_handle;
    i2c_master_dev_handle_t dev_handle;
    uint16_t active_7bit_address;
    uint32_t scl_speed_hz;
    uint32_t timeout_ms;
    uint32_t last_read_size;
    uint32_t last_read_duration_us;
    esp_err_t last_io_error;
    esp_err_t first_io_error;
    uint32_t read_error_count;
    uint32_t write_error_count;
} VL53L5CX_Platform;

#define VL53L5CX_NB_TARGET_PER_ZONE 1U

#define VL53L5CX_DISABLE_AMBIENT_PER_SPAD
#define VL53L5CX_DISABLE_NB_SPADS_ENABLED
#define VL53L5CX_DISABLE_SIGNAL_PER_SPAD
#define VL53L5CX_DISABLE_REFLECTANCE_PERCENT
#define VL53L5CX_DISABLE_MOTION_INDICATOR

esp_err_t vl53l5cx_platform_init(
        VL53L5CX_Platform *p_platform,
        i2c_master_bus_handle_t bus_handle,
        uint16_t i2c_address,
        uint32_t scl_speed_hz);

esp_err_t vl53l5cx_platform_deinit(
        VL53L5CX_Platform *p_platform);

void vl53l5cx_platform_begin_operation(
        VL53L5CX_Platform *p_platform);

esp_err_t vl53l5cx_platform_operation_error(
        const VL53L5CX_Platform *p_platform);

uint8_t RdByte(
        VL53L5CX_Platform *p_platform,
        uint16_t RegisterAdress,
        uint8_t *p_value);

uint8_t WrByte(
        VL53L5CX_Platform *p_platform,
        uint16_t RegisterAdress,
        uint8_t value);

uint8_t RdMulti(
        VL53L5CX_Platform *p_platform,
        uint16_t RegisterAdress,
        uint8_t *p_values,
        uint32_t size);

uint8_t WrMulti(
        VL53L5CX_Platform *p_platform,
        uint16_t RegisterAdress,
        uint8_t *p_values,
        uint32_t size);

uint8_t Reset_Sensor(
        VL53L5CX_Platform *p_platform);

void SwapBuffer(
        uint8_t *buffer,
        uint16_t size);

uint8_t WaitMs(
        VL53L5CX_Platform *p_platform,
        uint32_t TimeMs);

#ifdef __cplusplus
}
#endif

#endif
