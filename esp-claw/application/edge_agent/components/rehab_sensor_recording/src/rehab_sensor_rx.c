/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "rehab_sensor_internal.h"

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"

static const char *TAG = "rehab_sensor_rx";

rehab_sensor_runtime_t g_rehab_sensor;

typedef struct {
    uint8_t datagram[sizeof(sensor_packet_t) + 1];
    sensor_packet_t packet;
    rehab_record_item_t record_item;
} rehab_rx_context_t;

typedef struct {
    bool initialized;
    bool node_online[REHAB_SENSOR_NODE_COUNT];
    rehab_udp_quality_t udp_quality[REHAB_SENSOR_NODE_COUNT];
    bool required_ok;
    bool record_queue_drop;
    bool sd_write_error;
} rehab_sensor_summary_signature_t;

static int rehab_sensor_node_index(uint8_t node_id, uint8_t node_type)
{
    if (node_id == 1 && node_type == SENSOR_NODE_TYPE_EMG_DUAL_IMU) {
        return REHAB_SENSOR_NODE1_INDEX;
    }
    if (node_id == 2 && node_type == SENSOR_NODE_TYPE_DUAL_IMU) {
        return REHAB_SENSOR_NODE2_INDEX;
    }
    return -1;
}

static int rehab_sensor_claimed_node_index(const sensor_packet_t *packet)
{
    if (!packet) {
        return -1;
    }
    if (packet->node_id == 1) {
        return REHAB_SENSOR_NODE1_INDEX;
    }
    if (packet->node_id == 2) {
        return REHAB_SENSOR_NODE2_INDEX;
    }
    return -1;
}

static bool rehab_sensor_imu_finite(const sensor_imu_state_t *imu)
{
    return imu &&
           isfinite(imu->acc_x_g) && isfinite(imu->acc_y_g) && isfinite(imu->acc_z_g) &&
           isfinite(imu->lin_acc_x_g) && isfinite(imu->lin_acc_y_g) && isfinite(imu->lin_acc_z_g) &&
           isfinite(imu->gyro_x_dps) && isfinite(imu->gyro_y_dps) && isfinite(imu->gyro_z_dps) &&
           isfinite(imu->angle_x_deg) && isfinite(imu->angle_y_deg) && isfinite(imu->angle_z_deg);
}

static bool rehab_sensor_emg_finite(const sensor_emg_state_t *emg)
{
    return emg &&
           isfinite(emg->rms_ch1) && isfinite(emg->rms_ch2) &&
           isfinite(emg->rms_ch3) && isfinite(emg->rms_ch4) &&
           isfinite(emg->mav_ch1) && isfinite(emg->mav_ch2) &&
           isfinite(emg->mav_ch3) && isfinite(emg->mav_ch4);
}

uint16_t rehab_sensor_packet_checksum(const sensor_packet_t *packet)
{
    const uint8_t *bytes;
    uint32_t sum = 0;

    if (!packet) {
        return 0;
    }
    bytes = (const uint8_t *)packet;
    for (size_t index = 0; index < offsetof(sensor_packet_t, checksum); ++index) {
        sum += bytes[index];
    }
    return (uint16_t)(sum & 0xffffu);
}

rehab_packet_validation_t rehab_sensor_packet_validate(const void *datagram,
                                                       size_t datagram_length,
                                                       sensor_packet_t *packet)
{
    if (!datagram || !packet || datagram_length != sizeof(sensor_packet_t)) {
        return REHAB_PACKET_BAD_LENGTH;
    }

    /* Never cast an arbitrary network buffer to the packed wire structure. */
    memcpy(packet, datagram, sizeof(*packet));

    if (packet->magic != SENSOR_PACKET_MAGIC) {
        return REHAB_PACKET_BAD_MAGIC;
    }
    if (packet->version != SENSOR_PACKET_VERSION) {
        return REHAB_PACKET_BAD_VERSION;
    }
    if (packet->packet_size != sizeof(sensor_packet_t)) {
        return REHAB_PACKET_BAD_SIZE;
    }
    if (rehab_sensor_node_index(packet->node_id, packet->node_type) < 0) {
        return REHAB_PACKET_BAD_NODE;
    }
    if ((packet->flags & ~SENSOR_PACKET_KNOWN_FLAGS) != 0 ||
            (packet->node_id == 2 && (packet->flags & SENSOR_FLAG_EMG_VALID) != 0)) {
        return REHAB_PACKET_BAD_FLAGS;
    }
    if (rehab_sensor_packet_checksum(packet) != packet->checksum) {
        return REHAB_PACKET_BAD_CHECKSUM;
    }
    if (!rehab_sensor_emg_finite(&packet->emg) ||
            !rehab_sensor_imu_finite(&packet->imu1) ||
            !rehab_sensor_imu_finite(&packet->imu2)) {
        return REHAB_PACKET_NON_FINITE;
    }
    return REHAB_PACKET_OK;
}

rehab_seq_result_t rehab_sensor_classify_sequence(bool has_baseline,
                                                  bool was_offline,
                                                  uint32_t previous_seq,
                                                  int64_t previous_tx_us,
                                                  uint32_t new_seq,
                                                  int64_t new_tx_us,
                                                  uint32_t *gap)
{
    uint32_t delta;

    if (gap) {
        *gap = 0;
    }
    if (!has_baseline) {
        return REHAB_SEQ_BASELINE;
    }
    if (was_offline ||
            (previous_tx_us > 0 && new_tx_us > 0 && new_tx_us + 1000000LL < previous_tx_us)) {
        return REHAB_SEQ_REBASELINE;
    }

    delta = (uint32_t)(new_seq - previous_seq);
    if (delta == 0) {
        return REHAB_SEQ_DUPLICATE;
    }
    if (delta == 1) {
        return REHAB_SEQ_CONTIGUOUS;
    }
    if (delta < 0x80000000u) {
        if (gap) {
            *gap = delta - 1u;
        }
        return REHAB_SEQ_GAP;
    }
    return REHAB_SEQ_OUT_OF_ORDER;
}

