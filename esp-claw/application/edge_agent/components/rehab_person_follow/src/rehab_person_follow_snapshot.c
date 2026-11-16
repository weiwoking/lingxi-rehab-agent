// SPDX-License-Identifier: Apache-2.0
#include "rehab_person_follow_priv.h"

#include <inttypes.h>
#include <string.h>

#include "esp_log.h"

static const char *TAG = "person_follow_snap";

static bool snapshot_lock(rehab_person_follow_handle_t handle)
{
    return handle != NULL && handle->lock != NULL &&
           xSemaphoreTake(handle->lock, portMAX_DELAY) == pdTRUE;
}

static void snapshot_unlock(rehab_person_follow_handle_t handle)
{
    xSemaphoreGive(handle->lock);
}

static uint64_t next_sequence(uint64_t sequence)
{
    ++sequence;
    return sequence == 0U ? 1U : sequence;
}

const char *rehab_person_follow_snapshot_image_format_name(
    rehab_person_follow_snapshot_image_format_t format)
{
    return format == REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_JPEG
        ? "JPEG" : "NONE";
}

esp_err_t rehab_person_follow_snapshot_stage(
    rehab_person_follow_handle_t handle,
    uint32_t worker_generation,
    const uint8_t *jpeg,
    size_t jpeg_length,
    uint64_t camera_generation,
    int64_t capture_timestamp_us,
    uint32_t *out_stage_token)
{
    if (handle == NULL || jpeg == NULL || jpeg_length == 0U ||
        jpeg_length > REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_MAX ||
        capture_timestamp_us < 0 || out_stage_token == NULL) {
        return jpeg_length > REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_MAX
            ? ESP_ERR_INVALID_SIZE : ESP_ERR_INVALID_ARG;
    }
    *out_stage_token = 0U;

    int target = -1;
    if (!snapshot_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (!handle->initialized || !handle->running ||
        handle->cancel_requested || handle->generation != worker_generation) {
        snapshot_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    for (int i = 0; i < (int)PERSON_FOLLOW_SNAPSHOT_SLOTS; ++i) {
        rehab_person_follow_snapshot_slot_t *slot = &handle->snapshot_slots[i];
        if (i != handle->snapshot_active_index && slot->lease_count == 0U &&
            !slot->staging && !slot->staged) {
            target = i;
            slot->staging = true;
            slot->staged_worker_generation = worker_generation;
            break;
        }
    }
    snapshot_unlock(handle);
    if (target < 0) {
        return ESP_ERR_NOT_FINISHED;
    }

    rehab_person_follow_snapshot_slot_t *slot = &handle->snapshot_slots[target];
    memcpy(slot->data, jpeg, jpeg_length);

    if (!snapshot_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    bool current = slot->staging &&
                   slot->staged_worker_generation == worker_generation &&
                   handle->running && !handle->cancel_requested &&
                   handle->generation == worker_generation;
    if (!current) {
        slot->staging = false;
        slot->staged = false;
        slot->length = 0U;
        snapshot_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    slot->length = jpeg_length;
    slot->camera_generation = camera_generation;
    slot->capture_timestamp_us = capture_timestamp_us;
    slot->staging = false;
    slot->staged = true;
    *out_stage_token = (uint32_t)target + 1U;
    snapshot_unlock(handle);
    return ESP_OK;
}

void rehab_person_follow_snapshot_discard_stage(
    rehab_person_follow_handle_t handle,
    uint32_t stage_token)
{
    if (handle == NULL || stage_token == 0U ||
        stage_token > PERSON_FOLLOW_SNAPSHOT_SLOTS || !snapshot_lock(handle)) {
        return;
    }
    rehab_person_follow_snapshot_slot_t *slot =
        &handle->snapshot_slots[stage_token - 1U];
    slot->staging = false;
    slot->staged = false;
    slot->length = 0U;
    snapshot_unlock(handle);
}

esp_err_t rehab_person_follow_snapshot_publish(
    rehab_person_follow_handle_t handle,
    uint32_t worker_generation,
    uint32_t stage_token,
    const rehab_person_follow_snapshot_info_t *info)
{
    if (handle == NULL || info == NULL ||
        stage_token > PERSON_FOLLOW_SNAPSHOT_SLOTS) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!snapshot_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (!handle->initialized || !handle->running ||
        handle->cancel_requested || handle->generation != worker_generation) {
        if (stage_token != 0U) {
            rehab_person_follow_snapshot_slot_t *slot =
                &handle->snapshot_slots[stage_token - 1U];
            slot->staging = false;
            slot->staged = false;
            slot->length = 0U;
        }
        snapshot_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }

    uint64_t sequence = next_sequence(handle->snapshot_sequence);
    handle->snapshot_sequence = sequence;
    handle->snapshot_info = *info;
    handle->snapshot_info.snapshot_seq = sequence;
    handle->snapshot_info.valid = true;
    handle->snapshot_info.worker_generation = worker_generation;
    handle->snapshot_info.cancel_requested = false;
    handle->snapshot_info.image_current = false;
    handle->snapshot_info.image_format =
        REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_NONE;
    handle->snapshot_info.width = PERSON_FOLLOW_IMAGE_WIDTH;
    handle->snapshot_info.height = PERSON_FOLLOW_IMAGE_HEIGHT;
    handle->snapshot_info.image_length = 0U;

    if (stage_token != 0U) {
        int index = (int)stage_token - 1;
        rehab_person_follow_snapshot_slot_t *slot =
            &handle->snapshot_slots[index];
        if (slot->staged && !slot->staging && slot->length > 0U &&
            slot->staged_worker_generation == worker_generation) {
            slot->staged = false;
            slot->valid = true;
            slot->image_snapshot_seq = sequence;
            handle->snapshot_active_index = index;
            handle->snapshot_info.image_current = true;
            handle->snapshot_info.image_snapshot_seq = sequence;
            handle->snapshot_info.image_format =
                REHAB_PERSON_FOLLOW_SNAPSHOT_IMAGE_JPEG;
            handle->snapshot_info.image_length = slot->length;
            handle->snapshot_info.camera_generation = slot->camera_generation;
            handle->snapshot_info.capture_timestamp_us =
                slot->capture_timestamp_us;
        } else {
            slot->staging = false;
            slot->staged = false;
            slot->length = 0U;
        }
    }

    if (!handle->snapshot_info.image_current &&
        handle->snapshot_active_index >= 0) {
        rehab_person_follow_snapshot_slot_t *active =
            &handle->snapshot_slots[handle->snapshot_active_index];
        if (active->valid) {
            handle->snapshot_info.image_snapshot_seq =
                active->image_snapshot_seq;
        }
    }
    rehab_person_follow_snapshot_info_t published = handle->snapshot_info;
    snapshot_unlock(handle);

    ESP_LOGI(TAG,
             "PF_SNAPSHOT seq=%" PRIu64 " image_current=%u image_seq=%" PRIu64
             " jpeg_bytes=%u selected=%u zone=%s planned=%s actual=%s error_stage=%s",
             published.snapshot_seq, published.image_current ? 1U : 0U,
             published.image_snapshot_seq, (unsigned)published.image_length,
             published.selected_valid ? 1U : 0U,
             rehab_person_follow_target_zone_name(published.zone),
             rehab_person_follow_action_name(published.planned_action),
             rehab_person_follow_action_name(published.actual_action),
             published.error_stage[0] ? published.error_stage : "none");
    return ESP_OK;
}

esp_err_t rehab_person_follow_snapshot_acquire(
    rehab_person_follow_handle_t handle,
    uint64_t expected_snapshot_seq,
    rehab_person_follow_snapshot_frame_t *out_frame)
{
    if (handle == NULL || expected_snapshot_seq == 0U || out_frame == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_frame, 0, sizeof(*out_frame));
    if (!snapshot_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    int index = handle->snapshot_active_index;
    if (!handle->snapshot_info.valid ||
        !handle->snapshot_info.image_current ||
        handle->snapshot_info.snapshot_seq != expected_snapshot_seq ||
        index < 0 || index >= (int)PERSON_FOLLOW_SNAPSHOT_SLOTS) {
        snapshot_unlock(handle);
        return ESP_ERR_NOT_FOUND;
    }
    rehab_person_follow_snapshot_slot_t *slot = &handle->snapshot_slots[index];
    if (!slot->valid || slot->staging || slot->staged ||
        slot->image_snapshot_seq != expected_snapshot_seq ||
        slot->length == 0U || slot->lease_count == UINT32_MAX) {
        snapshot_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    ++slot->lease_count;
    out_frame->info = handle->snapshot_info;
    out_frame->image_data = slot->data;
    out_frame->image_lease_token = (uint32_t)index + 1U;
    snapshot_unlock(handle);
    return ESP_OK;
}

void rehab_person_follow_snapshot_release(
    rehab_person_follow_handle_t handle,
    rehab_person_follow_snapshot_frame_t *frame)
{
    if (handle == NULL || frame == NULL || frame->image_lease_token == 0U ||
        frame->image_lease_token > PERSON_FOLLOW_SNAPSHOT_SLOTS ||
        !snapshot_lock(handle)) {
        return;
    }
    rehab_person_follow_snapshot_slot_t *slot =
        &handle->snapshot_slots[frame->image_lease_token - 1U];
    if (slot->lease_count > 0U &&
        slot->image_snapshot_seq == frame->info.snapshot_seq &&
        slot->data == frame->image_data) {
        --slot->lease_count;
    }
    snapshot_unlock(handle);
    memset(frame, 0, sizeof(*frame));
}

void rehab_person_follow_snapshot_reset_for_start(
    rehab_person_follow_handle_t handle)
{
    if (handle == NULL) {
        return;
    }
    memset(&handle->snapshot_info, 0, sizeof(handle->snapshot_info));
    handle->snapshot_active_index = -1;
    for (size_t i = 0; i < PERSON_FOLLOW_SNAPSHOT_SLOTS; ++i) {
        rehab_person_follow_snapshot_slot_t *slot = &handle->snapshot_slots[i];
        slot->length = 0U;
        slot->valid = false;
        slot->staging = false;
        slot->staged = false;
    }
}

bool rehab_person_follow_snapshot_leases_idle(
    rehab_person_follow_handle_t handle)
{
    if (handle == NULL) {
        return false;
    }
    for (size_t i = 0; i < PERSON_FOLLOW_SNAPSHOT_SLOTS; ++i) {
        if (handle->snapshot_slots[i].lease_count != 0U ||
            handle->snapshot_slots[i].staging ||
            handle->snapshot_slots[i].staged) {
            return false;
        }
    }
    return true;
}
