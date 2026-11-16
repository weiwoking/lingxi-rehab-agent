// SPDX-License-Identifier: Apache-2.0
#include <string.h>

#include "unity.h"
#include "../src/rehab_person_follow_priv.h"

static rehab_person_follow_observation_t box(
    float x1_ratio, float x2_ratio, float y1_ratio, float y2_ratio)
{
    return (rehab_person_follow_observation_t) {
        .valid = true,
        .target_found = true,
        .confidence = 0.9f,
        .center_x = (x1_ratio + x2_ratio) * 0.5f,
        .center_y = (y1_ratio + y2_ratio) * 0.5f,
        .bbox_x1 = x1_ratio * PERSON_FOLLOW_IMAGE_WIDTH,
        .bbox_y1 = y1_ratio * PERSON_FOLLOW_IMAGE_HEIGHT,
        .bbox_x2 = x2_ratio * PERSON_FOLLOW_IMAGE_WIDTH,
        .bbox_y2 = y2_ratio * PERSON_FOLLOW_IMAGE_HEIGHT,
    };
}

static rehab_person_follow_observation_t target(
    float center_x, float width_ratio, float y1_ratio, float y2_ratio)
{
    return box(center_x - width_ratio * 0.5f,
               center_x + width_ratio * 0.5f,
               y1_ratio, y2_ratio);
}

static rehab_person_follow_observation_t no_target(void)
{
    return (rehab_person_follow_observation_t) {.valid = true};
}

static rehab_person_follow_decision_context_t context(void)
{
    rehab_person_follow_decision_context_t result = {
        .state = REHAB_PERSON_FOLLOW_STATE_SENSE,
        .generation = 7U,
    };
    rehab_person_follow_reset_adaptive_turn(
        &result, REHAB_PERSON_FOLLOW_TURN_INITIAL_MS);
    return result;
}

static rehab_person_follow_decision_t decide_at(
    rehab_person_follow_decision_context_t *ctx,
    uint32_t frame_id,
    rehab_person_follow_observation_t observation)
{
    rehab_person_follow_decision_t result = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_decide(
        ctx, &observation, frame_id, ctx->generation, false, 3U,
        REHAB_PERSON_FOLLOW_TURN_INITIAL_MS, 500U, &result));
    return result;
}

static rehab_person_follow_decision_t step(
    rehab_person_follow_decision_context_t *ctx,
    uint32_t frame_id,
    rehab_person_follow_observation_t observation,
    bool action_succeeds)
{
    rehab_person_follow_decision_t result = decide_at(ctx, frame_id,
                                                       observation);
    rehab_person_follow_commit_action(
        ctx, &result,
        action_succeeds && result.action != REHAB_PERSON_FOLLOW_ACTION_NONE);
    return result;
}

static void assert_action(
    rehab_person_follow_decision_t decision,
    rehab_person_follow_action_t action,
    uint32_t hold_ms)
{
    TEST_ASSERT_EQUAL(action, decision.action);
    TEST_ASSERT_EQUAL_UINT32(hold_ms, decision.hold_ms);
}

static void trigger_first(
    rehab_person_follow_decision_context_t *ctx, uint32_t frame_id)
{
    rehab_person_follow_decision_t trigger = decide_at(
        ctx, frame_id, target(0.5f, 0.25f, 0.2f, 0.8f));
    TEST_ASSERT_EQUAL_STRING("first_align_trigger_width", trigger.reason);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE, trigger.action);
    TEST_ASSERT_TRUE(trigger.stop_rolling);
    rehab_person_follow_commit_align_stop(ctx, &trigger, true);
}

static void complete_first(rehab_person_follow_decision_context_t *ctx)
{
    trigger_first(ctx, 1U);
    TEST_ASSERT_EQUAL_STRING("first_align_stable_1_of_2",
        step(ctx, 2U, target(0.5f, 0.20f, 0.2f, 0.8f), true).reason);
    TEST_ASSERT_EQUAL_STRING("first_align_stable_2_of_2_complete",
        step(ctx, 3U, target(0.5f, 0.10f, 0.2f, 0.8f), true).reason);
    TEST_ASSERT_TRUE(ctx->first_align_completed);
}