rehab_udp_quality_t rehab_sensor_classify_udp_quality_pct(float gap_ratio_pct)
{
    if (!isfinite(gap_ratio_pct) || gap_ratio_pct < 0.0f) {
        return REHAB_UDP_QUALITY_UNKNOWN;
    }
    if (gap_ratio_pct <= 20.0f) {
        return REHAB_UDP_QUALITY_NORMAL;
    }
    if (gap_ratio_pct <= 30.0f) {
        return REHAB_UDP_QUALITY_ACCEPTABLE;
    }
    return REHAB_UDP_QUALITY_DEGRADED;
}

rehab_udp_quality_t rehab_sensor_calculate_udp_quality(bool has_baseline,
                                                       uint64_t received_packets,
                                                       uint64_t gap_packets,
                                                       float *gap_ratio_pct)
{
    double denominator;
    double ratio;

    if (gap_ratio_pct) {
        *gap_ratio_pct = 0.0f;
    }
    if (!has_baseline || received_packets < REHAB_SENSOR_UDP_MIN_WINDOW_RECEIVED) {
        return REHAB_UDP_QUALITY_UNKNOWN;
    }

    denominator = (double)received_packets + (double)gap_packets;
    if (!isfinite(denominator) || denominator <= 0.0) {
        return REHAB_UDP_QUALITY_UNKNOWN;
    }
    ratio = (double)gap_packets * 100.0 / denominator;
    if (!isfinite(ratio) || ratio < 0.0 || ratio > 100.0) {
        return REHAB_UDP_QUALITY_UNKNOWN;
    }
    if (gap_ratio_pct) {
        *gap_ratio_pct = (float)ratio;
    }
    return rehab_sensor_classify_udp_quality_pct((float)ratio);
}

bool rehab_sensor_session_is_active(rehab_training_state_t state, bool file_open)
{
    return state == REHAB_TRAINING_STATE_STARTING ||
           state == REHAB_TRAINING_STATE_RECORDING ||
           state == REHAB_TRAINING_STATE_STOPPING ||
           (state == REHAB_TRAINING_STATE_ERROR && file_open);
}

esp_err_t rehab_sensor_lock(void)
{
    if (!g_rehab_sensor.mutex) {
        return ESP_ERR_INVALID_STATE;
    }
    return xSemaphoreTake(g_rehab_sensor.mutex, portMAX_DELAY) == pdTRUE ?
           ESP_OK : ESP_ERR_TIMEOUT;
}

void rehab_sensor_unlock(void)
{
    xSemaphoreGive(g_rehab_sensor.mutex);
}

uint32_t rehab_sensor_age_ms(int64_t now_us, int64_t then_us)
{
    uint64_t age;

    if (then_us <= 0 || now_us <= then_us) {
        return 0;
    }
    age = (uint64_t)(now_us - then_us) / 1000u;
    return age > UINT32_MAX ? UINT32_MAX : (uint32_t)age;
}

uint32_t rehab_sensor_saturating_add_u32(uint32_t left, uint32_t right)
{
    return UINT32_MAX - left < right ? UINT32_MAX : left + right;
}

void rehab_sensor_set_error_locked(esp_err_t err, int system_errno, const char *message)
{
    g_rehab_sensor.last_error_code = err;
    g_rehab_sensor.last_errno = system_errno;
    strlcpy(g_rehab_sensor.last_error,
            message ? message : esp_err_to_name(err),
            sizeof(g_rehab_sensor.last_error));
}

void rehab_sensor_clear_error_locked(void)
{
    g_rehab_sensor.last_error_code = ESP_OK;
    g_rehab_sensor.last_errno = 0;
    g_rehab_sensor.last_error[0] = '\0';
}

static bool rehab_sensor_float_changed(float left, float right)
{
    return fabsf(left - right) > 0.0001f;
}

static bool rehab_sensor_imu_values_changed(const sensor_imu_state_t *left,
                                            const sensor_imu_state_t *right)
{
    return rehab_sensor_float_changed(left->acc_x_g, right->acc_x_g) ||
           rehab_sensor_float_changed(left->acc_y_g, right->acc_y_g) ||
           rehab_sensor_float_changed(left->acc_z_g, right->acc_z_g) ||
           rehab_sensor_float_changed(left->lin_acc_x_g, right->lin_acc_x_g) ||
           rehab_sensor_float_changed(left->lin_acc_y_g, right->lin_acc_y_g) ||
           rehab_sensor_float_changed(left->lin_acc_z_g, right->lin_acc_z_g) ||
           rehab_sensor_float_changed(left->gyro_x_dps, right->gyro_x_dps) ||
           rehab_sensor_float_changed(left->gyro_y_dps, right->gyro_y_dps) ||
           rehab_sensor_float_changed(left->gyro_z_dps, right->gyro_z_dps) ||
           rehab_sensor_float_changed(left->angle_x_deg, right->angle_x_deg) ||
           rehab_sensor_float_changed(left->angle_y_deg, right->angle_y_deg) ||
           rehab_sensor_float_changed(left->angle_z_deg, right->angle_z_deg);
}

static bool rehab_sensor_emg_group_changed(const sensor_emg_state_t *left,
                                           const sensor_emg_state_t *right,
                                           unsigned first_channel)
{
    const int32_t left_latest[4] = {
        left->latest_ch1, left->latest_ch2, left->latest_ch3, left->latest_ch4
    };
    const int32_t right_latest[4] = {
        right->latest_ch1, right->latest_ch2, right->latest_ch3, right->latest_ch4
    };
    const float left_rms[4] = {left->rms_ch1, left->rms_ch2, left->rms_ch3, left->rms_ch4};
    const float right_rms[4] = {right->rms_ch1, right->rms_ch2, right->rms_ch3, right->rms_ch4};
    const float left_mav[4] = {left->mav_ch1, left->mav_ch2, left->mav_ch3, left->mav_ch4};
    const float right_mav[4] = {right->mav_ch1, right->mav_ch2, right->mav_ch3, right->mav_ch4};

    for (unsigned channel = first_channel; channel < first_channel + 2; ++channel) {
        if (left_latest[channel] != right_latest[channel] ||
                rehab_sensor_float_changed(left_rms[channel], right_rms[channel]) ||
                rehab_sensor_float_changed(left_mav[channel], right_mav[channel])) {
            return true;
        }
    }
    return false;
}

