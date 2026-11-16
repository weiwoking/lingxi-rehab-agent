// SPDX-License-Identifier: Apache-2.0

#include "rehab_tof_frame_core.h"

#include <string.h>

static bool summary_zone_valid(const rehab_tof_frame_payload_t *payload,
                               size_t zone)
{
    uint8_t status = payload->target_status[zone];
    return payload->nb_target_detected[zone] > 0U &&
           payload->distance_mm[zone] > 0 &&
           (status == 5U || status == 6U || status == 9U);
}

static int32_t summary_kth_value(const rehab_tof_frame_payload_t *payload,
                                 uint8_t rank,
                                 bool sigma)
{
    for (size_t candidate_zone = 0;
         candidate_zone < REHAB_TOF_FRAME_ZONE_COUNT;
         ++candidate_zone) {
        if (!summary_zone_valid(payload, candidate_zone)) {
            continue;
        }
        int32_t candidate = sigma
            ? (int32_t)payload->range_sigma_mm[candidate_zone]
            : (int32_t)payload->distance_mm[candidate_zone];
        uint8_t less = 0U;
        uint8_t equal = 0U;
        for (size_t zone = 0; zone < REHAB_TOF_FRAME_ZONE_COUNT; ++zone) {
            if (!summary_zone_valid(payload, zone)) {
                continue;
            }
            int32_t value = sigma
                ? (int32_t)payload->range_sigma_mm[zone]
                : (int32_t)payload->distance_mm[zone];
            if (value < candidate) {
                ++less;
            } else if (value == candidate) {
                ++equal;
            }
        }
        if (rank >= less && rank < (uint8_t)(less + equal)) {
            return candidate;
        }
    }
    return -1;
}

void rehab_tof_frame_collector_start(rehab_tof_frame_collector_t *collector,
                                     uint8_t startup_discard_frames)
{
    if (collector == NULL) {
        return;
    }
    uint64_t sequence = collector->sequence;
    memset(collector, 0, sizeof(*collector));
    collector->running = true;
    collector->startup_discard_remaining = startup_discard_frames;
    collector->sequence = sequence;
}

void rehab_tof_frame_collector_stop(rehab_tof_frame_collector_t *collector)
{
    if (collector == NULL) {
        return;
    }
    collector->running = false;
    collector->latest_valid = false;
    memset(&collector->latest, 0, sizeof(collector->latest));
}

rehab_tof_frame_ingest_result_t rehab_tof_frame_collector_ingest(
    rehab_tof_frame_collector_t *collector,
    const rehab_tof_frame_payload_t *payload,
    uint64_t *published_sequence)
{
    if (collector == NULL || payload == NULL || !collector->running) {
        return REHAB_TOF_FRAME_REJECTED;
    }
    if (collector->startup_discard_remaining > 0U) {
        --collector->startup_discard_remaining;
        return REHAB_TOF_FRAME_STARTUP_DISCARDED;
    }
    collector->latest = *payload;
    collector->latest_valid = true;
    ++collector->sequence;
    if (published_sequence != NULL) {
        *published_sequence = collector->sequence;
    }
    return REHAB_TOF_FRAME_PUBLISHED;
}

bool rehab_tof_frame_collector_copy_latest(
    const rehab_tof_frame_collector_t *collector,
    uint64_t after_sequence,
    rehab_tof_frame_payload_t *payload,
    uint64_t *sequence)
{
    if (collector == NULL || payload == NULL || sequence == NULL ||
        !collector->running || !collector->latest_valid ||
        collector->sequence <= after_sequence) {
        return false;
    }
    *payload = collector->latest;
    *sequence = collector->sequence;
    return true;
}

