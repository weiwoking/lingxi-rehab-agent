// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "rehab_follow.h"

typedef struct {
    uint64_t generation;
    uint64_t publish_count;
    uint64_t drop_count;
    uint64_t error_count;
    int64_t timestamp_us;
    size_t slot_capacity_each;
    size_t active_length;
    uint32_t lease_counts[REHAB_FOLLOW_CAMERA_JPEG_SLOTS];
} rehab_camera_preview_status_t;

esp_err_t rehab_camera_preview_init(uint32_t width,
                                    uint32_t height,
                                    size_t compact_payload_bytes,
                                    bool rgb565_big_endian,
                                    uint8_t quality);
esp_err_t rehab_camera_preview_encode(const uint8_t *rgb565,
                                      size_t length,
                                      uint64_t generation,
                                      int64_t timestamp_us);
esp_err_t rehab_camera_preview_get_status(rehab_camera_preview_status_t *status);
esp_err_t rehab_camera_preview_wait_idle(uint32_t timeout_ms);
esp_err_t rehab_camera_preview_deinit(void);