static void trigger_final(
    rehab_person_follow_decision_context_t *ctx, uint32_t frame_id)
{
    rehab_person_follow_decision_t trigger = decide_at(
        ctx, frame_id, target(0.5f, 0.60f, 0.2f, 0.8f));
    TEST_ASSERT_EQUAL_STRING("final_align_trigger_width", trigger.reason);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE, trigger.action);
    TEST_ASSERT_TRUE(trigger.stop_rolling);
    rehab_person_follow_commit_align_stop(ctx, &trigger, true);
}

TEST_CASE("new run resets the unified turn authority",
          "[person_follow][unified_turn][reset]")
{
    rehab_person_follow_decision_context_t ctx = context();
    TEST_ASSERT_FALSE(ctx.target_acquired_once);
    TEST_ASSERT_EQUAL_UINT32(250U, ctx.turn_hold_ms);
    TEST_ASSERT_FALSE(ctx.turn_check_pending);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE,
                      ctx.last_successful_turn_direction);
}

TEST_CASE("pre-acquisition no target always searches RIGHT 250 without pending",
          "[person_follow][initial_search][fixed_right]")
{
    rehab_person_follow_decision_context_t ctx = context();
    for (uint32_t frame_id = 1U; frame_id <= 4U; ++frame_id) {
        rehab_person_follow_decision_t decision = step(
            &ctx, frame_id, no_target(), true);
        assert_action(decision,
                      REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT, 250U);
        TEST_ASSERT_EQUAL_STRING("initial_search_right_fixed",
                                 decision.reason);
        TEST_ASSERT_FALSE(ctx.target_acquired_once);
        TEST_ASSERT_EQUAL_UINT32(250U, ctx.turn_hold_ms);
        TEST_ASSERT_FALSE(ctx.turn_check_pending);
        TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE,
                          ctx.last_successful_turn_direction);
    }
}

TEST_CASE("pre-acquisition technical failure stops without acquiring target",
          "[person_follow][initial_search][technical]")
{
    rehab_person_follow_decision_context_t ctx = context();
    rehab_person_follow_decision_t failure = step(
        &ctx, 1U, (rehab_person_follow_observation_t) {0}, false);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE, failure.action);
    TEST_ASSERT_FALSE(ctx.target_acquired_once);
    TEST_ASSERT_FALSE(ctx.turn_check_pending);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE,
                      ctx.last_successful_turn_direction);
}

TEST_CASE("first valid LEFT RIGHT and CENTER enter unified turn cleanly",
          "[person_follow][initial_search][first_target]")
{
    const struct {
        float center_x;
        rehab_person_follow_action_t expected_action;
        bool action_succeeds;
    } cases[] = {
        {0.1f, REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, true},
        {0.9f, REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, true},
        {0.5f, REHAB_PERSON_FOLLOW_ACTION_FORWARD, false},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        rehab_person_follow_decision_context_t ctx = context();
        for (uint32_t frame_id = 1U; frame_id <= 3U; ++frame_id) {
            (void)step(&ctx, frame_id, no_target(), true);
        }
        rehab_person_follow_decision_t first = step(
            &ctx, 4U, target(cases[i].center_x, 0.10f, 0.2f, 0.8f),
            cases[i].action_succeeds);
        TEST_ASSERT_TRUE(first.first_target_acquired);
        TEST_ASSERT_TRUE(ctx.target_acquired_once);
        TEST_ASSERT_EQUAL(cases[i].expected_action, first.action);
        TEST_ASSERT_EQUAL_UINT32(
            cases[i].expected_action == REHAB_PERSON_FOLLOW_ACTION_FORWARD
                ? 500U : 250U,
            first.hold_ms);
        TEST_ASSERT_EQUAL_UINT32(250U, ctx.turn_hold_ms);
        if (cases[i].expected_action ==
                REHAB_PERSON_FOLLOW_ACTION_FORWARD) {
            TEST_ASSERT_FALSE(ctx.turn_check_pending);
            TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE,
                              ctx.last_successful_turn_direction);
        } else {
            TEST_ASSERT_TRUE(ctx.turn_check_pending);
            TEST_ASSERT_EQUAL(cases[i].expected_action,
                              ctx.last_successful_turn_direction);
        }
    }
}