static bool rehab_sensor_emg_semantically_valid(const sensor_packet_t *packet)
{
    return (packet->flags & SENSOR_FLAG_EMG_VALID) != 0 &&
           packet->emg.sample_count > 0 &&
           packet->emg.rms_ch1 >= 0.0f && packet->emg.rms_ch2 >= 0.0f &&
           packet->emg.rms_ch3 >= 0.0f && packet->emg.rms_ch4 >= 0.0f &&
           packet->emg.mav_ch1 >= 0.0f && packet->emg.mav_ch2 >= 0.0f &&
           packet->emg.mav_ch3 >= 0.0f && packet->emg.mav_ch4 >= 0.0f &&
           packet->emg.peak_abs_ch1 >= 0 && packet->emg.peak_abs_ch2 >= 0 &&
           packet->emg.peak_abs_ch3 >= 0 && packet->emg.peak_abs_ch4 >= 0;
}

static void rehab_sensor_update_imu_locked(rehab_imu_runtime_t *runtime,
                                           const sensor_imu_state_t *imu,
                                           bool flag_valid,
                                           int64_t now_us)
{
    bool metadata_advanced = false;

    if (runtime->initialized) {
        metadata_advanced = imu->update_count != runtime->last_update_count &&
                            imu->ts_us > runtime->last_ts_us;
        if (rehab_sensor_imu_values_changed(&runtime->latest, imu)) {
            runtime->last_value_change_us = now_us;
        }
    } else {
        runtime->initialized = true;
        metadata_advanced = flag_valid && imu->update_count > 0 && imu->ts_us > 0;
        runtime->last_value_change_us = now_us;
    }

    runtime->flag_valid = flag_valid;
    if (metadata_advanced) {
        runtime->last_metadata_advance_us = now_us;
    }
    runtime->last_update_count = imu->update_count;
    runtime->last_ts_us = imu->ts_us;
    runtime->latest = *imu;
}

static void rehab_sensor_update_emg_locked(const sensor_packet_t *packet, int64_t now_us)
{
    rehab_emg_runtime_t *runtime = &g_rehab_sensor.emg;
    bool metadata_advanced = false;

    if (runtime->initialized) {
        metadata_advanced = packet->emg.window_end_us > runtime->last_window_end_us &&
                            packet->emg.window_start_us >= runtime->latest.window_start_us;
        if (rehab_sensor_emg_group_changed(&runtime->latest, &packet->emg, 0)) {
            runtime->group_1_last_change_us = now_us;
        }
        if (rehab_sensor_emg_group_changed(&runtime->latest, &packet->emg, 2)) {
            runtime->group_2_last_change_us = now_us;
        }
    } else {
        runtime->initialized = true;
        metadata_advanced = packet->emg.window_end_us > packet->emg.window_start_us;
        runtime->group_1_last_change_us = now_us;
        runtime->group_2_last_change_us = now_us;
    }

    runtime->flag_valid = rehab_sensor_emg_semantically_valid(packet);
    if (metadata_advanced) {
        runtime->last_metadata_advance_us = now_us;
    }
    runtime->last_window_end_us = packet->emg.window_end_us;
    runtime->latest = packet->emg;
}

static void rehab_sensor_update_sensor_health_locked(int node_index,
                                                     const sensor_packet_t *packet,
                                                     int64_t now_us)
{
    unsigned base_imu = node_index == REHAB_SENSOR_NODE1_INDEX ? 0 : 2;

    if (node_index == REHAB_SENSOR_NODE1_INDEX) {
        rehab_sensor_update_emg_locked(packet, now_us);
    }
    rehab_sensor_update_imu_locked(&g_rehab_sensor.imus[base_imu],
                                   &packet->imu1,
                                   (packet->flags & SENSOR_FLAG_IMU1_VALID) != 0,
                                   now_us);
    rehab_sensor_update_imu_locked(&g_rehab_sensor.imus[base_imu + 1],
                                   &packet->imu2,
                                   (packet->flags & SENSOR_FLAG_IMU2_VALID) != 0,
                                   now_us);
}

void rehab_sensor_refresh_health_locked(int64_t now_us)
{
    bool node_online[2] = {false, false};
    bool imu_ok[4] = {false, false, false, false};
    bool emg_ok;
    uint32_t node_age[2] = {0, 0};

    for (unsigned index = 0; index < REHAB_SENSOR_NODE_COUNT; ++index) {
        rehab_node_runtime_t *node = &g_rehab_sensor.nodes[index];
        node_age[index] = rehab_sensor_age_ms(now_us, node->last_rx_us);
        node_online[index] = node->seen && node_age[index] <= REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS;
        node->online = node_online[index];
        node->public_status.online = node_online[index];
        node->public_status.packet_valid = node->has_latest;
        node->public_status.packet_updating = node_online[index] &&
            node_age[index] <= REHAB_SENSOR_SOCKET_TIMEOUT_MS;
        node->public_status.last_rx_age_ms = node_age[index];
    }

    emg_ok = node_online[REHAB_SENSOR_NODE1_INDEX] &&
             g_rehab_sensor.emg.initialized && g_rehab_sensor.emg.flag_valid &&
             rehab_sensor_age_ms(now_us, g_rehab_sensor.emg.last_metadata_advance_us) <=
             REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS;

    for (unsigned index = 0; index < 4; ++index) {
        unsigned node_index = index < 2 ? REHAB_SENSOR_NODE1_INDEX : REHAB_SENSOR_NODE2_INDEX;
        rehab_imu_runtime_t *imu = &g_rehab_sensor.imus[index];
        imu_ok[index] = node_online[node_index] && imu->initialized && imu->flag_valid &&
                        imu->last_update_count > 0 && imu->last_ts_us > 0 &&
                        imu->latest.age_ms <= REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS &&
                        rehab_sensor_age_ms(now_us, imu->last_metadata_advance_us) <=
                        REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS;
    }

    g_rehab_sensor.required_sensors_ok = emg_ok && imu_ok[0] && imu_ok[1] &&
                                         imu_ok[2] && imu_ok[3];

    if (!g_rehab_sensor.recording || g_rehab_sensor.required_sensors_ok) {
        g_rehab_sensor.anomaly_active = false;
        g_rehab_sensor.anomaly_start_us = 0;
        g_rehab_sensor.anomaly_over_10s = false;
    } else {
        if (!g_rehab_sensor.anomaly_active) {
            g_rehab_sensor.anomaly_active = true;
            g_rehab_sensor.anomaly_start_us = now_us;
        }
        if (rehab_sensor_age_ms(now_us, g_rehab_sensor.anomaly_start_us) >=
                REHAB_SENSOR_ANOMALY_THRESHOLD_MS) {
            g_rehab_sensor.anomaly_over_10s = true;
        }
    }
}

