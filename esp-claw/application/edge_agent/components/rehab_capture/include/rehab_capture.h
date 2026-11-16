// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "rehab_tof.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_CAPTURE_IMAGE_WIDTH             640U
#define REHAB_CAPTURE_IMAGE_HEIGHT            360U
#define REHAB_CAPTURE_IMAGE_STRIDE             1280U
#define REHAB_CAPTURE_IMAGE_BYTES              460800U
#define REHAB_CAPTURE_TOF_CANDIDATE_COUNT       1U
#define REHAB_CAPTURE_MAX_PAIR_OFFSET_MS        100U
#define REHAB_CAPTURE_TOF_JSON_MAX              4096U
#define REHAB_CAPTURE_ID_MAX                    24U
#define REHAB_CAPTURE_BOUNDARY_MAX              40U
#define REHAB_CAPTURE_CONTENT_TYPE_MAX          80U
#define REHAB_CAPTURE_ERROR_MAX                 64U
#define REHAB_CAPTURE_QUIESCENCE_POLL_MS        50U
#define REHAB_CAPTURE_QUIESCENCE_TIMEOUT_MS   3000U
#define REHAB_CAPTURE_QUIESCENCE_SETTLE_MS     100U

typedef struct {
    uint32_t lease_token;
    char capture_id[REHAB_CAPTURE_ID_MAX];
    const uint8_t *image;
    size_t image_length;
    uint64_t image_seq;
    int64_t image_timestamp_us;
    uint32_t image_crc32;
    uint64_t tof_seq;
    int64_t tof_timestamp_us;
    uint8_t configured_candidate_count;
    uint8_t actual_candidate_count;
    uint8_t selected_index;
    int64_t signed_offset_us;
    uint64_t absolute_offset_us;
    uint32_t offset_ms;
    bool pair_within_limit;
    bool ready_for_cloud;
    int16_t distance_mm[REHAB_TOF_ZONE_COUNT];
    uint8_t status_code[REHAB_TOF_ZONE_COUNT];
    char tof_json[REHAB_CAPTURE_TOF_JSON_MAX];
    size_t tof_json_length;
    char boundary[REHAB_CAPTURE_BOUNDARY_MAX];
    char content_type[REHAB_CAPTURE_CONTENT_TYPE_MAX];
    size_t content_length;
    uint64_t total_us;
} rehab_capture_packet_t;

typedef struct {
    bool ready;
    bool busy;
    bool waiter_active;
    bool request_pending;
    bool coordinator_active;
    bool capture_quiescent;
    bool tof_request_pending;
    bool tof_window_active;
    bool tof_quiescent;
    bool camera_request_pending;
    bool camera_snapshot_active;
    bool camera_quiescent;
    bool pipeline_quiescent;
    bool camera_ready;
    bool tof_ready;
    bool packet_lease;
    uint8_t configured_candidate_count;
    uint8_t max_candidate_count;
    uint32_t max_pair_offset_ms;
    uint64_t request_count;
    uint64_t success_count;
    uint64_t error_count;
    uint64_t next_capture_seq;
    char last_capture_id[REHAB_CAPTURE_ID_MAX];
    uint64_t last_image_seq;
    uint64_t last_tof_seq;
    uint8_t last_actual_candidate_count;
    uint8_t last_selected_index;
    int64_t last_signed_offset_us;
    uint64_t last_absolute_offset_us;
    uint32_t last_offset_ms;
    bool last_pair_within_limit;
    bool last_ready_for_cloud;
    size_t last_packet_content_length;
    uint64_t caller_timeout_count;
    uint64_t camera_timeout_count;
    uint64_t tof_timeout_count;
    char last_error[REHAB_CAPTURE_ERROR_MAX];
} rehab_capture_status_t;

typedef esp_err_t (*rehab_capture_write_fn)(void *ctx,
                                            const void *data,
                                            size_t length);

/** Return false to interrupt a quiescence wait at a caller-owned boundary. */
typedef bool (*rehab_capture_quiescence_wait_fn)(void *ctx,
                                                  uint32_t wait_ms);

esp_err_t rehab_capture_init(void);
void rehab_capture_deinit(void);
bool rehab_capture_is_ready(void);
esp_err_t rehab_capture_get_status(rehab_capture_status_t *status);
bool rehab_capture_status_is_quiescent(const rehab_capture_status_t *status);
/**
 * Wait until the capture coordinator, packet lease, ToF request/window and
 * Camera snapshot are all idle, then confirm the state after a settle period.
 * timeout_ms includes polling and settle time. No capture or hardware reset is
 * performed. ESP_ERR_TIMEOUT means the pipeline never became stably idle;
 * ESP_ERR_INVALID_STATE means the caller interrupted the supplied wait hook.
 */
esp_err_t rehab_capture_wait_for_quiescence(
    uint32_t timeout_ms,
    uint32_t poll_ms,
    uint32_t settle_ms,
    rehab_capture_quiescence_wait_fn wait_fn,
    void *wait_ctx,
    uint32_t *waited_ms,
    rehab_capture_status_t *last_status);
esp_err_t rehab_capture_packet_acquire(rehab_capture_packet_t *packet,
                                       uint32_t timeout_ms);
void rehab_capture_packet_release(rehab_capture_packet_t *packet);
esp_err_t rehab_capture_packet_write_multipart(
    const rehab_capture_packet_t *packet,
    rehab_capture_write_fn writer,
    void *writer_ctx);
esp_err_t rehab_capture_packet_set_epoch_offset(
    rehab_capture_packet_t *packet,
    int64_t epoch_offset_us);

#ifdef __cplusplus
}
#endif
