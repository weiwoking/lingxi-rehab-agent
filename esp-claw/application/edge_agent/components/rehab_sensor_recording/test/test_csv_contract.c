/* Source-level Unity tests. Add to an ESP-IDF component test app before running. */
#include <float.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "unity.h"
#include "../src/rehab_sensor_internal.h"

static size_t test_csv_column_count(const char *line)
{
    size_t count = line && line[0] ? 1 : 0;
    for (const char *cursor = line; cursor && *cursor; ++cursor) {
        if (*cursor == ',') {
            count++;
        }
    }
    return count;
}

static sensor_packet_t test_node_packet(uint8_t node_id, float base)
{
    sensor_packet_t packet = {0};
    packet.node_id = node_id;
    packet.node_type = node_id == 1 ? 1 : 2;
    packet.flags = SENSOR_FLAG_IMU1_VALID | SENSOR_FLAG_IMU2_VALID;
    packet.tx_seq = node_id * 100;
    if (node_id == 1) {
        packet.flags |= SENSOR_FLAG_EMG_VALID;
        packet.emg.sample_count = 8;
        packet.emg.latest_ch1 = 11;
        packet.emg.latest_ch2 = 12;
        packet.emg.latest_ch3 = 13;
        packet.emg.latest_ch4 = 14;
        packet.emg.rms_ch1 = packet.emg.mav_ch1 = base + 1.0f;
        packet.emg.rms_ch2 = packet.emg.mav_ch2 = base + 2.0f;
        packet.emg.rms_ch3 = packet.emg.mav_ch3 = base + 3.0f;
        packet.emg.rms_ch4 = packet.emg.mav_ch4 = base + 4.0f;
    }
    packet.imu1.age_ms = 3;
    packet.imu1.angle_x_deg = base + 10.0f;
    packet.imu1.gyro_x_dps = base + 20.0f;
    packet.imu1.acc_x_g = base + 30.0f;
    packet.imu1.lin_acc_x_g = base + 40.0f;
    packet.imu2.age_ms = 4;
    packet.imu2.angle_x_deg = base + 50.0f;
    packet.imu2.gyro_x_dps = base + 60.0f;
    packet.imu2.acc_x_g = base + 70.0f;
    packet.imu2.lin_acc_x_g = base + 80.0f;
    return packet;
}

static void test_build_row(rehab_fused_row_t *row,
                           rehab_writer_node_t nodes[2],
                           rehab_sensor_node_status_t status[2],
                           uint64_t log_seq)
{
    const uint64_t gap[2] = {2, 3};
    rehab_sensor_build_fused_row(nodes, status, gap, 123456000, log_seq, row);
}

TEST_CASE("CSV header is the exact frozen 86-column order and is emitted without embedded newline", "[rehab_csv]")
{
    static const char expected[] =
        "log_seq,time_ms,output_rate_hz,flags,node1_rx,node1_tx_seq,node1_tx_gap,node1_age_ms,node2_rx,node2_tx_seq,node2_tx_gap,node2_age_ms,emg_valid,emg_latest_1,emg_latest_2,emg_latest_3,emg_latest_4,emg_rms_1,emg_rms_2,emg_rms_3,emg_rms_4,emg_mav_1,emg_mav_2,emg_mav_3,emg_mav_4,emg_peak_1,emg_peak_2,emg_peak_3,emg_peak_4,emg_n,imu1_valid,imu1_age_ms,imu1_roll,imu1_pitch,imu1_yaw,imu1_gx,imu1_gy,imu1_gz,imu1_ax,imu1_ay,imu1_az,imu1_lax,imu1_lay,imu1_laz,imu2_valid,imu2_age_ms,imu2_roll,imu2_pitch,imu2_yaw,imu2_gx,imu2_gy,imu2_gz,imu2_ax,imu2_ay,imu2_az,imu2_lax,imu2_lay,imu2_laz,imu3_valid,imu3_age_ms,imu3_roll,imu3_pitch,imu3_yaw,imu3_gx,imu3_gy,imu3_gz,imu3_ax,imu3_ay,imu3_az,imu3_lax,imu3_lay,imu3_laz,imu4_valid,imu4_age_ms,imu4_roll,imu4_pitch,imu4_yaw,imu4_gx,imu4_gy,imu4_gz,imu4_ax,imu4_ay,imu4_az,imu4_lax,imu4_lay,imu4_laz";
    TEST_ASSERT_EQUAL_STRING(expected, REHAB_FUSED_CSV_HEADER);
    TEST_ASSERT_EQUAL_UINT32(86, test_csv_column_count(REHAB_FUSED_CSV_HEADER));
    TEST_ASSERT_NULL(strchr(REHAB_FUSED_CSV_HEADER, '\n'));
}