TEST_CASE("first target turn records pending only after action success",
          "[person_follow][initial_search][commit]")
{
    rehab_person_follow_decision_context_t ctx = context();
    (void)step(&ctx, 1U, no_target(), true);
    rehab_person_follow_decision_t first = step(
        &ctx, 2U, target(0.1f, 0.10f, 0.2f, 0.8f), false);
    assert_action(first, REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 250U);
    TEST_ASSERT_TRUE(first.first_target_acquired);
    TEST_ASSERT_TRUE(ctx.target_acquired_once);
    TEST_ASSERT_FALSE(ctx.turn_check_pending);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE,
                      ctx.last_successful_turn_direction);
}

TEST_CASE("invalid target bbox does not acquire unified turn authority",
          "[person_follow][initial_search][invalid_bbox]")
{
    rehab_person_follow_decision_context_t ctx = context();
    rehab_person_follow_observation_t invalid =
        target(0.5f, 0.10f, 0.2f, 0.8f);
    invalid.bbox_x2 = invalid.bbox_x1;
    rehab_person_follow_decision_t decision = {0};
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE, rehab_person_follow_decide(
        &ctx, &invalid, 1U, ctx.generation, false, 3U,
        REHAB_PERSON_FOLLOW_TURN_INITIAL_MS, 500U, &decision));
    TEST_ASSERT_FALSE(ctx.target_acquired_once);
    TEST_ASSERT_FALSE(ctx.turn_check_pending);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE,
                      ctx.last_successful_turn_direction);
}

TEST_CASE("unified turn ladder reaches and holds the 100 ms floor",
          "[person_follow][unified_turn][ladder]")
{
    rehab_person_follow_decision_context_t ctx = context();
    const uint32_t expected[] = {
        250U, 225U, 200U, 175U, 150U, 125U, 100U, 100U,
    };
    for (size_t i = 0; i < sizeof(expected) / sizeof(expected[0]); ++i) {
        float center = i % 2U == 0U ? 0.1f : 0.9f;
        rehab_person_follow_decision_t decision = step(
            &ctx, (uint32_t)i + 1U,
            target(center, 0.10f, 0.2f, 0.8f), true);
        TEST_ASSERT_EQUAL_UINT32(expected[i], decision.hold_ms);
    }
    TEST_ASSERT_EQUAL_UINT32(100U, ctx.turn_hold_ms);
}