static void rehab_sensor_increment_validation_error_locked(int node_index,
                                                            rehab_packet_validation_t validation)
{
    unsigned first = node_index >= 0 ? (unsigned)node_index : 0;
    unsigned end = node_index >= 0 ? first + 1 : REHAB_SENSOR_NODE_COUNT;

    for (unsigned index = first; index < end; ++index) {
        rehab_sensor_node_status_t *status = &g_rehab_sensor.nodes[index].public_status;
        switch (validation) {
            case REHAB_PACKET_BAD_LENGTH: status->bad_length_count++; break;
            case REHAB_PACKET_BAD_MAGIC: status->bad_magic_count++; break;
            case REHAB_PACKET_BAD_VERSION: status->bad_version_count++; break;
            case REHAB_PACKET_BAD_SIZE: status->bad_packet_size_count++; break;
            case REHAB_PACKET_BAD_NODE: status->bad_node_count++; break;
            case REHAB_PACKET_BAD_FLAGS: status->bad_flags_count++; break;
            case REHAB_PACKET_BAD_CHECKSUM: status->checksum_error_count++; break;
            case REHAB_PACKET_NON_FINITE: status->non_finite_count++; break;
            case REHAB_PACKET_OK: break;
        }
    }
}

static void rehab_sensor_update_pps_locked(rehab_node_runtime_t *node, int64_t now_us)
{
    if (node->pps_window_start_us == 0) {
        node->pps_window_start_us = now_us;
        node->pps_window_packets = 1;
        return;
    }

    node->pps_window_packets++;
    if (now_us - node->pps_window_start_us >= 1000000LL) {
        node->public_status.measured_pps =
            (float)node->pps_window_packets * 1000000.0f /
            (float)(now_us - node->pps_window_start_us);
        node->pps_window_start_us = now_us;
        node->pps_window_packets = 0;
    }
}

static void rehab_sensor_reset_udp_window_locked(rehab_node_runtime_t *node,
                                                 int64_t now_us,
                                                 bool baseline_established)
{
    node->udp_window_start_us = now_us;
    node->udp_window_received = 0;
    node->udp_window_gap = 0;
    node->udp_window_has_baseline = baseline_established;
    node->public_status.window_received = 0;
    node->public_status.window_gap = 0;
    node->public_status.udp_gap_ratio_pct = 0.0f;
    node->public_status.udp_quality = REHAB_UDP_QUALITY_UNKNOWN;
}

static void rehab_sensor_note_udp_sequence_locked(rehab_node_runtime_t *node,
                                                  rehab_seq_result_t sequence,
                                                  uint32_t gap,
                                                  int64_t now_us)
{
    if (sequence == REHAB_SEQ_BASELINE || sequence == REHAB_SEQ_REBASELINE) {
        rehab_sensor_reset_udp_window_locked(node, now_us, true);
        return;
    }
    if (!node->udp_window_has_baseline || node->udp_window_start_us == 0) {
        return;
    }
    if (sequence == REHAB_SEQ_CONTIGUOUS || sequence == REHAB_SEQ_GAP) {
        node->udp_window_received++;
        if (sequence == REHAB_SEQ_GAP) {
            node->udp_window_gap += gap;
        }
    }
}

static void rehab_sensor_finalize_udp_window_locked(rehab_node_runtime_t *node,
                                                    int64_t now_us)
{
    if (node->udp_window_start_us == 0 ||
            now_us - node->udp_window_start_us <
            REHAB_SENSOR_SUMMARY_INTERVAL_MS * 1000LL) {
        return;
    }

    node->public_status.window_received = node->udp_window_received;
    node->public_status.window_gap = node->udp_window_gap;
    node->public_status.udp_quality = rehab_sensor_calculate_udp_quality(
        node->udp_window_has_baseline,
        node->udp_window_received,
        node->udp_window_gap,
        &node->public_status.udp_gap_ratio_pct);
    node->udp_window_start_us = now_us;
    node->udp_window_received = 0;
    node->udp_window_gap = 0;
}

