/* SPDX-License-Identifier: Apache-2.0 */
#include "unity.h"

#include <stdlib.h>

#include "rehab_score_bridge_priv.h"

static const char QUERY_RESULT[] =
    "{\"schema_version\":\"rehab_score_result_v1\","
    "\"session_id\":\"20260714_TASK006\",\"task_name\":\"水杯横向取放训练（一级）\","
    "\"task_number\":12,\"valid\":true,\"invalid_reason\":null,"
    "\"average_score\":48.71,\"max_score\":51.0,\"min_score\":46.34,"
    "\"repetition_count\":6,\"target_repetition_count\":10,"
    "\"consistency_score\":98.46,\"data_quality_level\":\"use_with_caution\","
    "\"warnings\":[\"large sampling gaps over 100 ms detected\"],"
    "\"compensation_detected\":false,\"compensation_action_count\":0,"
    "\"score_version\":\"v3.0\","
    "\"model_version\":\"weighted_dtw_imu_plus_emg_v1.0\","
    "\"completed_at\":\"2026-07-14T15:47:59.723453+00:00\","
    "\"pc_job_id\":\"20260714_TASK006\"}";

TEST_CASE("score query copies a validated 20-field result", "[rehab_score]")
{
    rehab_score_query_result_t *result = calloc(1, sizeof(*result));
    TEST_ASSERT_NOT_NULL(result);
    TEST_ASSERT_EQUAL(
        ESP_OK,
        rehab_score_contract_parse_result(QUERY_RESULT,
                                          sizeof(QUERY_RESULT) - 1,
                                          "20260714_TASK006",
                                          "20260714_TASK006",
                                          "水杯横向取放训练（一级）",
                                          result));
    TEST_ASSERT_TRUE(result->result_available);
    TEST_ASSERT_TRUE(result->valid);
    TEST_ASSERT_TRUE(result->average_score_available);
    TEST_ASSERT_EQUAL_DOUBLE(48.71, result->average_score);
    TEST_ASSERT_EQUAL_UINT32(6, result->repetition_count);
    TEST_ASSERT_EQUAL_UINT32(10, result->target_repetition_count);
    TEST_ASSERT_EQUAL_UINT32(1, result->warning_count);
    TEST_ASSERT_EQUAL_STRING("large sampling gaps over 100 ms detected",
                             result->warnings_summary);
    free(result);
}

TEST_CASE("score query rejects mismatched result identity", "[rehab_score]")
{
    rehab_score_query_result_t *result = calloc(1, sizeof(*result));
    TEST_ASSERT_NOT_NULL(result);
    TEST_ASSERT_EQUAL(
        ESP_ERR_INVALID_RESPONSE,
        rehab_score_contract_parse_result(QUERY_RESULT,
                                          sizeof(QUERY_RESULT) - 1,
                                          "20260714_TASK005",
                                          "20260714_TASK006",
                                          "水杯横向取放训练（一级）",
                                          result));
    TEST_ASSERT_FALSE(result->result_available);
    free(result);
}
