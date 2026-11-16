// SPDX-License-Identifier: Apache-2.0

#include <stdlib.h>

#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "platform.h"

#define VL53L5CX_I2C_TIMEOUT_MS   2000U
#define VL53L5CX_I2C_CHUNK_SIZE   1024U
#define VL53L5CX_ST_DEFAULT_ADDR  0x52U
#define VL53L5CX_ESP_DEFAULT_ADDR 0x29U

static const char *TAG = "rehab_tof_i2c";

static void platform_record_error(VL53L5CX_Platform *platform, esp_err_t err)
{
    if (platform == NULL) {
        return;
    }
    platform->last_io_error = err;
    if (err != ESP_OK && platform->first_io_error == ESP_OK) {
        platform->first_io_error = err;
    }
}

static uint16_t vl53l5cx_to_esp_7bit_addr(uint16_t uld_addr)
{
    if (uld_addr == VL53L5CX_ST_DEFAULT_ADDR || uld_addr == VL53L5CX_ESP_DEFAULT_ADDR) {
        return VL53L5CX_ESP_DEFAULT_ADDR;
    }
    return uld_addr > 0x7fU ? (uld_addr >> 1U) : uld_addr;
}

static esp_err_t ensure_device_handle(VL53L5CX_Platform *platform)
{
    ESP_RETURN_ON_FALSE(platform != NULL, ESP_ERR_INVALID_ARG, TAG, "platform is NULL");
    ESP_RETURN_ON_FALSE(platform->bus_handle != NULL, ESP_ERR_INVALID_STATE, TAG,
                        "I2C bus is not initialized");

    uint16_t address = vl53l5cx_to_esp_7bit_addr(platform->address);
    ESP_RETURN_ON_FALSE(address <= 0x7fU, ESP_ERR_INVALID_ARG, TAG,
                        "invalid I2C address 0x%02x", address);
    if (platform->dev_handle != NULL && platform->active_7bit_address == address) {
        return ESP_OK;
    }
    if (platform->dev_handle != NULL) {
        esp_err_t err = i2c_master_bus_rm_device(platform->dev_handle);
        if (err != ESP_OK) {
            return err;
        }
        platform->dev_handle = NULL;
    }

    const i2c_device_config_t config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = address,
        .scl_speed_hz = platform->scl_speed_hz,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(platform->bus_handle,
                                                   &config,
                                                   &platform->dev_handle),
                        TAG, "add VL53L5CX at 0x%02x failed", address);
    platform->active_7bit_address = address;
    return ESP_OK;
}

esp_err_t vl53l5cx_platform_init(VL53L5CX_Platform *platform,
                                 i2c_master_bus_handle_t bus_handle,
                                 uint16_t i2c_address,
                                 uint32_t scl_speed_hz)
{
    ESP_RETURN_ON_FALSE(platform != NULL, ESP_ERR_INVALID_ARG, TAG, "platform is NULL");
    ESP_RETURN_ON_FALSE(scl_speed_hz == 400000U || scl_speed_hz == 1000000U,
                        ESP_ERR_INVALID_ARG, TAG, "unsupported I2C frequency");
    memset(platform, 0, sizeof(*platform));
    platform->address = i2c_address;
    platform->bus_handle = bus_handle;
    platform->scl_speed_hz = scl_speed_hz;
    platform->timeout_ms = VL53L5CX_I2C_TIMEOUT_MS;
    platform->last_io_error = ESP_OK;
    platform->first_io_error = ESP_OK;
    esp_err_t err = ensure_device_handle(platform);
    platform_record_error(platform, err);
    return err;
}

