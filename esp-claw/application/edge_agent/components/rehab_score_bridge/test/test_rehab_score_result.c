/* SPDX-License-Identifier: Apache-2.0 */
#include "unity.h"

#include "rehab_score_bridge_priv.h"

static const char VALID_RESULT[] =
    "{\"schema_version\":\"rehab_score_result_v1\","
    "\"session_id\":\"20260714_TASK002\",\"task_name\":\"水杯横向取放训练（一级）\","
    "\"task_number\":12,\"valid\":true,\"invalid_reason\":null,"
    "\"average_score\":44.42,\"max_score\":47.62,\"min_score\":40.69,"
    "\"repetition_count\":16,\"target_repetition_count\":10,"
    "\"consistency_score\":98.05,\"data_quality_level\":\"use_with_caution\","
    "\"warnings\":[],\"compensation_detected\":false,"
    "\"compensation_action_count\":0,\"score_version\":\"v3.0\","
    "\"model_version\":\"weighted_dtw_imu_plus_emg_v1.0\","
    "\"completed_at\":\"2026-07-14T20:31:00+08:00\","
    "\"pc_job_id\":\"20260714_TASK002\"}";

static const char GENERIC_RESULT[] =
    "{\"schema_version\":\"rehab_score_result_v1\","
    "\"session_id\":\"20260721_TASK002\",\"task_name\":\"水杯拿取与饮水训练（二级）\","
    "\"task_number\":21,\"valid\":true,\"invalid_reason\":null,"
    "\"average_score\":74.0,\"max_score\":88.0,\"min_score\":61.0,"
    "\"repetition_count\":7,\"target_repetition_count\":8,"
    "\"consistency_score\":82.0,\"data_quality_level\":\"good\","
    "\"warnings\":[],\"compensation_detected\":true,"
    "\"compensation_action_count\":2,\"score_version\":\"v3.0\","
    "\"model_version\":\"weighted_dtw_imu_plus_emg_v1.0\","
    "\"completed_at\":\"2026-07-21T20:31:00+08:00\","
    "\"pc_job_id\":\"20260721_TASK002\"}";

TEST_CASE("compact score result binds session job and task", "[rehab_score]")
{
    TEST_ASSERT_EQUAL(
        ESP_OK,
        rehab_score_contract_validate_result(VALID_RESULT,
                                             sizeof(VALID_RESULT) - 1,
                                             "20260714_TASK002",
                                             "20260714_TASK002",
                                             "水杯横向取放训练（一级）"));
    TEST_ASSERT_NOT_EQUAL(
        ESP_OK,
        rehab_score_contract_validate_result(VALID_RESULT,
                                             sizeof(VALID_RESULT) - 1,
                                             "20260714_TASK003",
                                             "20260714_TASK002",
                                             "水杯横向取放训练（一级）"));
}

TEST_CASE("compact score result accepts generic task metadata", "[rehab_score]")
{
    TEST_ASSERT_EQUAL(
        ESP_OK,
        rehab_score_contract_validate_result(GENERIC_RESULT,
                                             sizeof(GENERIC_RESULT) - 1,
                                             "20260721_TASK002",
                                             "20260721_TASK002",
                                             "水杯拿取与饮水训练（二级）"));
}