static void rehab_sensor_handle_valid_packet(const sensor_packet_t *packet,
                                             int64_t rx_us,
                                             rehab_record_item_t *item)
{
    bool enqueue = false;
    bool should_update = true;
    int node_index = rehab_sensor_node_index(packet->node_id, packet->node_type);
    uint32_t gap = 0;
    rehab_seq_result_t sequence;
    bool became_online = false;

    if (rehab_sensor_lock() != ESP_OK) {
        return;
    }

    rehab_sensor_refresh_health_locked(rx_us);
    rehab_node_runtime_t *node = &g_rehab_sensor.nodes[node_index];
    became_online = !node->online;
    sequence = rehab_sensor_classify_sequence(node->seen,
                                              node->seen && !node->online,
                                              node->last_tx_seq,
                                              node->last_tx_ts_us,
                                              packet->tx_seq,
                                              packet->tx_ts_us,
                                              &gap);

    node->public_status.valid_packet_count++;
    rehab_sensor_update_pps_locked(node, rx_us);
    switch (sequence) {
        case REHAB_SEQ_GAP:
            node->public_status.gap_count += gap;
            if (g_rehab_sensor.recording) {
                node->session_gap_count += gap;
            }
            break;
        case REHAB_SEQ_DUPLICATE:
            node->public_status.duplicate_count++;
            should_update = false;
            break;
        case REHAB_SEQ_OUT_OF_ORDER:
            node->public_status.out_of_order_count++;
            should_update = false;
            break;
        case REHAB_SEQ_REBASELINE:
            node->public_status.reboot_or_rebaseline_count++;
            break;
        case REHAB_SEQ_BASELINE:
        case REHAB_SEQ_CONTIGUOUS:
            break;
    }
    rehab_sensor_note_udp_sequence_locked(node, sequence, gap, rx_us);

    if (should_update) {
        node->seen = true;
        node->online = true;
        node->has_latest = true;
        node->last_rx_us = rx_us;
        node->last_tx_seq = packet->tx_seq;
        node->last_tx_ts_us = packet->tx_ts_us;
        node->latest = *packet;
        node->public_status.last_tx_seq = packet->tx_seq;
        rehab_sensor_update_sensor_health_locked(node_index, packet, rx_us);
        rehab_sensor_refresh_health_locked(rx_us);

        if (g_rehab_sensor.recording) {
            item->packet = *packet;
            item->rx_us = rx_us;
            item->generation = g_rehab_sensor.generation;
            g_rehab_sensor.enqueue_inflight++;
            enqueue = true;
        }
    }
    rehab_sensor_unlock();

    if (became_online && should_update) {
        ESP_LOGI(TAG, "node%u online", (unsigned)packet->node_id);
    }

    if (!enqueue) {
        return;
    }

    BaseType_t sent = xQueueSend(g_rehab_sensor.queue, item, 0);
    UBaseType_t depth = uxQueueMessagesWaiting(g_rehab_sensor.queue);
    bool log_first_drop = false;

    if (rehab_sensor_lock() == ESP_OK) {
        if (g_rehab_sensor.enqueue_inflight > 0) {
            g_rehab_sensor.enqueue_inflight--;
        }
        if (sent != pdTRUE) {
            g_rehab_sensor.queue_drop_count++;
            log_first_drop = g_rehab_sensor.queue_drop_count == 1;
        }
        if (depth > g_rehab_sensor.queue_high_water) {
            g_rehab_sensor.queue_high_water = (uint32_t)depth;
        }
        rehab_sensor_unlock();
    }
    if (log_first_drop) {
        ESP_LOGW(TAG, "record queue full; dropping newest packet");
    }
}

static void rehab_sensor_note_datagram(rehab_rx_context_t *context,
                                       size_t length,
                                       int64_t rx_us)
{
    sensor_packet_t *packet = &context->packet;
    rehab_packet_validation_t validation;
    int node_index = -1;

    memset(packet, 0, sizeof(*packet));
    if (length == sizeof(sensor_packet_t)) {
        memcpy(packet, context->datagram, sizeof(*packet));
        node_index = rehab_sensor_claimed_node_index(packet);
    } else if (length > offsetof(sensor_packet_t, node_type)) {
        uint8_t node_id = context->datagram[offsetof(sensor_packet_t, node_id)];
        node_index = node_id == 1 ? REHAB_SENSOR_NODE1_INDEX :
                     node_id == 2 ? REHAB_SENSOR_NODE2_INDEX : -1;
    }

    validation = rehab_sensor_packet_validate(context->datagram, length, packet);
    if (rehab_sensor_lock() == ESP_OK) {
        if (node_index >= 0) {
            g_rehab_sensor.nodes[node_index].public_status.rx_count++;
        }
        if (validation != REHAB_PACKET_OK) {
            rehab_sensor_increment_validation_error_locked(node_index, validation);
        }
        rehab_sensor_unlock();
    }

    if (validation == REHAB_PACKET_OK) {
        rehab_sensor_handle_valid_packet(packet, rx_us, &context->record_item);
    }
}

static void rehab_sensor_set_socket_error(int system_errno, const char *message)
{
    if (rehab_sensor_lock() == ESP_OK) {
        rehab_sensor_set_error_locked(ESP_FAIL, system_errno, message);
        rehab_sensor_unlock();
    }
}