TEST_CASE("all four overshoot transitions decrement and reverse",
          "[person_follow][unified_turn][overshoot]")
{
    const struct {
        rehab_person_follow_action_t previous;
        rehab_person_follow_observation_t next;
        rehab_person_follow_action_t expected;
    } cases[] = {
        {REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT,
         target(0.9f, 0.10f, 0.2f, 0.8f),
         REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT},
        {REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT,
         target(0.1f, 0.10f, 0.2f, 0.8f),
         REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT},
        {REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, no_target(),
         REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT},
        {REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, no_target(),
         REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        rehab_person_follow_decision_context_t ctx = context();
        float first_center = cases[i].previous ==
                REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT ? 0.1f : 0.9f;
        (void)step(&ctx, 1U,
                   target(first_center, 0.10f, 0.2f, 0.8f), true);
        rehab_person_follow_decision_t decision = step(
            &ctx, 2U, cases[i].next, true);
        assert_action(decision, cases[i].expected, 225U);
        TEST_ASSERT_TRUE(decision.overshoot_reverse);
        TEST_ASSERT_EQUAL_UINT32(225U, ctx.turn_hold_ms);
    }
}

TEST_CASE("same side does not decrement the unified hold",
          "[person_follow][unified_turn][same_side]")
{
    rehab_person_follow_decision_context_t left = context();
    (void)step(&left, 1U, target(0.1f, 0.10f, 0.2f, 0.8f), true);
    assert_action(step(&left, 2U,
                       target(0.1f, 0.10f, 0.2f, 0.8f), true),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 250U);
    TEST_ASSERT_EQUAL_UINT32(250U, left.turn_hold_ms);

    rehab_person_follow_decision_context_t right = context();
    (void)step(&right, 1U, target(0.9f, 0.10f, 0.2f, 0.8f), true);
    assert_action(step(&right, 2U,
                       target(0.9f, 0.10f, 0.2f, 0.8f), true),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, 250U);
    TEST_ASSERT_EQUAL_UINT32(250U, right.turn_hold_ms);
}

TEST_CASE("CENTER consumes pending without decrement or stale history",
          "[person_follow][unified_turn][center]")
{
    rehab_person_follow_decision_context_t ctx = context();
    (void)step(&ctx, 1U, target(0.1f, 0.10f, 0.2f, 0.8f), true);
    TEST_ASSERT_TRUE(ctx.turn_check_pending);
    assert_action(step(&ctx, 2U,
                       target(0.5f, 0.10f, 0.2f, 0.8f), false),
                  REHAB_PERSON_FOLLOW_ACTION_FORWARD, 500U);
    TEST_ASSERT_FALSE(ctx.turn_check_pending);
    TEST_ASSERT_EQUAL_UINT32(250U, ctx.turn_hold_ms);
    assert_action(step(&ctx, 3U, no_target(), true),
                  REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT, 250U);
}

TEST_CASE("technical failure stops and preserves pending until valid result",
          "[person_follow][unified_turn][technical]")
{
    rehab_person_follow_decision_context_t ctx = context();
    (void)step(&ctx, 1U, target(0.1f, 0.10f, 0.2f, 0.8f), true);
    rehab_person_follow_decision_t failure = step(&ctx, 2U,
        (rehab_person_follow_observation_t) {0}, false);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE, failure.action);
    TEST_ASSERT_TRUE(ctx.turn_check_pending);
    TEST_ASSERT_EQUAL_UINT32(250U, ctx.turn_hold_ms);
    assert_action(step(&ctx, 3U,
                       target(0.9f, 0.10f, 0.2f, 0.8f), true),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, 225U);
}

TEST_CASE("post-acquisition no target search uses current hold and records RIGHT",
          "[person_follow][unified_turn][search]")
{
    rehab_person_follow_decision_context_t ctx = context();
    (void)step(&ctx, 1U,
               target(0.5f, 0.10f, 0.2f, 0.8f), false);
    TEST_ASSERT_TRUE(ctx.target_acquired_once);
    assert_action(step(&ctx, 2U, no_target(), true),
                  REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT, 250U);
    TEST_ASSERT_TRUE(ctx.turn_check_pending);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT,
                      ctx.last_successful_turn_direction);
    assert_action(step(&ctx, 3U, no_target(), true),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 225U);
}

