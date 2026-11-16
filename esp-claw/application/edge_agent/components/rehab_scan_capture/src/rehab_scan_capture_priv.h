// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <time.h>

#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "rehab_scan_capture.h"
#include "rehab_tof.h"

#define REHAB_SCAN_IMAGE_WIDTH       640U
#define REHAB_SCAN_IMAGE_HEIGHT      360U
#define REHAB_SCAN_IMAGE_STRIDE      1280U
#define REHAB_SCAN_IMAGE_BYTES       460800U
#define REHAB_SCAN_TOF_WIDTH         8U
#define REHAB_SCAN_TOF_HEIGHT        8U
#define REHAB_SCAN_MIN_FRAMES        1U
#define REHAB_SCAN_MAX_FRAMES        64U
#define REHAB_SCAN_MIN_INTERVAL_MS   500U
#define REHAB_SCAN_MAX_INTERVAL_MS   10000U
#define REHAB_SCAN_MIN_VALID_EPOCH   1704067200LL
#define REHAB_SCAN_ID_ATTEMPTS       1000U
#define REHAB_SCAN_DELETE_WAIT_MS    2000U
#define REHAB_SCAN_ABORT_POLL_MS     50U

#define REHAB_SCAN_SCHEMA "deltar_scan_input.v1"
#define REHAB_FRAME_SCHEMA "deltar_scan_frame.v1"
#define REHAB_TOF_SCHEMA "rehab_tof_raw.v1"
#define REHAB_CAMERA_PROFILE_ID "camera_sc2336_640x360_rgb565_v1"
#define REHAB_TOF_PROFILE_ID "tof_vl53l5cx_orientation_identity_v1"
#define REHAB_CALIBRATION_PROFILE_ID "calibration_unverified_stage6_v1"
#define REHAB_ALGORITHM_PROFILE_ID "algorithm_stage6_official_v1"
#define REHAB_SCAN_STRATEGY_PROFILE_ID "scan_strategy_stage6_guidance_v1"

typedef struct {
    uint32_t sequence;
    int64_t camera_timestamp_us;
    rehab_tof_extended_frame_t tof;
    uint64_t pair_delta_us;
    uint64_t provider_before_sequence;
    uint64_t provider_after_sequence;
} rehab_scan_frame_record_t;

struct rehab_scan_capture {
    char scan_root_path[REHAB_SCAN_CAPTURE_PATH_MAX];
    rehab_scan_capture_config_t config;
    SemaphoreHandle_t lock;
    SemaphoreHandle_t worker_done;
    TaskHandle_t worker;
    bool abort_requested;
    bool console_registered;
    time_t last_scan_second;
    uint32_t next_suffix;
    uint32_t request_interval_ms;
    char created_at[32];
    rehab_scan_capture_status_t status;
    rehab_scan_capture_prepare_cb_t prepare_cb;
    rehab_scan_capture_before_frame_cb_t before_frame_cb;
    rehab_scan_capture_frame_cb_t before_write_cb;
    rehab_scan_capture_frame_cb_t after_frame_cb;
    rehab_scan_capture_before_commit_cb_t before_commit_cb;
    void *guided_ctx;
};

bool rehab_scan_capture_abort_requested(rehab_scan_capture_handle_t handle);
void rehab_scan_capture_set_failure(rehab_scan_capture_handle_t handle,
                                    esp_err_t error,
                                    int saved_errno,
                                    uint32_t sequence,
                                    bool aborted);
esp_err_t rehab_scan_capture_pair_frame(rehab_scan_capture_handle_t handle,
                                        uint32_t sequence,
                                        uint8_t *image_buffer,
                                        rehab_scan_frame_record_t *out_record);

cJSON *rehab_scan_capture_make_tof_json(const rehab_scan_frame_record_t *record);
cJSON *rehab_scan_capture_make_frame_json(const rehab_scan_frame_record_t *record);
cJSON *rehab_scan_capture_make_scan_json(rehab_scan_capture_handle_t handle);

esp_err_t rehab_scan_capture_prepare_working(rehab_scan_capture_handle_t handle,
                                             char *working_path,
                                             size_t working_path_size,
                                             char *final_path,
                                             size_t final_path_size);
esp_err_t rehab_scan_capture_make_frame_dir(const char *working_path,
                                            uint32_t sequence,
                                            char *frame_path,
                                            size_t frame_path_size);
esp_err_t rehab_scan_capture_write_binary_new_atomic(const char *directory,
                                                     const char *filename,
                                                     const void *data,
                                                     size_t length,
                                                     int *out_errno);
esp_err_t rehab_scan_capture_write_json_new_atomic(const char *directory,
                                                   const char *filename,
                                                   const cJSON *json,
                                                   int *out_errno);
esp_err_t rehab_scan_capture_commit_directory(const char *working_path,
                                              const char *final_path,
                                              int *out_errno);