rehab_tof_io_validation_t rehab_tof_frame_validate_io(
    int driver_status,
    int platform_error,
    uint32_t expected_size,
    uint32_t actual_size)
{
    if (platform_error == 0x107) {
        return REHAB_TOF_IO_TIMEOUT;
    }
    /* ESP_ERR_INVALID_RESPONSE is used by the host seam to model a NACK. */
    if (platform_error == 0x108) {
        return REHAB_TOF_IO_NACK;
    }
    if (driver_status != 0 || platform_error != 0) {
        return REHAB_TOF_IO_DRIVER_ERROR;
    }
    if (actual_size != expected_size) {
        return REHAB_TOF_IO_SHORT_READ;
    }
    return REHAB_TOF_IO_OK;
}

bool rehab_tof_diag_log_gate_due(rehab_tof_diag_log_gate_t *gate,
                                 int64_t now_us,
                                 int64_t period_us)
{
    if (gate == NULL || now_us < 0 || period_us <= 0) {
        return false;
    }
    if (!gate->has_logged || now_us < gate->last_log_us ||
        now_us - gate->last_log_us >= period_us) {
        gate->has_logged = true;
        gate->last_log_us = now_us;
        return true;
    }
    return false;
}

void rehab_tof_frame_summarize(const rehab_tof_frame_payload_t *payload,
                               rehab_tof_frame_summary_t *summary)
{
    if (summary == NULL) {
        return;
    }
    memset(summary, 0, sizeof(*summary));
    summary->distance_min_mm = -1;
    summary->distance_median_mm = -1;
    summary->distance_max_mm = -1;
    summary->sigma_min_mm = -1;
    summary->sigma_median_mm = -1;
    summary->sigma_max_mm = -1;
    if (payload == NULL) {
        return;
    }

    for (size_t zone = 0; zone < REHAB_TOF_FRAME_ZONE_COUNT; ++zone) {
        uint8_t targets = payload->nb_target_detected[zone];
        uint8_t status = payload->target_status[zone];
        if (targets == 0U) {
            ++summary->target0_zones;
        } else if (targets == 1U) {
            ++summary->target1_zones;
        } else {
            ++summary->target_gt1_zones;
        }
        if (targets > 0U && payload->range_sigma_mm[zone] == 0U) {
            ++summary->sigma_zero_zones;
        }
        if (status == 5U) {
            ++summary->status5_zones;
        } else if (status == 6U) {
            ++summary->status6_zones;
        } else if (status == 9U) {
            ++summary->status9_zones;
        } else if (status == 255U) {
            ++summary->status255_zones;
        } else {
            ++summary->status_other_zones;
        }
        if (!summary_zone_valid(payload, zone)) {
            continue;
        }
        ++summary->valid_zones;
        int32_t distance = payload->distance_mm[zone];
        int32_t sigma = payload->range_sigma_mm[zone];
        if (summary->distance_min_mm < 0 ||
            distance < summary->distance_min_mm) {
            summary->distance_min_mm = distance;
        }
        if (distance > summary->distance_max_mm) {
            summary->distance_max_mm = distance;
        }
        if (summary->sigma_min_mm < 0 || sigma < summary->sigma_min_mm) {
            summary->sigma_min_mm = sigma;
        }
        if (sigma > summary->sigma_max_mm) {
            summary->sigma_max_mm = sigma;
        }
    }

    if (summary->valid_zones == 0U) {
        return;
    }
    uint8_t lower_rank = (uint8_t)((summary->valid_zones - 1U) / 2U);
    uint8_t upper_rank = (uint8_t)(summary->valid_zones / 2U);
    int32_t distance_lower = summary_kth_value(payload, lower_rank, false);
    int32_t distance_upper = summary_kth_value(payload, upper_rank, false);
    int32_t sigma_lower = summary_kth_value(payload, lower_rank, true);
    int32_t sigma_upper = summary_kth_value(payload, upper_rank, true);
    summary->distance_median_mm =
        (distance_lower + distance_upper) / 2;
    summary->sigma_median_mm = (sigma_lower + sigma_upper) / 2;
}