TEST_CASE("new generation reset forgets prior acquisition and turn ladder",
          "[person_follow][initial_search][restart]")
{
    rehab_person_follow_decision_context_t ctx = context();
    for (uint32_t frame_id = 1U; frame_id <= 5U; ++frame_id) {
        float center_x = frame_id % 2U == 0U ? 0.9f : 0.1f;
        (void)step(&ctx, frame_id,
                   target(center_x, 0.10f, 0.2f, 0.8f), true);
    }
    TEST_ASSERT_TRUE(ctx.target_acquired_once);
    TEST_ASSERT_EQUAL_UINT32(150U, ctx.turn_hold_ms);

    ++ctx.generation;
    rehab_person_follow_reset_adaptive_turn(
        &ctx, REHAB_PERSON_FOLLOW_TURN_INITIAL_MS);
    TEST_ASSERT_FALSE(ctx.target_acquired_once);
    TEST_ASSERT_EQUAL_UINT32(250U, ctx.turn_hold_ms);
    TEST_ASSERT_FALSE(ctx.turn_check_pending);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE,
                      ctx.last_successful_turn_direction);
    assert_action(step(&ctx, 6U, no_target(), true),
                  REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT, 250U);
    TEST_ASSERT_FALSE(ctx.turn_check_pending);
}