static void rehab_sensor_refresh_and_log_summary(
    int64_t now_us,
    rehab_sensor_summary_signature_t *last_summary)
{
    bool node1_online = false;
    bool node2_online = false;
    float node1_pps = 0.0f;
    float node2_pps = 0.0f;
    uint64_t node1_valid = 0;
    uint64_t node2_valid = 0;
    uint64_t drops = 0;
    uint64_t write_errors = 0;
    uint64_t udp_received[2] = {0, 0};
    uint64_t udp_gap[2] = {0, 0};
    float udp_ratio[2] = {0.0f, 0.0f};
    rehab_udp_quality_t udp_quality[2] = {
        REHAB_UDP_QUALITY_UNKNOWN,
        REHAB_UDP_QUALITY_UNKNOWN,
    };
    bool required_ok = false;
    bool node1_before;
    bool node2_before;
    bool node1_after;
    bool node2_after;
    bool writer_stalled = false;

    if (rehab_sensor_lock() != ESP_OK) {
        return;
    }
    node1_before = g_rehab_sensor.nodes[0].online;
    node2_before = g_rehab_sensor.nodes[1].online;
    rehab_sensor_refresh_health_locked(now_us);
    rehab_sensor_finalize_udp_window_locked(&g_rehab_sensor.nodes[0], now_us);
    rehab_sensor_finalize_udp_window_locked(&g_rehab_sensor.nodes[1], now_us);
    if (g_rehab_sensor.recording && g_rehab_sensor.writer_last_progress_ms > 0 &&
            (uint64_t)now_us / 1000u > g_rehab_sensor.writer_last_progress_ms +
            REHAB_SENSOR_WRITER_STALL_MS) {
        g_rehab_sensor.recording = false;
        g_rehab_sensor.state = REHAB_TRAINING_STATE_ERROR;
        g_rehab_sensor.write_error_count++;
        strlcpy(g_rehab_sensor.requested_end_reason,
                "write_error",
                sizeof(g_rehab_sensor.requested_end_reason));
        rehab_sensor_set_error_locked(ESP_ERR_TIMEOUT, 0, "writer_no_progress");
        writer_stalled = true;
    }
    node1_after = g_rehab_sensor.nodes[0].online;
    node2_after = g_rehab_sensor.nodes[1].online;
    node1_online = g_rehab_sensor.nodes[0].public_status.online;
    node2_online = g_rehab_sensor.nodes[1].public_status.online;
    node1_pps = g_rehab_sensor.nodes[0].public_status.measured_pps;
    node2_pps = g_rehab_sensor.nodes[1].public_status.measured_pps;
    node1_valid = g_rehab_sensor.nodes[0].public_status.valid_packet_count;
    node2_valid = g_rehab_sensor.nodes[1].public_status.valid_packet_count;
    drops = g_rehab_sensor.queue_drop_count;
    write_errors = g_rehab_sensor.write_error_count;
    for (unsigned index = 0; index < REHAB_SENSOR_NODE_COUNT; ++index) {
        udp_received[index] = g_rehab_sensor.nodes[index].public_status.window_received;
        udp_gap[index] = g_rehab_sensor.nodes[index].public_status.window_gap;
        udp_ratio[index] = g_rehab_sensor.nodes[index].public_status.udp_gap_ratio_pct;
        udp_quality[index] = g_rehab_sensor.nodes[index].public_status.udp_quality;
    }
    required_ok = g_rehab_sensor.required_sensors_ok;
    rehab_sensor_unlock();

    if (writer_stalled) {
        ESP_LOGE(TAG, "writer made no progress for %d ms", REHAB_SENSOR_WRITER_STALL_MS);
        xTaskNotifyGive(g_rehab_sensor.writer_task);
    }

    if (node1_before != node1_after) {
        ESP_LOGI(TAG, "node1 %s", node1_after ? "online" : "offline");
    }
    if (node2_before != node2_after) {
        ESP_LOGI(TAG, "node2 %s", node2_after ? "online" : "offline");
    }
    bool summary_changed = !last_summary->initialized ||
        last_summary->node_online[0] != node1_online ||
        last_summary->node_online[1] != node2_online ||
        last_summary->udp_quality[0] != udp_quality[0] ||
        last_summary->udp_quality[1] != udp_quality[1] ||
        last_summary->required_ok != required_ok ||
        last_summary->record_queue_drop != (drops > 0) ||
        last_summary->sd_write_error != (write_errors > 0);
    if (summary_changed) {
        ESP_LOGI(TAG,
                 "summary node1_online=%d pps=%.1f valid=%" PRIu64
                 " udp=%s gap=%.1f%% window_rx=%" PRIu64 " window_gap=%" PRIu64
                 " node2_online=%d pps=%.1f valid=%" PRIu64
                 " udp=%s gap=%.1f%% window_rx=%" PRIu64 " window_gap=%" PRIu64
                 " required_ok=%d record_queue_drop=%" PRIu64
                 " sd_write_error=%" PRIu64,
                 node1_online, node1_pps, node1_valid,
                 rehab_udp_quality_to_string(udp_quality[0]), udp_ratio[0],
                 udp_received[0], udp_gap[0],
                 node2_online, node2_pps, node2_valid,
                 rehab_udp_quality_to_string(udp_quality[1]), udp_ratio[1],
                 udp_received[1], udp_gap[1],
                 required_ok, drops, write_errors);
        last_summary->initialized = true;
        last_summary->node_online[0] = node1_online;
        last_summary->node_online[1] = node2_online;
        last_summary->udp_quality[0] = udp_quality[0];
        last_summary->udp_quality[1] = udp_quality[1];
        last_summary->required_ok = required_ok;
        last_summary->record_queue_drop = drops > 0;
        last_summary->sd_write_error = write_errors > 0;
    }
}

void rehab_sensor_rx_task(void *arg)
{
    rehab_rx_context_t *context = calloc(1, sizeof(*context));
    rehab_sensor_summary_signature_t last_summary = {0};
    (void)arg;

    if (!context) {
        rehab_sensor_set_socket_error(ENOMEM, "rx_context_allocation_failed");
        vTaskDelete(NULL);
        return;
    }

    for (;;) {
        int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
        if (sock < 0) {
            rehab_sensor_set_socket_error(errno, "udp_socket_create_failed");
            ESP_LOGE(TAG, "UDP socket create failed errno=%d", errno);
            vTaskDelay(pdMS_TO_TICKS(REHAB_SENSOR_SOCKET_RETRY_MS));
            continue;
        }

        struct timeval timeout = {
            .tv_sec = REHAB_SENSOR_SOCKET_TIMEOUT_MS / 1000,
            .tv_usec = (REHAB_SENSOR_SOCKET_TIMEOUT_MS % 1000) * 1000,
        };
        int reuse = 1;
        (void)setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
        if (setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) < 0) {
            int timeout_errno = errno;
            close(sock);
            rehab_sensor_set_socket_error(timeout_errno, "udp_receive_timeout_config_failed");
            ESP_LOGE(TAG, "UDP receive timeout configuration failed errno=%d", timeout_errno);
            vTaskDelay(pdMS_TO_TICKS(REHAB_SENSOR_SOCKET_RETRY_MS));
            continue;
        }

        struct sockaddr_in address = {
            .sin_family = AF_INET,
            .sin_port = htons(REHAB_SENSOR_UDP_PORT),
            .sin_addr.s_addr = htonl(INADDR_ANY),
        };
        if (bind(sock, (struct sockaddr *)&address, sizeof(address)) < 0) {
            int bind_errno = errno;
            close(sock);
            rehab_sensor_set_socket_error(bind_errno, "udp_bind_failed");
            ESP_LOGE(TAG, "UDP bind 0.0.0.0:%d failed errno=%d",
                     REHAB_SENSOR_UDP_PORT, bind_errno);
            vTaskDelay(pdMS_TO_TICKS(REHAB_SENSOR_SOCKET_RETRY_MS));
            continue;
        }

        ESP_LOGI(TAG, "UDP receiver listening on 0.0.0.0:%d", REHAB_SENSOR_UDP_PORT);
        for (;;) {
            struct sockaddr_in source = {0};
            socklen_t source_length = sizeof(source);
            int length = recvfrom(sock,
                                  context->datagram,
                                  sizeof(context->datagram),
                                  0,
                                  (struct sockaddr *)&source,
                                  &source_length);
            int64_t now_us = esp_timer_get_time();

            if (length < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    rehab_sensor_refresh_and_log_summary(now_us, &last_summary);
                    continue;
                }
                int receive_errno = errno;
                rehab_sensor_set_socket_error(receive_errno, "udp_receive_failed");
                ESP_LOGE(TAG, "UDP receive failed errno=%d; recreating socket", receive_errno);
                break;
            }

            rehab_sensor_note_datagram(context, (size_t)length, now_us);
            rehab_sensor_refresh_and_log_summary(now_us, &last_summary);
        }

        close(sock);
        vTaskDelay(pdMS_TO_TICKS(REHAB_SENSOR_SOCKET_RETRY_MS));
    }
}

