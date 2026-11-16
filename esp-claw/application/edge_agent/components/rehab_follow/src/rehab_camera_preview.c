// SPDX-License-Identifier: Apache-2.0

#include "rehab_follow_priv.h"

#include <inttypes.h>
#include <limits.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_jpeg_enc.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

typedef struct {
    uint8_t *data;
    size_t capacity;
    size_t length;
    uint64_t generation;
    int64_t timestamp_us;
    uint32_t lease_count;
    bool valid;
    bool encoding;
} preview_slot_t;

typedef struct {
    SemaphoreHandle_t lock;
    StaticSemaphore_t lock_storage;
    bool initialized;
    jpeg_enc_handle_t encoder;
    preview_slot_t slots[REHAB_FOLLOW_CAMERA_JPEG_SLOTS];
    int active_index;
    uint64_t publish_count;
    uint64_t drop_count;
    uint64_t error_count;
} preview_store_t;

static const char *TAG = "rehab_cam_jpeg";
static preview_store_t s_preview;

static bool preview_slots_idle_locked(void)
{
    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_JPEG_SLOTS; ++i) {
        if (s_preview.slots[i].lease_count != 0 || s_preview.slots[i].encoding) {
            return false;
        }
    }
    return true;
}

esp_err_t rehab_camera_preview_init(uint32_t width,
                                    uint32_t height,
                                    size_t compact_payload_bytes,
                                    bool rgb565_big_endian,
                                    uint8_t quality)
{
    if (width == 0 || height == 0 || compact_payload_bytes == 0 ||
        compact_payload_bytes > INT_MAX || quality == 0 || quality > 100) {
        return ESP_ERR_INVALID_ARG;
    }

    if (s_preview.lock == NULL) {
        s_preview.lock = xSemaphoreCreateMutexStatic(&s_preview.lock_storage);
        if (s_preview.lock == NULL) {
            return ESP_ERR_NO_MEM;
        }
        s_preview.active_index = -1;
    }

    xSemaphoreTake(s_preview.lock, portMAX_DELAY);
    if (s_preview.initialized) {
        xSemaphoreGive(s_preview.lock);
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreGive(s_preview.lock);

    preview_slot_t new_slots[REHAB_FOLLOW_CAMERA_JPEG_SLOTS] = {0};
    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_JPEG_SLOTS; ++i) {
        new_slots[i].data = heap_caps_malloc(compact_payload_bytes,
                                             MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (new_slots[i].data == NULL) {
            for (size_t j = 0; j < i; ++j) {
                heap_caps_free(new_slots[j].data);
            }
            return ESP_ERR_NO_MEM;
        }
        new_slots[i].capacity = compact_payload_bytes;
    }

    jpeg_enc_config_t encoder_config = DEFAULT_JPEG_ENC_CONFIG();
    encoder_config.width = (int)width;
    encoder_config.height = (int)height;
    encoder_config.src_type = rgb565_big_endian
                                  ? JPEG_PIXEL_FORMAT_RGB565_BE
                                  : JPEG_PIXEL_FORMAT_RGB565_LE;
    encoder_config.subsampling = JPEG_SUBSAMPLE_422;
    encoder_config.quality = quality;
    encoder_config.task_enable = false;

    jpeg_enc_handle_t encoder = NULL;
    jpeg_error_t jpeg_err = jpeg_enc_open(&encoder_config, &encoder);
    if (jpeg_err != JPEG_ERR_OK || encoder == NULL) {
        for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_JPEG_SLOTS; ++i) {
            heap_caps_free(new_slots[i].data);
        }
        ESP_LOGE(TAG, "CAM_JPEG_INIT failed jpeg_err=%d", (int)jpeg_err);
        return ESP_FAIL;
    }

    xSemaphoreTake(s_preview.lock, portMAX_DELAY);
    memcpy(s_preview.slots, new_slots, sizeof(new_slots));
    s_preview.encoder = encoder;
    s_preview.active_index = -1;
    s_preview.publish_count = 0;
    s_preview.drop_count = 0;
    s_preview.error_count = 0;
    s_preview.initialized = true;
    xSemaphoreGive(s_preview.lock);

    ESP_LOGI(TAG,
             "CAM_JPEG_SLOTS count=2 width=%" PRIu32 " height=%" PRIu32
             " byte_order=%s quality=%u slot_capacity=%u slots=%u",
             width,
             height,
             rgb565_big_endian ? "big_endian" : "little_endian",
             (unsigned)quality,
             (unsigned)compact_payload_bytes,
             (unsigned)REHAB_FOLLOW_CAMERA_JPEG_SLOTS);
    return ESP_OK;
}