TEST_CASE("CSV formatter keeps 50Hz left-right mapping and exactly 86 values", "[rehab_csv]")
{
    rehab_writer_node_t nodes[2] = {
        {.has_packet = true, .packet = test_node_packet(1, 100.0f), .rx_us = 123450000},
        {.has_packet = true, .packet = test_node_packet(2, 200.0f), .rx_us = 123450000},
    };
    rehab_sensor_node_status_t status[2] = {
        {.online = true, .last_rx_age_ms = 6, .rx_count = 1000, .last_tx_seq = 100},
        {.online = true, .last_rx_age_ms = 7, .rx_count = 2000, .last_tx_seq = 200},
    };
    rehab_fused_row_t row;
    char line[REHAB_SENSOR_CSV_LINE_BUFFER_SIZE];
    char copy[REHAB_SENSOR_CSV_LINE_BUFFER_SIZE];
    char *tokens[86] = {0};
    char *save = NULL;
    size_t count = 0;

    test_build_row(&row, nodes, status, 1);
    int length = rehab_sensor_format_csv_row(line, sizeof(line), &row);
    TEST_ASSERT_GREATER_THAN(0, length);
    TEST_ASSERT_LESS_THAN((int)sizeof(line), length);
    TEST_ASSERT_EQUAL_UINT32(86, test_csv_column_count(line));

    strlcpy(copy, line, sizeof(copy));
    for (char *token = strtok_r(copy, ",", &save);
            token && count < 86;
            token = strtok_r(NULL, ",", &save)) {
        tokens[count++] = token;
    }
    TEST_ASSERT_EQUAL_UINT32(86, count);
    TEST_ASSERT_EQUAL_STRING("50", tokens[2]);
    TEST_ASSERT_EQUAL_STRING("1", tokens[12]);
    TEST_ASSERT_EQUAL_STRING("110.00", tokens[32]);
    TEST_ASSERT_EQUAL_STRING("250.00", tokens[60]);
    TEST_ASSERT_EQUAL_STRING("1", tokens[58]);
}

TEST_CASE("CSV fusion reuses online snapshots and zeros an offline side", "[rehab_csv]")
{
    rehab_writer_node_t nodes[2] = {
        {.has_packet = true, .packet = test_node_packet(1, 10.0f)},
        {.has_packet = true, .packet = test_node_packet(2, 20.0f)},
    };
    rehab_sensor_node_status_t status[2] = {
        {.online = true, .last_rx_age_ms = 20},
        {.online = true, .last_rx_age_ms = 20},
    };
    rehab_fused_row_t first;
    rehab_fused_row_t reused;
    rehab_fused_row_t offline;

    test_build_row(&first, nodes, status, 10);
    status[0].last_rx_age_ms = 40;
    status[1].last_rx_age_ms = 40;
    test_build_row(&reused, nodes, status, 11);
    TEST_ASSERT_EQUAL_FLOAT(first.imu[0].roll_deg, reused.imu[0].roll_deg);
    TEST_ASSERT_GREATER_THAN(first.imu[0].age_ms, reused.imu[0].age_ms);

    status[1].online = false;
    status[1].last_rx_age_ms = 1001;
    test_build_row(&offline, nodes, status, 12);
    TEST_ASSERT_FALSE(offline.imu[2].valid);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, offline.imu[2].roll_deg);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, offline.imu[3].gyro_x_dps);

    nodes[1].has_packet = false;
    status[1].online = false;
    test_build_row(&offline, nodes, status, 13);
    TEST_ASSERT_FALSE(offline.imu[2].valid);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, offline.imu[2].acc_x_g);
}