static void rehab_sensor_copy_imu_status_locked(rehab_imu_status_t *out,
                                                unsigned imu_index,
                                                int64_t now_us)
{
    rehab_imu_runtime_t *runtime = &g_rehab_sensor.imus[imu_index];
    unsigned node_index = imu_index < 2 ? 0 : 1;
    uint32_t node_age = g_rehab_sensor.nodes[node_index].public_status.last_rx_age_ms;

    memset(out, 0, sizeof(*out));
    out->valid = runtime->initialized && runtime->flag_valid &&
                 g_rehab_sensor.nodes[node_index].online;
    out->metadata_updating = out->valid &&
        rehab_sensor_age_ms(now_us, runtime->last_metadata_advance_us) <=
        REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS;
    out->updating = out->metadata_updating;
    out->changed_recently = runtime->initialized &&
        rehab_sensor_age_ms(now_us, runtime->last_value_change_us) <=
        REHAB_SENSOR_CHANGED_RECENT_MS;
    out->source_age_ms = runtime->latest.age_ms;
    out->p4_node_age_ms = node_age;
    out->update_count = runtime->last_update_count;
    out->source_ts_us = runtime->last_ts_us;
}

esp_err_t rehab_training_get_status(rehab_training_status_t *status)
{
    int64_t now_us;
    bool current_session_active;
    struct stat storage_info = {0};

    if (!status) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(status, 0, sizeof(*status));
    if (!g_rehab_sensor.mutex) {
        return ESP_ERR_INVALID_STATE;
    }

    now_us = esp_timer_get_time();
    if (rehab_sensor_lock() != ESP_OK) {
        return ESP_ERR_TIMEOUT;
    }
    rehab_sensor_refresh_health_locked(now_us);

    status->initialized = g_rehab_sensor.initialized;
    status->state = g_rehab_sensor.state;
    status->recording = g_rehab_sensor.recording;
    status->generation = g_rehab_sensor.generation;
    current_session_active = rehab_sensor_session_is_active(
        g_rehab_sensor.state,
        g_rehab_sensor.csv_file != NULL);
    status->current_session_active = current_session_active;
    status->active_generation = current_session_active ? g_rehab_sensor.generation : 0;
    strlcpy(status->storage_base, g_rehab_sensor.storage_base, sizeof(status->storage_base));
    status->sd_ready = strcmp(g_rehab_sensor.storage_base, "/sdcard") == 0;
    if (current_session_active) {
        strlcpy(status->task_name, g_rehab_sensor.task_name, sizeof(status->task_name));
        strlcpy(status->session_path, g_rehab_sensor.session_path, sizeof(status->session_path));
        strlcpy(status->csv_path, g_rehab_sensor.csv_path, sizeof(status->csv_path));
        strlcpy(status->task_json_path,
                g_rehab_sensor.task_json_path,
                sizeof(status->task_json_path));
        status->rows_written = g_rehab_sensor.rows_written;
        status->start_log_seq = g_rehab_sensor.start_log_seq;
        status->end_log_seq = g_rehab_sensor.end_log_seq;
        status->queue_high_water = g_rehab_sensor.queue_high_water;
        status->queue_drop_count = g_rehab_sensor.queue_drop_count;
        status->write_error_count = g_rehab_sensor.write_error_count;
    }
    status->writer_alive = g_rehab_sensor.writer_alive;
    status->writer_last_progress_ms = g_rehab_sensor.writer_last_progress_ms;
    status->last_completed_session_exists = g_rehab_sensor.last_result_exists;
    if (g_rehab_sensor.last_result_exists) {
        status->last_completed_session = g_rehab_sensor.last_result;
    }
    status->node1 = g_rehab_sensor.nodes[0].public_status;
    status->node2 = g_rehab_sensor.nodes[1].public_status;

    status->emg.valid = g_rehab_sensor.emg.initialized &&
                        g_rehab_sensor.emg.flag_valid &&
                        g_rehab_sensor.nodes[0].online;
    status->emg.sample_count = g_rehab_sensor.emg.latest.sample_count;
    status->emg.window_updating = status->emg.valid &&
        rehab_sensor_age_ms(now_us, g_rehab_sensor.emg.last_metadata_advance_us) <=
        REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS;
    status->emg.group_1_updating = status->emg.window_updating;
    status->emg.group_2_updating = status->emg.window_updating;
    status->emg.group_1_changed_recently = g_rehab_sensor.emg.initialized &&
        rehab_sensor_age_ms(now_us, g_rehab_sensor.emg.group_1_last_change_us) <=
        REHAB_SENSOR_CHANGED_RECENT_MS;
    status->emg.group_2_changed_recently = g_rehab_sensor.emg.initialized &&
        rehab_sensor_age_ms(now_us, g_rehab_sensor.emg.group_2_last_change_us) <=
        REHAB_SENSOR_CHANGED_RECENT_MS;

    rehab_sensor_copy_imu_status_locked(&status->imu1, 0, now_us);
    rehab_sensor_copy_imu_status_locked(&status->imu2, 1, now_us);
    rehab_sensor_copy_imu_status_locked(&status->imu3, 2, now_us);
    rehab_sensor_copy_imu_status_locked(&status->imu4, 3, now_us);
    status->required_sensors_ok = g_rehab_sensor.required_sensors_ok;
    status->anomaly_active = g_rehab_sensor.anomaly_active;
    status->anomaly_duration_ms = g_rehab_sensor.anomaly_active ?
        rehab_sensor_age_ms(now_us, g_rehab_sensor.anomaly_start_us) : 0;
    status->anomaly_over_10s = g_rehab_sensor.anomaly_over_10s;
    status->last_error_code = g_rehab_sensor.last_error_code;
    status->last_errno = g_rehab_sensor.last_errno;
    strlcpy(status->last_error, g_rehab_sensor.last_error, sizeof(status->last_error));
    rehab_sensor_unlock();

    status->queue_depth = g_rehab_sensor.queue ?
                          (uint32_t)uxQueueMessagesWaiting(g_rehab_sensor.queue) : 0;
    if (status->sd_ready &&
            (stat(status->storage_base, &storage_info) != 0 ||
             !S_ISDIR(storage_info.st_mode))) {
        status->sd_ready = false;
    }
    return ESP_OK;
}

