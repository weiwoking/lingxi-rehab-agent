// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stdint.h>

#define REHAB_TOF_FRAME_ZONE_COUNT 64U

typedef struct {
    int16_t distance_mm[REHAB_TOF_FRAME_ZONE_COUNT];
    uint16_t range_sigma_mm[REHAB_TOF_FRAME_ZONE_COUNT];
    uint8_t target_status[REHAB_TOF_FRAME_ZONE_COUNT];
    uint8_t nb_target_detected[REHAB_TOF_FRAME_ZONE_COUNT];
} rehab_tof_frame_payload_t;

typedef struct {
    bool running;
    bool latest_valid;
    uint8_t startup_discard_remaining;
    uint64_t sequence;
    rehab_tof_frame_payload_t latest;
} rehab_tof_frame_collector_t;

typedef enum {
    REHAB_TOF_FRAME_REJECTED = 0,
    REHAB_TOF_FRAME_STARTUP_DISCARDED,
    REHAB_TOF_FRAME_PUBLISHED,
} rehab_tof_frame_ingest_result_t;

typedef enum {
    REHAB_TOF_IO_OK = 0,
    REHAB_TOF_IO_TIMEOUT,
    REHAB_TOF_IO_NACK,
    REHAB_TOF_IO_SHORT_READ,
    REHAB_TOF_IO_DRIVER_ERROR,
} rehab_tof_io_validation_t;

typedef struct {
    bool has_logged;
    int64_t last_log_us;
} rehab_tof_diag_log_gate_t;

typedef struct {
    uint8_t valid_zones;
    uint8_t target0_zones;
    uint8_t target1_zones;
    uint8_t target_gt1_zones;
    uint8_t status5_zones;
    uint8_t status6_zones;
    uint8_t status9_zones;
    uint8_t status255_zones;
    uint8_t status_other_zones;
    uint8_t sigma_zero_zones;
    int32_t distance_min_mm;
    int32_t distance_median_mm;
    int32_t distance_max_mm;
    int32_t sigma_min_mm;
    int32_t sigma_median_mm;
    int32_t sigma_max_mm;
} rehab_tof_frame_summary_t;

void rehab_tof_frame_collector_start(rehab_tof_frame_collector_t *collector,
                                     uint8_t startup_discard_frames);
void rehab_tof_frame_collector_stop(rehab_tof_frame_collector_t *collector);
rehab_tof_frame_ingest_result_t rehab_tof_frame_collector_ingest(
    rehab_tof_frame_collector_t *collector,
    const rehab_tof_frame_payload_t *payload,
    uint64_t *published_sequence);
bool rehab_tof_frame_collector_copy_latest(
    const rehab_tof_frame_collector_t *collector,
    uint64_t after_sequence,
    rehab_tof_frame_payload_t *payload,
    uint64_t *sequence);
rehab_tof_io_validation_t rehab_tof_frame_validate_io(
    int driver_status,
    int platform_error,
    uint32_t expected_size,
    uint32_t actual_size);
bool rehab_tof_diag_log_gate_due(rehab_tof_diag_log_gate_t *gate,
                                 int64_t now_us,
                                 int64_t period_us);
void rehab_tof_frame_summarize(const rehab_tof_frame_payload_t *payload,
                               rehab_tof_frame_summary_t *summary);