TEST_CASE("CSV formatter sanitizes non-finite input and fits the 4096-byte buffer", "[rehab_csv]")
{
    rehab_writer_node_t nodes[2] = {
        {.has_packet = true, .packet = test_node_packet(1, 10.0f)},
        {.has_packet = true, .packet = test_node_packet(2, 20.0f)},
    };
    rehab_sensor_node_status_t status[2] = {
        {.online = true}, {.online = true},
    };
    rehab_fused_row_t row;
    char line[REHAB_SENSOR_CSV_LINE_BUFFER_SIZE];
    char tiny[8];

    nodes[0].packet.imu1.angle_x_deg = NAN;
    test_build_row(&row, nodes, status, 1);
    TEST_ASSERT_FALSE(row.imu[0].valid);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, row.imu[0].roll_deg);
    TEST_ASSERT_NOT_EQUAL(0, row.sanitized_node_mask & 1u);

    for (unsigned index = 0; index < 4; ++index) {
        row.imu[index].valid = true;
        row.imu[index].roll_deg = FLT_MAX;
        row.imu[index].pitch_deg = -FLT_MAX;
        row.imu[index].yaw_deg = FLT_MAX;
        row.imu[index].gyro_x_dps = FLT_MAX;
        row.imu[index].acc_x_g = FLT_MAX;
        row.imu[index].lin_acc_x_g = FLT_MAX;
    }
    int length = rehab_sensor_format_csv_row(line, sizeof(line), &row);
    TEST_ASSERT_GREATER_THAN(0, length);
    TEST_ASSERT_LESS_THAN((int)sizeof(line), length);
    TEST_ASSERT_NULL(strstr(line, "nan"));
    TEST_ASSERT_NULL(strstr(line, "inf"));
    TEST_ASSERT_GREATER_OR_EQUAL((int)sizeof(tiny),
                                 rehab_sensor_format_csv_row(tiny, sizeof(tiny), &row));
}

TEST_CASE("task JSON remains exactly seven fields and log sequences stay explicit", "[rehab_csv]")
{
    char *json = rehab_sensor_render_task_json("task",
                                                "DATA_SEQ0000000001.csv",
                                                "2026-07-14T10:30:15+08:00",
                                                "2026-07-14T10:31:15+08:00",
                                                1,
                                                50,
                                                "completed");
    TEST_ASSERT_NOT_NULL(json);
    cJSON *root = cJSON_Parse(json);
    TEST_ASSERT_NOT_NULL(root);
    TEST_ASSERT_EQUAL_INT(7, cJSON_GetArraySize(root));
    TEST_ASSERT_NOT_NULL(cJSON_GetObjectItemCaseSensitive(root, "task_name"));
    TEST_ASSERT_NOT_NULL(cJSON_GetObjectItemCaseSensitive(root, "csv_file"));
    TEST_ASSERT_NOT_NULL(cJSON_GetObjectItemCaseSensitive(root, "start_time"));
    TEST_ASSERT_NOT_NULL(cJSON_GetObjectItemCaseSensitive(root, "end_time"));
    TEST_ASSERT_EQUAL_DOUBLE(1, cJSON_GetObjectItemCaseSensitive(root, "start_log_seq")->valuedouble);
    TEST_ASSERT_EQUAL_DOUBLE(50, cJSON_GetObjectItemCaseSensitive(root, "end_log_seq")->valuedouble);
    TEST_ASSERT_NOT_NULL(cJSON_GetObjectItemCaseSensitive(root, "end_reason"));
    cJSON_Delete(root);
    cJSON_free(json);
}

