/* SPDX-License-Identifier: Apache-2.0 */
#include "unity.h"

#include "rehab_score_bridge_priv.h"

TEST_CASE("restart recovery actions are deterministic", "[rehab_score]")
{
    TEST_ASSERT_EQUAL(REHAB_SCORE_RECOVERY_CREATE_PENDING,
                      rehab_score_contract_recovery_action(false,
                                                           REHAB_SCORE_STATE_PENDING));
    TEST_ASSERT_EQUAL(REHAB_SCORE_RECOVERY_RESET_PENDING,
                      rehab_score_contract_recovery_action(true,
                                                           REHAB_SCORE_STATE_UPLOADING));
    TEST_ASSERT_EQUAL(REHAB_SCORE_RECOVERY_POLL,
                      rehab_score_contract_recovery_action(true,
                                                           REHAB_SCORE_STATE_ACCEPTED));
    TEST_ASSERT_EQUAL(REHAB_SCORE_RECOVERY_POLL,
                      rehab_score_contract_recovery_action(true,
                                                           REHAB_SCORE_STATE_SCORING));
    TEST_ASSERT_EQUAL(REHAB_SCORE_RECOVERY_VALIDATE_RESULT,
                      rehab_score_contract_recovery_action(true,
                                                           REHAB_SCORE_STATE_SUCCEEDED));
    TEST_ASSERT_EQUAL(REHAB_SCORE_RECOVERY_HOLD_FAILED,
                      rehab_score_contract_recovery_action(true,
                                                           REHAB_SCORE_STATE_FAILED));
}