esp_err_t rehab_camera_preview_encode(const uint8_t *rgb565,
                                      size_t length,
                                      uint64_t generation,
                                      int64_t timestamp_us)
{
    if (rgb565 == NULL || length == 0 || length > INT_MAX ||
        (((uintptr_t)rgb565) & 0x0fU) != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_preview.lock == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    xSemaphoreTake(s_preview.lock, portMAX_DELAY);
    if (!s_preview.initialized || s_preview.encoder == NULL) {
        xSemaphoreGive(s_preview.lock);
        return ESP_ERR_INVALID_STATE;
    }

    int target = (s_preview.active_index < 0) ? 0 : (1 - s_preview.active_index);
    preview_slot_t *slot = &s_preview.slots[target];
    if (slot->lease_count != 0 || slot->encoding ||
        target == s_preview.active_index) {
        ++s_preview.drop_count;
        xSemaphoreGive(s_preview.lock);
        return ESP_ERR_NOT_FINISHED;
    }
    slot->encoding = true;
    jpeg_enc_handle_t encoder = s_preview.encoder;
    uint8_t *output = slot->data;
    size_t output_capacity = slot->capacity;
    xSemaphoreGive(s_preview.lock);

    int output_length = 0;
    jpeg_error_t jpeg_err = jpeg_enc_process(encoder,
                                             rgb565,
                                             (int)length,
                                             output,
                                             (int)output_capacity,
                                             &output_length);

    xSemaphoreTake(s_preview.lock, portMAX_DELAY);
    slot->encoding = false;
    if (jpeg_err != JPEG_ERR_OK || output_length <= 0 ||
        (size_t)output_length > output_capacity) {
        ++s_preview.error_count;
        xSemaphoreGive(s_preview.lock);
        ESP_LOGE(TAG,
                 "CAM_JPEG_ENCODE failed jpeg_err=%d output_length=%d capacity=%u",
                 (int)jpeg_err,
                 output_length,
                 (unsigned)output_capacity);
        return ESP_FAIL;
    }

    slot->length = (size_t)output_length;
    slot->generation = generation;
    slot->timestamp_us = timestamp_us;
    slot->valid = true;
    s_preview.active_index = target;
    ++s_preview.publish_count;
    xSemaphoreGive(s_preview.lock);
    return ESP_OK;
}

esp_err_t rehab_follow_camera_preview_acquire(rehab_follow_preview_frame_t *frame)
{
    if (frame == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(frame, 0, sizeof(*frame));
    if (s_preview.lock == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    xSemaphoreTake(s_preview.lock, portMAX_DELAY);
    int index = s_preview.active_index;
    if (!s_preview.initialized || index < 0 ||
        index >= REHAB_FOLLOW_CAMERA_JPEG_SLOTS ||
        !s_preview.slots[index].valid || s_preview.slots[index].encoding ||
        s_preview.slots[index].lease_count == UINT32_MAX) {
        xSemaphoreGive(s_preview.lock);
        return ESP_ERR_NOT_FOUND;
    }

    preview_slot_t *slot = &s_preview.slots[index];
    ++slot->lease_count;
    frame->data = slot->data;
    frame->length = slot->length;
    frame->capacity = slot->capacity;
    frame->generation = slot->generation;
    frame->timestamp_us = slot->timestamp_us;
    frame->lease_token = (uint32_t)index + 1U;
    xSemaphoreGive(s_preview.lock);
    return ESP_OK;
}

void rehab_follow_camera_preview_release(rehab_follow_preview_frame_t *frame)
{
    if (frame == NULL || frame->lease_token == 0 || s_preview.lock == NULL) {
        return;
    }

    uint32_t index = frame->lease_token - 1U;
    xSemaphoreTake(s_preview.lock, portMAX_DELAY);
    if (index < REHAB_FOLLOW_CAMERA_JPEG_SLOTS) {
        preview_slot_t *slot = &s_preview.slots[index];
        if (slot->lease_count > 0 && slot->generation == frame->generation) {
            --slot->lease_count;
        }
    }
    xSemaphoreGive(s_preview.lock);
    memset(frame, 0, sizeof(*frame));
}

esp_err_t rehab_camera_preview_get_status(rehab_camera_preview_status_t *status)
{
    if (status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(status, 0, sizeof(*status));
    if (s_preview.lock == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    xSemaphoreTake(s_preview.lock, portMAX_DELAY);
    status->publish_count = s_preview.publish_count;
    status->drop_count = s_preview.drop_count;
    status->error_count = s_preview.error_count;
    if (s_preview.initialized) {
        status->slot_capacity_each = s_preview.slots[0].capacity;
        for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_JPEG_SLOTS; ++i) {
            status->lease_counts[i] = s_preview.slots[i].lease_count;
        }
        if (s_preview.active_index >= 0) {
            const preview_slot_t *slot = &s_preview.slots[s_preview.active_index];
            status->generation = slot->generation;
            status->timestamp_us = slot->timestamp_us;
            status->active_length = slot->length;
        }
    }
    xSemaphoreGive(s_preview.lock);
    return ESP_OK;
}

esp_err_t rehab_camera_preview_wait_idle(uint32_t timeout_ms)
{
    if (s_preview.lock == NULL) {
        return ESP_OK;
    }

    TickType_t start = xTaskGetTickCount();
    TickType_t timeout_ticks = pdMS_TO_TICKS(timeout_ms);
    do {
        xSemaphoreTake(s_preview.lock, portMAX_DELAY);
        bool idle = preview_slots_idle_locked();
        xSemaphoreGive(s_preview.lock);
        if (idle) {
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    } while ((xTaskGetTickCount() - start) < timeout_ticks);
    return ESP_ERR_TIMEOUT;
}

esp_err_t rehab_camera_preview_deinit(void)
{
    if (s_preview.lock == NULL) {
        return ESP_OK;
    }

    xSemaphoreTake(s_preview.lock, portMAX_DELAY);
    if (!s_preview.initialized) {
        xSemaphoreGive(s_preview.lock);
        return ESP_OK;
    }
    if (!preview_slots_idle_locked()) {
        xSemaphoreGive(s_preview.lock);
        return ESP_ERR_INVALID_STATE;
    }

    jpeg_enc_handle_t encoder = s_preview.encoder;
    uint8_t *buffers[REHAB_FOLLOW_CAMERA_JPEG_SLOTS] = {
        s_preview.slots[0].data,
        s_preview.slots[1].data,
    };
    memset(s_preview.slots, 0, sizeof(s_preview.slots));
    s_preview.encoder = NULL;
    s_preview.active_index = -1;
    s_preview.initialized = false;
    xSemaphoreGive(s_preview.lock);

    if (encoder != NULL) {
        (void)jpeg_enc_close(encoder);
    }
    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_JPEG_SLOTS; ++i) {
        heap_caps_free(buffers[i]);
    }
    return ESP_OK;
}