TEST_CASE("status strings retain complete values and NUL capacity", "[rehab_status]")
{
    char mode[sizeof("mock")] = {0};
    char storage[REHAB_TRAINING_STORAGE_BASE_LEN] = {0};
    char error_code[REHAB_TRAINING_ERROR_CODE_LEN] = {0};

    TEST_ASSERT_LESS_THAN(sizeof(mode), strlcpy(mode, "mock", sizeof(mode)));
    TEST_ASSERT_LESS_THAN(sizeof(storage), strlcpy(storage, "/sdcard", sizeof(storage)));
    TEST_ASSERT_LESS_THAN(sizeof(error_code),
                          strlcpy(error_code, "not_initialized", sizeof(error_code)));
    TEST_ASSERT_EQUAL_STRING("mock", mode);
    TEST_ASSERT_EQUAL_STRING("idle",
                             rehab_training_state_to_string(REHAB_TRAINING_STATE_IDLE));
    TEST_ASSERT_EQUAL_STRING("/sdcard", storage);
    TEST_ASSERT_EQUAL_STRING("not_initialized", error_code);
}

TEST_CASE("last completed session remains distinct from the current session", "[rehab_status]")
{
    rehab_training_result_t last = {0};
    rehab_training_result_t repeated;
    bool exists = false;

    TEST_ASSERT_FALSE(rehab_sensor_last_completed_matches(exists,
                                                          &last,
                                                          REHAB_TRAINING_STATE_IDLE,
                                                          0));
    TEST_ASSERT_FALSE(rehab_sensor_session_is_active(REHAB_TRAINING_STATE_IDLE, false));

    exists = true;
    last.accepted = true;
    last.state = REHAB_TRAINING_STATE_IDLE;
    last.generation = 1;
    last.rows_written = 3700;
    last.start_log_seq = 1;
    last.end_log_seq = 3700;
    last.duration_ms = 74400;
    strlcpy(last.task_name, "completed task", sizeof(last.task_name));
    strlcpy(last.session_path, "/sdcard/rehab_sessions/20260714_TASK002",
            sizeof(last.session_path));
    strlcpy(last.end_reason, "user_stop", sizeof(last.end_reason));

    TEST_ASSERT_TRUE(rehab_sensor_last_completed_matches(exists,
                                                         &last,
                                                         REHAB_TRAINING_STATE_IDLE,
                                                         1));
    TEST_ASSERT_GREATER_THAN_UINT32(0, last.generation);
    TEST_ASSERT_EQUAL_UINT64(3700, last.rows_written);

    /* Starting generation 2 does not erase the generation 1 completed result. */
    TEST_ASSERT_TRUE(rehab_sensor_session_is_active(REHAB_TRAINING_STATE_RECORDING, true));
    TEST_ASSERT_FALSE(rehab_sensor_last_completed_matches(exists,
                                                          &last,
                                                          REHAB_TRAINING_STATE_RECORDING,
                                                          2));
    TEST_ASSERT_EQUAL_UINT64(3700, last.rows_written);

    /* The next completion replaces it, and repeated stop selection is read-only. */
    last.generation = 2;
    last.rows_written = 100;
    last.end_log_seq = 3800;
    repeated = last;
    TEST_ASSERT_TRUE(rehab_sensor_last_completed_matches(exists,
                                                         &last,
                                                         REHAB_TRAINING_STATE_IDLE,
                                                         2));
    TEST_ASSERT_TRUE(rehab_sensor_last_completed_matches(exists,
                                                         &last,
                                                         REHAB_TRAINING_STATE_IDLE,
                                                         2));
    TEST_ASSERT_EQUAL_MEMORY(&repeated, &last, sizeof(last));
}

TEST_CASE("periodic writer stops accepting regular rows outside recording", "[rehab_csv]")
{
    TEST_ASSERT_TRUE(rehab_sensor_writer_allows_periodic_row(REHAB_TRAINING_STATE_RECORDING));
    TEST_ASSERT_FALSE(rehab_sensor_writer_allows_periodic_row(REHAB_TRAINING_STATE_IDLE));
    TEST_ASSERT_FALSE(rehab_sensor_writer_allows_periodic_row(REHAB_TRAINING_STATE_STOPPING));
    TEST_ASSERT_FALSE(rehab_sensor_writer_allows_periodic_row(REHAB_TRAINING_STATE_ERROR));
}
