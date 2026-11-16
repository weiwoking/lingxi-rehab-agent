/* SPDX-License-Identifier: Apache-2.0 */
#include "unity.h"

#include "rehab_score_bridge_priv.h"

TEST_CASE("score bridge validates frozen identities and URL shape", "[rehab_score]")
{
    TEST_ASSERT_TRUE(rehab_score_contract_session_id("20260714_TASK002"));
    TEST_ASSERT_FALSE(rehab_score_contract_session_id("../20260714_TASK002"));
    TEST_ASSERT_TRUE(rehab_score_contract_csv_file("DATA_SEQ0000000001.csv"));
    TEST_ASSERT_FALSE(rehab_score_contract_csv_file("DATA_SEQ0000000001.csv.bak"));

    char normalized[64];
    TEST_ASSERT_TRUE(rehab_score_contract_base_url("http://192.168.1.100:8020/",
                                                   normalized,
                                                   sizeof(normalized)));
    TEST_ASSERT_EQUAL_STRING("http://192.168.1.100:8020", normalized);
    TEST_ASSERT_FALSE(rehab_score_contract_base_url("https://pc.local:8020",
                                                    normalized,
                                                    sizeof(normalized)));
}

TEST_CASE("task contract accepts complete sessions for arbitrary task names", "[rehab_score]")
{
    const char *supported =
        "{\"task_name\":\"水杯横向取放训练（一级）\",\"csv_file\":\"DATA_SEQ0000000001.csv\","
        "\"start_time\":\"2026-07-14T16:24:00+08:00\","
        "\"end_time\":\"2026-07-14T16:25:14+08:00\","
        "\"start_log_seq\":1,\"end_log_seq\":3700,\"end_reason\":\"user_stop\"}";
    const char *generic =
        "{\"task_name\":\"水杯拿取与饮水训练（二级）\",\"csv_file\":\"DATA_SEQ0000000001.csv\","
        "\"start_time\":\"2026-07-14T16:24:00+08:00\","
        "\"end_time\":\"2026-07-14T16:25:14+08:00\","
        "\"start_log_seq\":1,\"end_log_seq\":3700,\"end_reason\":\"completed\"}";
    const char *not_finalized =
        "{\"task_name\":\"水杯拿取与饮水训练（二级）\",\"csv_file\":\"DATA_SEQ0000000001.csv\","
        "\"start_time\":\"2026-07-14T16:24:00+08:00\","
        "\"end_time\":\"2026-07-14T16:25:14+08:00\","
        "\"start_log_seq\":1,\"end_log_seq\":3700,\"end_reason\":\"write_error\"}";
    rehab_score_task_t task = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_score_contract_parse_task(supported, &task));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_score_contract_parse_task(generic, &task));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
                      rehab_score_contract_parse_task(not_finalized, &task));
}

TEST_CASE("PC score health contract requires exact service and version", "[rehab_score]")
{
    const char ready[] =
        "{\"service\":\"rehab_score_service\",\"status\":\"ready\","
        "\"service_version\":\"0.2.1\"}";
    bool is_ready = false;
    TEST_ASSERT_EQUAL(ESP_OK,
                      rehab_score_contract_validate_health(ready,
                                                           sizeof(ready) - 1,
                                                           &is_ready));
    TEST_ASSERT_TRUE(is_ready);

    const char degraded[] =
        "{\"service\":\"rehab_score_service\",\"status\":\"degraded\","
        "\"service_version\":\"0.2.1\"}";
    TEST_ASSERT_EQUAL(ESP_OK,
                      rehab_score_contract_validate_health(degraded,
                                                           sizeof(degraded) - 1,
                                                           &is_ready));
    TEST_ASSERT_FALSE(is_ready);

    const char wrong_service[] =
        "{\"service\":\"another_service\",\"status\":\"ready\","
        "\"service_version\":\"0.2.1\"}";
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
                      rehab_score_contract_validate_health(wrong_service,
                                                           sizeof(wrong_service) - 1,
                                                           &is_ready));
}

TEST_CASE("PC machine errors remain stable through bridge contracts", "[rehab_score]")
{
    char error_code[REHAB_SCORE_LAST_ERROR_LEN] = {0};
    TEST_ASSERT_EQUAL(
        ESP_OK,
        rehab_score_contract_parse_error_code(
            "{\"error_code\":\"invalid_task_contract\",\"message\":\"invalid\"}",
            error_code));
    TEST_ASSERT_EQUAL_STRING("invalid_task_contract", error_code);

    rehab_score_job_response_t status = {0};
    TEST_ASSERT_EQUAL(
        ESP_OK,
        rehab_score_contract_parse_job_status(
            "{\"job_id\":\"20260714_TASK002\","
            "\"session_id\":\"20260714_TASK002\",\"state\":\"failed\","
            "\"error_code\":\"scoring_failed\"}",
            "20260714_TASK002",
            &status));
    TEST_ASSERT_EQUAL(REHAB_SCORE_STATE_FAILED, status.state);
    TEST_ASSERT_EQUAL_STRING("scoring_failed", status.error_code);
}
