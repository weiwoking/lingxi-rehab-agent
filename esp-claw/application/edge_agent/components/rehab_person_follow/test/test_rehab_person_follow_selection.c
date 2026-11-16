// SPDX-License-Identifier: Apache-2.0
#include "unity.h"
#include "../src/rehab_person_follow_priv.h"

static rehab_person_follow_detection_t detection(
    float x1, float x2, float center, float confidence, float area)
{
    return (rehab_person_follow_detection_t) {
        .x1 = x1, .y1 = 10, .x2 = x2, .y2 = 350,
        .center_x = center, .center_y = 0.5f,
        .confidence = confidence, .area_normalized = area,
    };
}

TEST_CASE("person follow dedup keeps higher confidence overlapping box",
          "[person_follow][selection]")
{
    rehab_person_follow_detection_t input[] = {
        detection(100, 300, 0.31f, 0.70f, 0.30f),
        detection(105, 305, 0.32f, 0.92f, 0.29f),
        detection(400, 500, 0.70f, 0.80f, 0.15f),
    };
    rehab_person_follow_detection_t output[3] = {0};
    TEST_ASSERT_EQUAL_UINT32(2, rehab_person_follow_deduplicate(
        input, 3, output, 3));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.92f, output[0].confidence);
}

TEST_CASE("person follow selection uses central area then continuity",
          "[person_follow][selection]")
{
    rehab_person_follow_detection_t input[] = {
        detection(20, 120, 0.11f, 0.99f, 0.10f),
        detection(180, 420, 0.47f, 0.80f, 0.35f),
        detection(350, 580, 0.73f, 0.90f, 0.30f),
    };
    rehab_person_follow_detection_t selected = {0};
    TEST_ASSERT_TRUE(rehab_person_follow_select_target(
        input, 3, false, 0, &selected));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.47f, selected.center_x);
    TEST_ASSERT_TRUE(rehab_person_follow_select_target(
        input, 3, true, 0.75f, &selected));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.73f, selected.center_x);
}

TEST_CASE("person follow selection handles one box and two noncentral people",
          "[person_follow][selection]")
{
    rehab_person_follow_detection_t one =
        detection(100, 200, 0.20f, 0.8f, 0.1f);
    rehab_person_follow_detection_t selected = {0};
    TEST_ASSERT_TRUE(rehab_person_follow_select_target(
        &one, 1, false, 0, &selected));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.20f, selected.center_x);
    rehab_person_follow_detection_t sides[] = {
        detection(10, 110, 0.10f, 0.95f, 0.1f),
        detection(510, 630, 0.80f, 0.70f, 0.12f),
    };
    TEST_ASSERT_TRUE(rehab_person_follow_select_target(
        sides, 2, false, 0, &selected));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.80f, selected.center_x);
}

TEST_CASE("person follow target classification uses 20 60 20 zones",
          "[person_follow][selection]")
{
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_TARGET_NO_TARGET,
        rehab_person_follow_classify_target(false, 0.5f));
    const struct {
        float center_x;
        rehab_person_follow_target_zone_t expected;
    } cases[] = {
        {0.0f, REHAB_PERSON_FOLLOW_TARGET_LEFT},
        {0.1999f, REHAB_PERSON_FOLLOW_TARGET_LEFT},
        {0.20f, REHAB_PERSON_FOLLOW_TARGET_CENTER},
        {0.50f, REHAB_PERSON_FOLLOW_TARGET_CENTER},
        {0.80f, REHAB_PERSON_FOLLOW_TARGET_CENTER},
        {0.8001f, REHAB_PERSON_FOLLOW_TARGET_RIGHT},
        {1.0f, REHAB_PERSON_FOLLOW_TARGET_RIGHT},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        TEST_ASSERT_EQUAL(cases[i].expected,
            rehab_person_follow_classify_target(true, cases[i].center_x));
    }
}