esp_err_t vl53l5cx_platform_deinit(VL53L5CX_Platform *platform)
{
    if (platform == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (platform->dev_handle != NULL) {
        esp_err_t err = i2c_master_bus_rm_device(platform->dev_handle);
        platform_record_error(platform, err);
        if (err != ESP_OK) {
            return err;
        }
        platform->dev_handle = NULL;
    }
    platform->active_7bit_address = 0U;
    return ESP_OK;
}

void vl53l5cx_platform_begin_operation(VL53L5CX_Platform *platform)
{
    if (platform != NULL) {
        platform->last_io_error = ESP_OK;
        platform->first_io_error = ESP_OK;
    }
}

esp_err_t vl53l5cx_platform_operation_error(
    const VL53L5CX_Platform *platform)
{
    return platform != NULL ? platform->first_io_error : ESP_ERR_INVALID_ARG;
}

uint8_t RdByte(VL53L5CX_Platform *platform, uint16_t address, uint8_t *value)
{
    return RdMulti(platform, address, value, 1U);
}

uint8_t WrByte(VL53L5CX_Platform *platform, uint16_t address, uint8_t value)
{
    return WrMulti(platform, address, &value, 1U);
}

uint8_t RdMulti(VL53L5CX_Platform *platform,
                uint16_t address,
                uint8_t *values,
                uint32_t size)
{
    if (platform == NULL || values == NULL || size == 0U) {
        platform_record_error(platform, ESP_ERR_INVALID_ARG);
        return 1U;
    }
    esp_err_t handle_err = ensure_device_handle(platform);
    if (handle_err != ESP_OK) {
        platform_record_error(platform, handle_err);
        ++platform->read_error_count;
        return 1U;
    }
    uint8_t register_address[2] = {
        (uint8_t)(address >> 8U),
        (uint8_t)(address & 0xffU),
    };
    int64_t started_us = esp_timer_get_time();
    esp_err_t err = i2c_master_transmit_receive(platform->dev_handle,
                                                  register_address,
                                                 sizeof(register_address),
                                                 values,
                                                  size,
                                                  (int)platform->timeout_ms);
    platform->last_read_duration_us =
        (uint32_t)(esp_timer_get_time() - started_us);
    platform->last_read_size = err == ESP_OK ? size : 0U;
    platform_record_error(platform, err);
    if (err != ESP_OK) {
        ++platform->read_error_count;
        ESP_LOGE(TAG, "read reg=0x%04x size=%lu failed: %s",
                 address, (unsigned long)size, esp_err_to_name(err));
    }
    return err == ESP_OK ? 0U : 1U;
}

uint8_t WrMulti(VL53L5CX_Platform *platform,
                uint16_t address,
                uint8_t *values,
                uint32_t size)
{
    if (platform == NULL || values == NULL || size == 0U) {
        platform_record_error(platform, ESP_ERR_INVALID_ARG);
        return 1U;
    }
    esp_err_t handle_err = ensure_device_handle(platform);
    if (handle_err != ESP_OK) {
        platform_record_error(platform, handle_err);
        ++platform->write_error_count;
        return 1U;
    }

    for (uint32_t offset = 0; offset < size;) {
        uint32_t chunk = size - offset > VL53L5CX_I2C_CHUNK_SIZE
                             ? VL53L5CX_I2C_CHUNK_SIZE
                             : size - offset;
        uint8_t *buffer = malloc(chunk + 2U);
        if (buffer == NULL) {
            platform_record_error(platform, ESP_ERR_NO_MEM);
            return 1U;
        }
        uint16_t register_address = (uint16_t)(address + offset);
        buffer[0] = (uint8_t)(register_address >> 8U);
        buffer[1] = (uint8_t)(register_address & 0xffU);
        memcpy(&buffer[2], &values[offset], chunk);
        esp_err_t err = i2c_master_transmit(platform->dev_handle,
                                             buffer,
                                             chunk + 2U,
                                             (int)platform->timeout_ms);
        free(buffer);
        platform_record_error(platform, err);
        if (err != ESP_OK) {
            ++platform->write_error_count;
            ESP_LOGE(TAG, "write reg=0x%04x size=%lu failed: %s",
                     register_address, (unsigned long)chunk, esp_err_to_name(err));
            return 1U;
        }
        offset += chunk;
    }
    return 0U;
}

uint8_t Reset_Sensor(VL53L5CX_Platform *platform)
{
    (void)platform;
    /* The SATEL module LPn/PWREN pins are tied to 3V3 in this integration. */
    return 0U;
}

void SwapBuffer(uint8_t *buffer, uint16_t size)
{
    for (uint16_t i = 0; i < size; i += 4U) {
        uint8_t tmp = buffer[i];
        buffer[i] = buffer[i + 3U];
        buffer[i + 3U] = tmp;
        tmp = buffer[i + 1U];
        buffer[i + 1U] = buffer[i + 2U];
        buffer[i + 2U] = tmp;
    }
}

uint8_t WaitMs(VL53L5CX_Platform *platform, uint32_t time_ms)
{
    (void)platform;
    vTaskDelay(pdMS_TO_TICKS(time_ms));
    return 0U;
}
