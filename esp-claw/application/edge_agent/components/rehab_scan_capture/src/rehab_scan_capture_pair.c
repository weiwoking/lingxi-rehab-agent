// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_capture_priv.h"

#include <limits.h>
#include <string.h>

#include "rehab_follow.h"

static bool tof_frame_valid(const rehab_tof_extended_frame_t *frame)
{
    if (frame == NULL || frame->sequence == 0 || frame->timestamp_us <= 0 ||
        frame->resolution_width != REHAB_SCAN_TOF_WIDTH ||
        frame->resolution_height != REHAB_SCAN_TOF_HEIGHT) {
        return false;
    }
    for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
        if (frame->distance_mm[i] < 0) {
            return false;
        }
    }
    return true;
}

static bool camera_frame_valid(const rehab_follow_camera_raw_frame_t *frame)
{
    return frame != NULL && frame->data != NULL &&
           frame->width == REHAB_SCAN_IMAGE_WIDTH &&
           frame->height == REHAB_SCAN_IMAGE_HEIGHT &&
           frame->stride == REHAB_SCAN_IMAGE_STRIDE &&
           frame->length == REHAB_SCAN_IMAGE_BYTES &&
           frame->pixel_format == REHAB_FOLLOW_CAMERA_PIXEL_FORMAT_RGB565 &&
           frame->byte_order == REHAB_FOLLOW_CAMERA_BYTE_ORDER_LITTLE_ENDIAN &&
           frame->timestamp_us > 0 && frame->lease_token != 0;
}

static uint64_t timestamp_delta(int64_t first, int64_t second)
{
    return first >= second ? (uint64_t)(first - second)
                           : (uint64_t)(second - first);
}

esp_err_t rehab_scan_capture_pair_frame(rehab_scan_capture_handle_t handle,
                                        uint32_t sequence,
                                        uint8_t *image_buffer,
                                        rehab_scan_frame_record_t *out_record)
{
    if (handle == NULL || image_buffer == NULL || out_record == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    rehab_tof_extended_frame_t before = {0};
    rehab_tof_extended_frame_t after = {0};
    rehab_follow_camera_raw_frame_t camera = {0};
    bool camera_acquired = false;
    int64_t camera_timestamp_us = 0;
    esp_err_t err = rehab_tof_extended_get_latest(&before, 0);
    if (err != ESP_OK || !tof_frame_valid(&before)) {
        return err == ESP_OK ? ESP_ERR_INVALID_RESPONSE : err;
    }

    err = rehab_follow_camera_raw_snapshot_acquire(&camera,
                                                    handle->config.camera_timeout_ms);
    if (err != ESP_OK) {
        return err;
    }
    camera_acquired = true;
    if (!camera_frame_valid(&camera)) {
        err = ESP_ERR_INVALID_RESPONSE;
        goto release_camera;
    }

    memcpy(image_buffer, camera.data, REHAB_SCAN_IMAGE_BYTES);
    camera_timestamp_us = camera.timestamp_us;

release_camera:
    if (camera_acquired) {
        rehab_follow_camera_raw_snapshot_release(&camera);
        camera_acquired = false;
    }
    if (err != ESP_OK) {
        return err;
    }
    if (rehab_scan_capture_abort_requested(handle)) {
        return ESP_ERR_INVALID_STATE;
    }

    err = rehab_tof_extended_wait_next(&after,
                                       before.sequence,
                                       handle->config.tof_after_timeout_ms);
    if (err != ESP_OK) {
        return err;
    }
    if (!tof_frame_valid(&after) || after.sequence <= before.sequence) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    if (rehab_scan_capture_abort_requested(handle)) {
        return ESP_ERR_INVALID_STATE;
    }

    uint64_t before_delta = timestamp_delta(camera_timestamp_us,
                                            before.timestamp_us);
    uint64_t after_delta = timestamp_delta(camera_timestamp_us,
                                           after.timestamp_us);
    const rehab_tof_extended_frame_t *selected =
        after_delta <= before_delta ? &after : &before;

    memset(out_record, 0, sizeof(*out_record));
    out_record->sequence = sequence;
    out_record->camera_timestamp_us = camera_timestamp_us;
    out_record->tof = *selected;
    out_record->pair_delta_us = timestamp_delta(out_record->camera_timestamp_us,
                                                selected->timestamp_us);
    out_record->provider_before_sequence = before.sequence;
    out_record->provider_after_sequence = after.sequence;
    return ESP_OK;
}