TEST_CASE("normal follow uses 20 80 before the first gate",
          "[person_follow][gates][before]")
{
    rehab_person_follow_decision_context_t ctx = context();
    assert_action(decide_at(&ctx, 1U,
                            target(0.199f, 0.10f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 250U);
    assert_action(decide_at(&ctx, 2U,
                            target(0.20f, 0.10f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_FORWARD, 500U);
    assert_action(decide_at(&ctx, 3U,
                            target(0.80f, 0.10f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_FORWARD, 500U);
    assert_action(decide_at(&ctx, 4U,
                            target(0.801f, 0.10f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, 250U);
}

TEST_CASE("first gate uses 40 60 margin priority and direct arrival",
          "[person_follow][gates][first][margin]")
{
    rehab_person_follow_decision_context_t left = context();
    trigger_first(&left, 1U);
    assert_action(decide_at(&left, 2U, box(0.01f, 0.49f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 250U);

    rehab_person_follow_decision_context_t right = context();
    trigger_first(&right, 1U);
    assert_action(decide_at(&right, 2U, box(0.51f, 0.99f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, 250U);

    rehab_person_follow_decision_context_t centered = context();
    trigger_first(&centered, 1U);
    rehab_person_follow_decision_t arrived = step(
        &centered, 2U, box(0.01f, 0.99f, 0.2f, 0.8f), false);
    TEST_ASSERT_TRUE(arrived.direct_arrival);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_STATE_ARRIVED,
                      arrived.next_state);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_APPROACH_ARRIVED,
                      centered.approach_phase);
    TEST_ASSERT_EQUAL(
        REHAB_PERSON_FOLLOW_ARRIVAL_BOTH_MARGINS_MISSING_CENTERED,
        centered.arrival_reason);
}

TEST_CASE("first gate requires two fresh centered margin-ready frames",
          "[person_follow][gates][first][stable]")
{
    rehab_person_follow_decision_context_t ctx = context();
    trigger_first(&ctx, 10U);
    rehab_person_follow_decision_t first = step(
        &ctx, 11U, target(0.40f, 0.20f, 0.2f, 0.8f), true);
    TEST_ASSERT_EQUAL_UINT32(1U, ctx.align_stable_count);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE, first.action);
    rehab_person_follow_decision_t second = step(
        &ctx, 12U, target(0.60f, 0.10f, 0.2f, 0.8f), true);
    TEST_ASSERT_TRUE(ctx.first_align_completed);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_STATE_SENSE, second.next_state);
}

TEST_CASE("between gates uses 40 60 and ignores margins",
          "[person_follow][gates][between]")
{
    rehab_person_follow_decision_context_t ctx = context();
    complete_first(&ctx);
    assert_action(decide_at(&ctx, 4U,
                            target(0.399f, 0.10f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 250U);
    assert_action(decide_at(&ctx, 5U,
                            target(0.40f, 0.10f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_FORWARD, 500U);
    assert_action(decide_at(&ctx, 6U,
                            target(0.60f, 0.10f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_FORWARD, 500U);
    assert_action(decide_at(&ctx, 7U,
                            target(0.601f, 0.10f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, 250U);
    assert_action(decide_at(&ctx, 8U, box(0.01f, 0.59f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 250U);
}

TEST_CASE("second gate uses 45 55 and both-missing direct arrival",
          "[person_follow][gates][final][margin]")
{
    rehab_person_follow_decision_context_t left = context();
    complete_first(&left);
    trigger_final(&left, 4U);
    assert_action(decide_at(&left, 5U,
                            target(0.449f, 0.20f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 250U);

    rehab_person_follow_decision_context_t right = context();
    complete_first(&right);
    trigger_final(&right, 4U);
    assert_action(decide_at(&right, 5U,
                            target(0.551f, 0.20f, 0.2f, 0.8f)),
                  REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT, 250U);

    rehab_person_follow_decision_context_t centered = context();
    complete_first(&centered);
    trigger_final(&centered, 4U);
    rehab_person_follow_decision_t arrived = step(
        &centered, 5U, box(0.01f, 0.99f, 0.2f, 0.8f), false);
    TEST_ASSERT_TRUE(arrived.direct_arrival);
    TEST_ASSERT_FALSE(arrived.final_forward);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_APPROACH_ARRIVED,
                      centered.approach_phase);
}

TEST_CASE("second gate two frames dispatch one 1600 ms timed forward",
          "[person_follow][gates][final][forward]")
{
    rehab_person_follow_decision_context_t ctx = context();
    complete_first(&ctx);
    trigger_final(&ctx, 4U);
    TEST_ASSERT_EQUAL_STRING("final_align_stable_1_of_2",
        step(&ctx, 5U, target(0.50f, 0.20f, 0.2f, 0.8f), true).reason);
    rehab_person_follow_decision_t forward = decide_at(
        &ctx, 6U, target(0.50f, 0.10f, 0.2f, 0.8f));
    assert_action(forward, REHAB_PERSON_FOLLOW_ACTION_FORWARD, 1600U);
    TEST_ASSERT_TRUE(forward.final_forward);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_STATE_FINAL_FORWARD,
                      forward.next_state);
}

TEST_CASE("final forward failure returns to final align for two new frames",
          "[person_follow][gates][final][failure]")
{
    rehab_person_follow_decision_context_t ctx = context();
    complete_first(&ctx);
    trigger_final(&ctx, 4U);
    (void)step(&ctx, 5U, target(0.50f, 0.20f, 0.2f, 0.8f), true);
    rehab_person_follow_decision_t forward = decide_at(
        &ctx, 6U, target(0.50f, 0.10f, 0.2f, 0.8f));
    rehab_person_follow_commit_action(&ctx, &forward, false);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_APPROACH_FINAL_ALIGN,
                      ctx.approach_phase);
    TEST_ASSERT_EQUAL_UINT32(0U, ctx.align_stable_count);
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE,
        decide_at(&ctx, 6U, target(0.50f, 0.10f, 0.2f, 0.8f)).action);
    (void)step(&ctx, 7U, target(0.50f, 0.10f, 0.2f, 0.8f), true);
    rehab_person_follow_decision_t retry = decide_at(
        &ctx, 8U, target(0.50f, 0.10f, 0.2f, 0.8f));
    TEST_ASSERT_TRUE(retry.final_forward);
}

TEST_CASE("ARRIVED locks every later observation to NONE",
          "[person_follow][arrived]")
{
    rehab_person_follow_decision_context_t ctx = context();
    complete_first(&ctx);
    trigger_final(&ctx, 4U);
    (void)step(&ctx, 5U, target(0.50f, 0.10f, 0.2f, 0.8f), true);
    rehab_person_follow_decision_t forward = decide_at(
        &ctx, 6U, target(0.50f, 0.10f, 0.2f, 0.8f));
    rehab_person_follow_commit_action(&ctx, &forward, true);
    const rehab_person_follow_observation_t cases[] = {
        target(0.1f, 0.10f, 0.2f, 0.8f),
        target(0.5f, 0.10f, 0.2f, 0.8f),
        target(0.9f, 0.10f, 0.2f, 0.8f), no_target(), {0},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        rehab_person_follow_decision_t locked = decide_at(
            &ctx, (uint32_t)i + 7U, cases[i]);
        TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE, locked.action);
        TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_STATE_ARRIVED,
                          locked.next_state);
    }
}

TEST_CASE("Y axis never changes gate decisions",
          "[person_follow][gates][y_invariance]")
{
    const float y[][2] = {
        {0.0f, 1.0f}, {0.0f, 0.5f}, {0.5f, 1.0f}, {0.2f, 0.8f},
    };
    for (size_t i = 0; i < sizeof(y) / sizeof(y[0]); ++i) {
        rehab_person_follow_decision_context_t ctx = context();
        trigger_first(&ctx, 1U);
        assert_action(decide_at(&ctx, 2U,
                                target(0.30f, 0.20f, y[i][0], y[i][1])),
                      REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT, 250U);
    }
}

TEST_CASE("timing width and margin regressions remain frozen",
          "[person_follow][regression]")
{
    rehab_person_follow_config_t config =
        REHAB_PERSON_FOLLOW_CONFIG_DEFAULT((rehab_car_handle_t)1);
    TEST_ASSERT_EQUAL_UINT32(250U, REHAB_PERSON_FOLLOW_TURN_INITIAL_MS);
    TEST_ASSERT_EQUAL_UINT32(25U, REHAB_PERSON_FOLLOW_TURN_STEP_MS);
    TEST_ASSERT_EQUAL_UINT32(100U, REHAB_PERSON_FOLLOW_TURN_MIN_MS);
    TEST_ASSERT_EQUAL_UINT32(1600U,
                             REHAB_PERSON_FOLLOW_FINAL_FORWARD_HOLD_MS);
    TEST_ASSERT_EQUAL_UINT32(500U,
                             PERSON_FOLLOW_ROLLING_FORWARD_SEGMENT_MS);
    TEST_ASSERT_EQUAL_UINT32(900U,
                             PERSON_FOLLOW_ROLLING_FORWARD_LEASE_MS);
    TEST_ASSERT_EQUAL_UINT32(1000U,
                             REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_GRACE_MS);
    TEST_ASSERT_EQUAL_UINT32(10U, config.forward_post_action_settle_ms);
    TEST_ASSERT_EQUAL_UINT32(40U, config.turn_post_action_settle_ms);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.25f,
        REHAB_PERSON_FOLLOW_FIRST_ALIGN_TRIGGER_WIDTH_RATIO);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.60f,
        REHAB_PERSON_FOLLOW_FINAL_APPROACH_TRIGGER_WIDTH_RATIO);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.02f,
        REHAB_PERSON_FOLLOW_FINAL_ALIGN_SIDE_MARGIN_RATIO);
}

TEST_CASE("startup observation still suppresses no target for 1000 ms",
          "[person_follow][startup_observation][regression]")
{
    struct rehab_person_follow handle = {0};
    handle.generation = 7U;
    handle.decision_context = context();
    handle.startup_observation_active = true;
    handle.startup_observation_started_us = 1000000;
    rehab_person_follow_decision_t decision = {0};
    bool log = false;
    TEST_ASSERT_TRUE(rehab_person_follow_suppress_startup_no_target(
        &handle, 7U, 1999000, ESP_OK,
        &(rehab_person_follow_observation_t) {.valid = true},
        &decision, &log));
    TEST_ASSERT_EQUAL(REHAB_PERSON_FOLLOW_ACTION_NONE, decision.action);
    TEST_ASSERT_FALSE(rehab_person_follow_suppress_startup_no_target(
        &handle, 7U, 2000000, ESP_OK,
        &(rehab_person_follow_observation_t) {.valid = true},
        &decision, &log));
    TEST_ASSERT_EQUAL(
        REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_GRACE_EXPIRED_NO_TARGET,
        handle.startup_observation_exit_reason);
}
