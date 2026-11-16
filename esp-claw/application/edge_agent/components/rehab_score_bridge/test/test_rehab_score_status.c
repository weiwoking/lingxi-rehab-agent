/* SPDX-License-Identifier: Apache-2.0 */
#include "unity.h"

#include <stdlib.h>

#include "rehab_score_bridge_priv.h"

TEST_CASE("score status accepts exactly six contract fields", "[rehab_score]")
{
    const char *json =
        "{\"schema_version\":\"rehab_score_status_v1\",\"state\":\"pending\","
        "\"attempt\":0,\"pc_job_id\":null,"
        "\"updated_at\":\"2026-07-14T20:30:00+08:00\",\"last_error\":null}";
    rehab_score_status_t *status = calloc(1, sizeof(*status));
    TEST_ASSERT_NOT_NULL(status);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_score_contract_parse_status(json, status));
    TEST_ASSERT_EQUAL(REHAB_SCORE_STATE_PENDING, status->state);
    TEST_ASSERT_EQUAL_UINT32(0, status->attempt);

    const char *extra =
        "{\"schema_version\":\"rehab_score_status_v1\",\"state\":\"pending\","
        "\"attempt\":0,\"pc_job_id\":null,"
        "\"updated_at\":\"2026-07-14T20:30:00+08:00\",\"last_error\":null,"
        "\"unexpected\":true}";
    TEST_ASSERT_NOT_EQUAL(ESP_OK, rehab_score_contract_parse_status(extra, status));
    free(status);
}