const char *rehab_training_state_to_string(rehab_training_state_t state)
{
    switch (state) {
        case REHAB_TRAINING_STATE_IDLE: return "idle";
        case REHAB_TRAINING_STATE_STARTING: return "starting";
        case REHAB_TRAINING_STATE_RECORDING: return "recording";
        case REHAB_TRAINING_STATE_STOPPING: return "stopping";
        case REHAB_TRAINING_STATE_ERROR: return "error";
        default: return "unknown";
    }
}

const char *rehab_udp_quality_to_string(rehab_udp_quality_t quality)
{
    switch (quality) {
        case REHAB_UDP_QUALITY_NORMAL: return "normal";
        case REHAB_UDP_QUALITY_ACCEPTABLE: return "acceptable";
        case REHAB_UDP_QUALITY_DEGRADED: return "degraded";
        case REHAB_UDP_QUALITY_UNKNOWN: return "unknown";
        default: return "unknown";
    }
}

esp_err_t rehab_sensor_recording_init(const char *storage_base_path)
{
    BaseType_t created;

    if (!storage_base_path || !storage_base_path[0]) {
        return ESP_ERR_INVALID_ARG;
    }
    if (strlen(storage_base_path) >= sizeof(g_rehab_sensor.storage_base)) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (g_rehab_sensor.init_attempted) {
        return g_rehab_sensor.initialized &&
               strcmp(g_rehab_sensor.storage_base, storage_base_path) == 0 ?
               ESP_OK : ESP_ERR_INVALID_STATE;
    }

    memset(&g_rehab_sensor, 0, sizeof(g_rehab_sensor));
    g_rehab_sensor.init_attempted = true;
    g_rehab_sensor.state = REHAB_TRAINING_STATE_IDLE;
    g_rehab_sensor.next_log_seq = 1;
    strlcpy(g_rehab_sensor.storage_base,
            storage_base_path,
            sizeof(g_rehab_sensor.storage_base));

    g_rehab_sensor.mutex = xSemaphoreCreateMutex();
    if (!g_rehab_sensor.mutex) {
        return ESP_ERR_NO_MEM;
    }
    g_rehab_sensor.queue = xQueueCreate(REHAB_SENSOR_QUEUE_LENGTH,
                                        sizeof(rehab_record_item_t));
    if (!g_rehab_sensor.queue) {
        vSemaphoreDelete(g_rehab_sensor.mutex);
        g_rehab_sensor.mutex = NULL;
        return ESP_ERR_NO_MEM;
    }

    created = xTaskCreate(rehab_training_writer_task,
                          "rehab_writer",
                          REHAB_SENSOR_WRITER_TASK_STACK_BYTES,
                          NULL,
                          REHAB_SENSOR_WRITER_TASK_PRIORITY,
                          &g_rehab_sensor.writer_task);
    if (created != pdPASS) {
        vQueueDelete(g_rehab_sensor.queue);
        vSemaphoreDelete(g_rehab_sensor.mutex);
        g_rehab_sensor.queue = NULL;
        g_rehab_sensor.mutex = NULL;
        return ESP_ERR_NO_MEM;
    }
    g_rehab_sensor.writer_alive = true;
    g_rehab_sensor.writer_ready = true;

    created = xTaskCreate(rehab_sensor_rx_task,
                          "rehab_sensor_rx",
                          REHAB_SENSOR_RX_TASK_STACK_BYTES,
                          NULL,
                          REHAB_SENSOR_RX_TASK_PRIORITY,
                          &g_rehab_sensor.rx_task);
    if (created != pdPASS) {
        vTaskDelete(g_rehab_sensor.writer_task);
        vQueueDelete(g_rehab_sensor.queue);
        vSemaphoreDelete(g_rehab_sensor.mutex);
        g_rehab_sensor.writer_task = NULL;
        g_rehab_sensor.queue = NULL;
        g_rehab_sensor.mutex = NULL;
        return ESP_ERR_NO_MEM;
    }

    if (rehab_sensor_lock() == ESP_OK) {
        g_rehab_sensor.initialized = true;
        rehab_sensor_clear_error_locked();
        rehab_sensor_unlock();
    }
    ESP_LOGI(TAG,
             "initialized udp=%d queue=%d output=%dHz storage=%s",
             REHAB_SENSOR_UDP_PORT,
             REHAB_SENSOR_QUEUE_LENGTH,
             REHAB_SENSOR_OUTPUT_RATE_HZ,
             storage_base_path);
    return ESP_OK;
}
