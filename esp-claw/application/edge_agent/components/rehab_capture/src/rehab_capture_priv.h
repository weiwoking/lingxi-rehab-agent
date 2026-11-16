// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "rehab_capture.h"
#include "rehab_follow.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

typedef struct {
    SemaphoreHandle_t lock;
    StaticSemaphore_t lock_storage;
    SemaphoreHandle_t request_done;
    StaticSemaphore_t request_done_storage;
    SemaphoreHandle_t camera_done;
    StaticSemaphore_t camera_done_storage;
    SemaphoreHandle_t coordinator_stopped;
    StaticSemaphore_t coordinator_stopped_storage;
    SemaphoreHandle_t camera_stopped;
    StaticSemaphore_t camera_stopped_storage;
    TaskHandle_t coordinator_task;
    TaskHandle_t camera_task;
    bool initialized;
    bool stop_requested;
    bool busy;
    bool request_pending;
    bool coordinator_active;
    bool waiter_active;
    bool camera_started;
    bool packet_lease;
    uint32_t packet_token;
    uint64_t active_request_seq;
    esp_err_t request_result;
    esp_err_t camera_result;
    rehab_follow_camera_raw_frame_t camera_frame;
    rehab_tof_window_result_t tof_window;
    uint8_t *image_slot;
    rehab_capture_packet_t packet;
    rehab_capture_status_t status;
} rehab_capture_context_t;

extern rehab_capture_context_t g_rehab_capture;

esp_err_t rehab_capture_build_packet(
    rehab_capture_packet_t *packet,
    uint8_t *image_slot,
    const rehab_follow_camera_raw_frame_t *camera,
    const rehab_tof_window_result_t *window,
    uint8_t selected_index,
    const char *capture_id,
    uint32_t lease_token,
    uint64_t total_us);
