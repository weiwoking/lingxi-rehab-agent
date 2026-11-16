// SPDX-License-Identifier: Apache-2.0
#include "rehab_person_follow_priv.h"

#include <math.h>
#include <string.h>

const char *rehab_person_follow_state_name(rehab_person_follow_state_t state)
{
    switch (state) {
    case REHAB_PERSON_FOLLOW_STATE_IDLE: return "IDLE";
    case REHAB_PERSON_FOLLOW_STATE_STARTING: return "STARTING";
    case REHAB_PERSON_FOLLOW_STATE_SENSE: return "SENSE";
    case REHAB_PERSON_FOLLOW_STATE_CONFIRM: return "CONFIRM";
    case REHAB_PERSON_FOLLOW_STATE_SEARCH: return "SEARCH";
    case REHAB_PERSON_FOLLOW_STATE_ALIGN: return "ALIGN";
    case REHAB_PERSON_FOLLOW_STATE_FOLLOW_FORWARD: return "FOLLOW_FORWARD";
    case REHAB_PERSON_FOLLOW_STATE_TARGET_LOST: return "TARGET_LOST";
    case REHAB_PERSON_FOLLOW_STATE_RECOVER: return "RECOVER";
    case REHAB_PERSON_FOLLOW_STATE_STOPPING: return "STOPPING";
    case REHAB_PERSON_FOLLOW_STATE_CANCELLED: return "CANCELLED";
    case REHAB_PERSON_FOLLOW_STATE_ERROR: return "ERROR";
    case REHAB_PERSON_FOLLOW_STATE_FIRST_ALIGN: return "FIRST_ALIGN";
    case REHAB_PERSON_FOLLOW_STATE_FINAL_ALIGN: return "FINAL_ALIGN";
    case REHAB_PERSON_FOLLOW_STATE_FINAL_FORWARD: return "FINAL_FORWARD";
    case REHAB_PERSON_FOLLOW_STATE_ARRIVED: return "ARRIVED";
    default: return "UNKNOWN";
    }
}

const char *rehab_person_follow_action_name(rehab_person_follow_action_t action)
{
    switch (action) {
    case REHAB_PERSON_FOLLOW_ACTION_NONE: return "NONE";
    case REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT: return "TURN_LEFT";
    case REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT: return "TURN_RIGHT";
    case REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT: return "SEARCH_RIGHT";
    case REHAB_PERSON_FOLLOW_ACTION_FORWARD: return "FORWARD";
    default: return "UNKNOWN";
    }
}

const char *rehab_person_follow_approach_phase_name(
    rehab_person_follow_approach_phase_t phase)
{
    switch (phase) {
    case REHAB_PERSON_FOLLOW_APPROACH_NORMAL_FOLLOW: return "NORMAL_FOLLOW";
    case REHAB_PERSON_FOLLOW_APPROACH_FIRST_ALIGN: return "FIRST_ALIGN";
    case REHAB_PERSON_FOLLOW_APPROACH_FINAL_ALIGN: return "FINAL_ALIGN";
    case REHAB_PERSON_FOLLOW_APPROACH_FINAL_FORWARD: return "FINAL_FORWARD";
    case REHAB_PERSON_FOLLOW_APPROACH_ARRIVED: return "ARRIVED";
    default: return "UNKNOWN";
    }
}

const char *rehab_person_follow_align_gate_name(
    rehab_person_follow_align_gate_t gate)
{
    switch (gate) {
    case REHAB_PERSON_FOLLOW_ALIGN_GATE_NONE: return "NONE";
    case REHAB_PERSON_FOLLOW_ALIGN_GATE_FIRST: return "FIRST";
    case REHAB_PERSON_FOLLOW_ALIGN_GATE_FINAL: return "FINAL";
    default: return "UNKNOWN";
    }
}

const char *rehab_person_follow_arrival_reason_name(
    rehab_person_follow_arrival_reason_t reason)
{
    switch (reason) {
    case REHAB_PERSON_FOLLOW_ARRIVAL_NONE: return "none";
    case REHAB_PERSON_FOLLOW_ARRIVAL_FINAL_FORWARD_SUCCEEDED:
        return "final_forward_succeeded";
    case REHAB_PERSON_FOLLOW_ARRIVAL_BOTH_MARGINS_MISSING_CENTERED:
        return "both_margins_missing_centered";
    default: return "unknown";
    }
}

const char *rehab_person_follow_zone_name(rehab_person_follow_zone_t zone)
{
    switch (zone) {
    case REHAB_PERSON_FOLLOW_ZONE_LEFT: return "left";
    case REHAB_PERSON_FOLLOW_ZONE_CENTER: return "center";
    case REHAB_PERSON_FOLLOW_ZONE_RIGHT: return "right";
    default: return "none";
    }
}

rehab_person_follow_zone_t rehab_person_follow_zone_from_center(float center_x)
{
    if (!isfinite(center_x) || center_x < 0.0f || center_x > 1.0f) {
        return REHAB_PERSON_FOLLOW_ZONE_NONE;
    }
    if (center_x < PERSON_FOLLOW_LEFT_BOUNDARY) {
        return REHAB_PERSON_FOLLOW_ZONE_LEFT;
    }
    if (center_x > PERSON_FOLLOW_RIGHT_BOUNDARY) {
        return REHAB_PERSON_FOLLOW_ZONE_RIGHT;
    }
    return REHAB_PERSON_FOLLOW_ZONE_CENTER;
}

static bool observation_bbox_metrics(
    const rehab_person_follow_observation_t *observation,
    float *out_width_ratio,
    float *out_center_x_ratio,
    float *out_left_background_ratio,
    float *out_right_background_ratio)
{
    if (!isfinite(observation->bbox_x1) ||
        !isfinite(observation->bbox_x2) ||
        observation->bbox_x1 < 0.0f ||
        observation->bbox_x2 > (float)PERSON_FOLLOW_IMAGE_WIDTH ||
        observation->bbox_x2 <= observation->bbox_x1) {
        return false;
    }
    *out_width_ratio = (observation->bbox_x2 - observation->bbox_x1) /
                       (float)PERSON_FOLLOW_IMAGE_WIDTH;
    *out_center_x_ratio = ((observation->bbox_x1 + observation->bbox_x2) /
                           2.0f) / (float)PERSON_FOLLOW_IMAGE_WIDTH;
    *out_left_background_ratio = observation->bbox_x1 /
                                 (float)PERSON_FOLLOW_IMAGE_WIDTH;
    *out_right_background_ratio =
        ((float)PERSON_FOLLOW_IMAGE_WIDTH - observation->bbox_x2) /
        (float)PERSON_FOLLOW_IMAGE_WIDTH;
    return true;
}

const char *rehab_person_follow_startup_observation_exit_reason_name(
    rehab_person_follow_startup_observation_exit_reason_t reason)
{
    switch (reason) {
    case REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_NONE:
        return "NONE";
    case REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_TARGET_FOUND:
        return "TARGET_FOUND";
    case REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_GRACE_EXPIRED_NO_TARGET:
        return "GRACE_EXPIRED_NO_TARGET";
    case REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_STOPPED:
        return "STOPPED";
    case REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_GENERATION_CHANGED:
        return "GENERATION_CHANGED";
    default:
        return "UNKNOWN";
    }
}

static rehab_person_follow_action_t action_turn_direction(
    rehab_person_follow_action_t action)
{
    return action == REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT
        ? REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT : action;
}

static bool action_is_turn(rehab_person_follow_action_t action)
{
    rehab_person_follow_action_t direction = action_turn_direction(action);
    return direction == REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT ||
           direction == REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT;
}

static rehab_person_follow_action_t opposite_turn(
    rehab_person_follow_action_t direction)
{
    return direction == REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT
        ? REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT
        : direction == REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT
            ? REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT
            : REHAB_PERSON_FOLLOW_ACTION_NONE;
}

static uint32_t stepped_turn_hold_ms(uint32_t hold_ms)
{
    return hold_ms > REHAB_PERSON_FOLLOW_TURN_MIN_MS +
                          REHAB_PERSON_FOLLOW_TURN_STEP_MS
        ? hold_ms - REHAB_PERSON_FOLLOW_TURN_STEP_MS
        : REHAB_PERSON_FOLLOW_TURN_MIN_MS;
}

static void plan_turn_for_result(
    const rehab_person_follow_decision_context_t *context,
    rehab_person_follow_action_t requested_direction,
    bool no_target,
    rehab_person_follow_decision_t *decision)
{
    decision->consume_turn_check = context->turn_check_pending;
    rehab_person_follow_action_t previous =
        context->last_successful_turn_direction;
    bool overshoot = context->turn_check_pending &&
        (no_target || requested_direction == opposite_turn(previous));
    if (overshoot) {
        decision->action = opposite_turn(previous);
        decision->hold_ms = stepped_turn_hold_ms(context->turn_hold_ms);
        decision->overshoot_reverse = true;
        decision->reduce_turn_hold = true;
        decision->reason = no_target
            ? "turn_no_target_reverse" : "turn_cross_side_reverse";
        return;
    }
    if (no_target) {
        decision->action = REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT;
        decision->reason = "no_target_search_right";
    } else {
        decision->action = requested_direction;
        decision->reason = context->turn_check_pending
            ? "turn_same_side" : "turn_for_zone";
    }
    decision->hold_ms = context->turn_hold_ms;
}

void rehab_person_follow_reset_adaptive_turn(
    rehab_person_follow_decision_context_t *context,
    uint32_t turn_hold_ms)
{
    (void)turn_hold_ms;
    if (context == NULL) {
        return;
    }
    context->has_forwarded_once = false;
    context->fine_turn_mode = false;
    context->target_acquired_once = false;
    context->turn_hold_ms = REHAB_PERSON_FOLLOW_TURN_INITIAL_MS;
    context->turn_check_pending = false;
    context->last_successful_turn_direction =
        REHAB_PERSON_FOLLOW_ACTION_NONE;
    context->approach_phase = REHAB_PERSON_FOLLOW_APPROACH_NORMAL_FOLLOW;
    context->first_align_completed = false;
    context->active_align_gate = REHAB_PERSON_FOLLOW_ALIGN_GATE_NONE;
    context->align_stable_count = 0U;
    context->last_stable_frame_id = 0U;
    context->pending_align_trigger_frame_id = 0U;
    context->align_stop_completed = false;
    context->first_align_trigger_frame_id = 0U;
    context->final_align_trigger_frame_id = 0U;
    context->align_overshoot_count = 0U;
    context->final_forward_attempted_frame_id = 0U;
    context->arrival_reason = REHAB_PERSON_FOLLOW_ARRIVAL_NONE;
}

void rehab_person_follow_commit_align_stop(
    rehab_person_follow_decision_context_t *context,
    const rehab_person_follow_decision_t *decision,
    bool stop_completed)
{
    if (context == NULL || decision == NULL || !decision->align_trigger ||
        (context->approach_phase !=
             REHAB_PERSON_FOLLOW_APPROACH_FIRST_ALIGN &&
         context->approach_phase !=
             REHAB_PERSON_FOLLOW_APPROACH_FINAL_ALIGN) ||
        !stop_completed) {
        return;
    }
    if (context->active_align_gate ==
            REHAB_PERSON_FOLLOW_ALIGN_GATE_FIRST) {
        context->first_align_trigger_frame_id =
            context->pending_align_trigger_frame_id;
    } else {
        context->final_align_trigger_frame_id =
            context->pending_align_trigger_frame_id;
    }
    context->align_stop_completed = true;
}

void rehab_person_follow_commit_action(
    rehab_person_follow_decision_context_t *context,
    const rehab_person_follow_decision_t *decision,
    bool executed_successfully)
{
    if (context == NULL || decision == NULL) {
        return;
    }

    if (decision->consume_turn_check) {
        context->turn_check_pending = false;
        context->last_successful_turn_direction =
            REHAB_PERSON_FOLLOW_ACTION_NONE;
    }

    if (decision->direct_arrival) {
        context->approach_phase = REHAB_PERSON_FOLLOW_APPROACH_ARRIVED;
        context->active_align_gate = REHAB_PERSON_FOLLOW_ALIGN_GATE_NONE;
        context->arrival_reason =
            REHAB_PERSON_FOLLOW_ARRIVAL_BOTH_MARGINS_MISSING_CENTERED;
        return;
    }

    if (!executed_successfully) {
        if (decision->final_forward) {
            context->approach_phase =
                REHAB_PERSON_FOLLOW_APPROACH_FINAL_ALIGN;
            context->active_align_gate =
                REHAB_PERSON_FOLLOW_ALIGN_GATE_FINAL;
            context->align_stable_count = 0U;
            context->last_stable_frame_id =
                context->final_forward_attempted_frame_id;
        }
        return;
    }

    if (decision->final_forward) {
        context->approach_phase = REHAB_PERSON_FOLLOW_APPROACH_ARRIVED;
        context->arrival_reason =
            REHAB_PERSON_FOLLOW_ARRIVAL_FINAL_FORWARD_SUCCEEDED;
        return;
    }

    if (decision->action == REHAB_PERSON_FOLLOW_ACTION_FORWARD) {
        context->has_forwarded_once = true;
        return;
    }

    if (!action_is_turn(decision->action)) {
        return;
    }

    if (!context->target_acquired_once &&
        decision->action == REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT) {
        return;
    }

    if (decision->reduce_turn_hold) {
        context->turn_hold_ms = decision->hold_ms;
    }
    context->turn_check_pending = true;
    context->last_successful_turn_direction =
        action_turn_direction(decision->action);
    if (decision->align_turn && decision->overshoot_reverse &&
        context->align_overshoot_count < UINT32_MAX) {
        ++context->align_overshoot_count;
    }
    context->fine_turn_mode = true;
}

bool rehab_person_follow_suppress_startup_no_target(
    rehab_person_follow_handle_t handle,
    uint32_t generation,
    int64_t now_us,
    esp_err_t observe_result,
    const rehab_person_follow_observation_t *observation,
    rehab_person_follow_decision_t *out_decision,
    bool *out_log_suppression)
{
    if (out_log_suppression != NULL) {
        *out_log_suppression = false;
    }
    if (handle == NULL || observation == NULL || out_decision == NULL ||
        !handle->startup_observation_active ||
        handle->generation != generation || handle->cancel_requested ||
        observe_result != ESP_OK || !observation->valid ||
        observation->target_found) {
        return false;
    }

    int64_t elapsed_us = now_us - handle->startup_observation_started_us;
    int64_t grace_us =
        (int64_t)REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_GRACE_MS * 1000;
    if (elapsed_us >= grace_us) {
        handle->startup_observation_active = false;
        handle->startup_observation_exit_reason =
            REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_GRACE_EXPIRED_NO_TARGET;
        return false;
    }

    handle->decision_context.consecutive_errors = 0U;
    handle->decision_context.state = REHAB_PERSON_FOLLOW_STATE_SENSE;
    memset(out_decision, 0, sizeof(*out_decision));
    out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_SENSE;
    out_decision->action = REHAB_PERSON_FOLLOW_ACTION_NONE;
    out_decision->reason = "startup_observation_no_target";
    if (!handle->startup_observation_no_target_logged) {
        handle->startup_observation_no_target_logged = true;
        if (out_log_suppression != NULL) {
            *out_log_suppression = true;
        }
    }
    return true;
}

void rehab_person_follow_accept_startup_target(
    rehab_person_follow_handle_t handle,
    uint32_t generation,
    esp_err_t observe_result,
    const rehab_person_follow_observation_t *observation,
    esp_err_t decision_result)
{
    if (handle == NULL || observation == NULL ||
        !handle->startup_observation_active ||
        handle->generation != generation || handle->cancel_requested ||
        observe_result != ESP_OK || !observation->valid ||
        !observation->target_found || decision_result != ESP_OK) {
        return;
    }
    handle->startup_observation_active = false;
    handle->startup_observation_exit_reason =
        REHAB_PERSON_FOLLOW_STARTUP_OBSERVATION_EXIT_TARGET_FOUND;
}

static bool person_follow_is_aligning(
    const rehab_person_follow_decision_context_t *context)
{
    return context->approach_phase ==
               REHAB_PERSON_FOLLOW_APPROACH_FIRST_ALIGN ||
           context->approach_phase ==
               REHAB_PERSON_FOLLOW_APPROACH_FINAL_ALIGN;
}

static rehab_person_follow_state_t person_follow_align_state(
    const rehab_person_follow_decision_context_t *context)
{
    return context->approach_phase ==
            REHAB_PERSON_FOLLOW_APPROACH_FIRST_ALIGN
        ? REHAB_PERSON_FOLLOW_STATE_FIRST_ALIGN
        : REHAB_PERSON_FOLLOW_STATE_FINAL_ALIGN;
}

static bool person_follow_reset_align_stable(
    rehab_person_follow_decision_context_t *context)
{
    bool had_stable = context->align_stable_count > 0U;
    context->align_stable_count = 0U;
    context->last_stable_frame_id = 0U;
    return had_stable;
}

esp_err_t rehab_person_follow_decide(
    rehab_person_follow_decision_context_t *context,
    const rehab_person_follow_observation_t *observation,
    uint32_t frame_id,
    uint32_t current_generation,
    bool cancel_requested,
    uint32_t max_consecutive_errors,
    uint32_t turn_hold_ms,
    uint32_t forward_hold_ms,
    rehab_person_follow_decision_t *out_decision)
{
    if (context == NULL || observation == NULL || out_decision == NULL ||
        frame_id == 0U || max_consecutive_errors == 0U ||
        turn_hold_ms == 0U ||
        forward_hold_ms == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_decision, 0, sizeof(*out_decision));
    out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_SENSE;
    out_decision->action = REHAB_PERSON_FOLLOW_ACTION_NONE;
    out_decision->reason = "sense";

    if (cancel_requested || context->generation != current_generation) {
        person_follow_reset_align_stable(context);
        out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_STOPPING;
        out_decision->action = REHAB_PERSON_FOLLOW_ACTION_NONE;
        out_decision->reason = cancel_requested ? "cancel_requested"
                                                : "generation_stale";
        context->state = out_decision->next_state;
        return ESP_OK;
    }

    if (context->approach_phase == REHAB_PERSON_FOLLOW_APPROACH_ARRIVED) {
        out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_ARRIVED;
        out_decision->reason = "arrived_locked";
        out_decision->stop_rolling = true;
        context->state = out_decision->next_state;
        return ESP_OK;
    }

    if (context->approach_phase ==
            REHAB_PERSON_FOLLOW_APPROACH_FINAL_FORWARD) {
        out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_FINAL_FORWARD;
        out_decision->reason = "final_forward_in_progress";
        out_decision->stop_rolling = true;
        context->state = out_decision->next_state;
        return ESP_OK;
    }

    if (!observation->valid) {
        if (context->consecutive_errors < UINT32_MAX) {
            ++context->consecutive_errors;
        }
        out_decision->stop_rolling = true;
        if (person_follow_is_aligning(context)) {
            out_decision->align_stable_reset =
                person_follow_reset_align_stable(context);
            out_decision->align_stable_reset_reason = "technical_error";
            out_decision->next_state = person_follow_align_state(context);
            out_decision->reason = "align_stable_reset_technical_error";
            context->state = out_decision->next_state;
            return ESP_OK;
        }
        out_decision->next_state =
            context->consecutive_errors >= max_consecutive_errors
                ? REHAB_PERSON_FOLLOW_STATE_ERROR
                : REHAB_PERSON_FOLLOW_STATE_RECOVER;
        out_decision->action = REHAB_PERSON_FOLLOW_ACTION_NONE;
        out_decision->reason = out_decision->next_state ==
                REHAB_PERSON_FOLLOW_STATE_ERROR
            ? "technical_error_threshold" : "technical_error_recover";
        context->state = out_decision->next_state;
        return ESP_OK;
    }
    context->consecutive_errors = 0;

    if (!observation->target_found) {
        if (!context->target_acquired_once) {
            context->turn_hold_ms = REHAB_PERSON_FOLLOW_TURN_INITIAL_MS;
            context->turn_check_pending = false;
            context->last_successful_turn_direction =
                REHAB_PERSON_FOLLOW_ACTION_NONE;
            out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_SEARCH;
            out_decision->action = REHAB_PERSON_FOLLOW_ACTION_SEARCH_RIGHT;
            out_decision->hold_ms = REHAB_PERSON_FOLLOW_TURN_INITIAL_MS;
            out_decision->reason = "initial_search_right_fixed";
            context->state = out_decision->next_state;
            return ESP_OK;
        }
        if (person_follow_is_aligning(context)) {
            out_decision->align_stable_reset =
                person_follow_reset_align_stable(context);
            out_decision->align_stable_reset_reason = "no_target";
            out_decision->next_state = person_follow_align_state(context);
            out_decision->stop_rolling = true;
        } else {
            out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_SEARCH;
        }
        plan_turn_for_result(context, REHAB_PERSON_FOLLOW_ACTION_NONE,
                             true, out_decision);
        out_decision->align_turn = person_follow_is_aligning(context);
        context->state = out_decision->next_state;
        return ESP_OK;
    }

    if (!isfinite(observation->confidence) ||
        observation->confidence < 0.0f || observation->confidence > 1.0f) {
        out_decision->next_state = person_follow_is_aligning(context)
            ? person_follow_align_state(context)
            : REHAB_PERSON_FOLLOW_STATE_RECOVER;
        out_decision->action = REHAB_PERSON_FOLLOW_ACTION_NONE;
        out_decision->reason = person_follow_is_aligning(context)
            ? "align_stable_reset_observation_invalid"
            : "mock_observation_invalid";
        out_decision->stop_rolling = person_follow_is_aligning(context);
        if (out_decision->stop_rolling) {
            out_decision->align_stable_reset =
                person_follow_reset_align_stable(context);
            out_decision->align_stable_reset_reason =
                "observation_invalid";
        }
        context->state = out_decision->next_state;
        return ESP_ERR_INVALID_RESPONSE;
    }

    if (!observation_bbox_metrics(observation,
                                  &out_decision->bbox_width_ratio,
                                  &out_decision->person_center_x_ratio,
                                  &out_decision->left_background_ratio,
                                  &out_decision->right_background_ratio)) {
        bool aligning = person_follow_is_aligning(context);
        out_decision->next_state = aligning
            ? person_follow_align_state(context)
            : REHAB_PERSON_FOLLOW_STATE_RECOVER;
        out_decision->reason = aligning
            ? "align_stable_reset_bbox_invalid" : "bbox_invalid";
        out_decision->stop_rolling = aligning;
        if (aligning) {
            out_decision->align_stable_reset =
                person_follow_reset_align_stable(context);
            out_decision->align_stable_reset_reason = "bbox_invalid";
        }
        context->state = out_decision->next_state;
        return ESP_ERR_INVALID_RESPONSE;
    }

    if (!context->target_acquired_once) {
        context->target_acquired_once = true;
        context->turn_hold_ms = REHAB_PERSON_FOLLOW_TURN_INITIAL_MS;
        context->turn_check_pending = false;
        context->last_successful_turn_direction =
            REHAB_PERSON_FOLLOW_ACTION_NONE;
        out_decision->first_target_acquired = true;
    }

    if (context->approach_phase ==
            REHAB_PERSON_FOLLOW_APPROACH_NORMAL_FOLLOW) {
        bool trigger_first = !context->first_align_completed &&
            out_decision->bbox_width_ratio >=
                REHAB_PERSON_FOLLOW_FIRST_ALIGN_TRIGGER_WIDTH_RATIO;
        bool trigger_final = context->first_align_completed &&
            out_decision->bbox_width_ratio >=
                REHAB_PERSON_FOLLOW_FINAL_APPROACH_TRIGGER_WIDTH_RATIO;
        if (trigger_first || trigger_final) {
            context->approach_phase = trigger_first
                ? REHAB_PERSON_FOLLOW_APPROACH_FIRST_ALIGN
                : REHAB_PERSON_FOLLOW_APPROACH_FINAL_ALIGN;
            context->active_align_gate = trigger_first
                ? REHAB_PERSON_FOLLOW_ALIGN_GATE_FIRST
                : REHAB_PERSON_FOLLOW_ALIGN_GATE_FINAL;
            context->pending_align_trigger_frame_id = frame_id;
            context->align_stop_completed = false;
            if (trigger_first) {
                context->first_align_trigger_frame_id = 0U;
                context->align_overshoot_count = 0U;
            } else {
                context->final_align_trigger_frame_id = 0U;
            }
            person_follow_reset_align_stable(context);
            context->final_forward_attempted_frame_id = 0U;
            context->arrival_reason = REHAB_PERSON_FOLLOW_ARRIVAL_NONE;
            out_decision->next_state = person_follow_align_state(context);
            out_decision->reason = trigger_first
                ? "first_align_trigger_width"
                : "final_align_trigger_width";
            out_decision->stop_rolling = true;
            out_decision->align_trigger = true;
            context->state = out_decision->next_state;
            return ESP_OK;
        }
    }

    if (person_follow_is_aligning(context)) {
        bool first_gate = context->active_align_gate ==
            REHAB_PERSON_FOLLOW_ALIGN_GATE_FIRST;
        out_decision->next_state = person_follow_align_state(context);
        out_decision->stop_rolling = true;
        if (!context->align_stop_completed) {
            out_decision->align_trigger = true;
            out_decision->reason = "align_wait_rolling_stop";
            context->state = out_decision->next_state;
            return ESP_OK;
        }
        uint32_t trigger_frame_id = first_gate
            ? context->first_align_trigger_frame_id
            : context->final_align_trigger_frame_id;
        uint32_t freshness_floor = trigger_frame_id;
        if (!first_gate &&
            context->final_forward_attempted_frame_id > freshness_floor) {
            freshness_floor = context->final_forward_attempted_frame_id;
        }
        if (context->last_stable_frame_id > freshness_floor) {
            freshness_floor = context->last_stable_frame_id;
        }
        if (frame_id <= freshness_floor) {
            out_decision->align_stable_reset =
                person_follow_reset_align_stable(context);
            out_decision->align_stable_reset_reason = "stale_frame";
            context->last_stable_frame_id = freshness_floor;
            out_decision->reason = "align_stable_reset_stale_frame";
            context->state = out_decision->next_state;
            return ESP_OK;
        }

        float left_boundary = first_gate
            ? REHAB_PERSON_FOLLOW_FIRST_ALIGN_LEFT_BOUNDARY
            : REHAB_PERSON_FOLLOW_FINAL_ALIGN_LEFT_BOUNDARY;
        float right_boundary = first_gate
            ? REHAB_PERSON_FOLLOW_FIRST_ALIGN_RIGHT_BOUNDARY
            : REHAB_PERSON_FOLLOW_FINAL_ALIGN_RIGHT_BOUNDARY;
        bool left_margin_ready = out_decision->left_background_ratio >=
            REHAB_PERSON_FOLLOW_FINAL_ALIGN_SIDE_MARGIN_RATIO;
        bool right_margin_ready = out_decision->right_background_ratio >=
            REHAB_PERSON_FOLLOW_FINAL_ALIGN_SIDE_MARGIN_RATIO;
        rehab_person_follow_action_t requested_turn =
            REHAB_PERSON_FOLLOW_ACTION_NONE;
        const char *adjustment_reason = "center";
        if (!left_margin_ready && right_margin_ready) {
            requested_turn = REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT;
            adjustment_reason = "left_margin_missing";
        } else if (left_margin_ready && !right_margin_ready) {
            requested_turn = REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT;
            adjustment_reason = "right_margin_missing";
        } else if (out_decision->person_center_x_ratio < left_boundary) {
            requested_turn = REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT;
        } else if (out_decision->person_center_x_ratio > right_boundary) {
            requested_turn = REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT;
        } else if (!left_margin_ready && !right_margin_ready) {
            out_decision->align_stable_reset =
                person_follow_reset_align_stable(context);
            out_decision->align_stable_reset_reason = "both_margins_missing";
            out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_ARRIVED;
            out_decision->reason = "both_margins_missing_centered";
            out_decision->consume_turn_check = context->turn_check_pending;
            out_decision->direct_arrival = true;
            context->state = out_decision->next_state;
            return ESP_OK;
        }
        if (requested_turn != REHAB_PERSON_FOLLOW_ACTION_NONE) {
            out_decision->align_stable_reset =
                person_follow_reset_align_stable(context);
            out_decision->align_stable_reset_reason = adjustment_reason;
            plan_turn_for_result(context, requested_turn, false,
                                 out_decision);
            out_decision->align_turn = true;
            context->state = out_decision->next_state;
            return ESP_OK;
        }

        out_decision->consume_turn_check = context->turn_check_pending;

        context->last_stable_frame_id = frame_id;
        if (context->align_stable_count <
                REHAB_PERSON_FOLLOW_ALIGN_STABLE_FRAMES_REQUIRED) {
            ++context->align_stable_count;
        }
        if (context->align_stable_count <
                REHAB_PERSON_FOLLOW_ALIGN_STABLE_FRAMES_REQUIRED) {
            out_decision->reason = first_gate
                ? "first_align_stable_1_of_2"
                : "final_align_stable_1_of_2";
            context->state = out_decision->next_state;
            return ESP_OK;
        }

        if (first_gate) {
            context->first_align_completed = true;
            context->approach_phase =
                REHAB_PERSON_FOLLOW_APPROACH_NORMAL_FOLLOW;
            context->active_align_gate =
                REHAB_PERSON_FOLLOW_ALIGN_GATE_NONE;
            context->align_stable_count = 0U;
            context->last_stable_frame_id = 0U;
            out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_SENSE;
            out_decision->reason = "first_align_stable_2_of_2_complete";
            context->state = out_decision->next_state;
            return ESP_OK;
        }

        context->approach_phase =
            REHAB_PERSON_FOLLOW_APPROACH_FINAL_FORWARD;
        context->active_align_gate = REHAB_PERSON_FOLLOW_ALIGN_GATE_NONE;
        context->final_forward_attempted_frame_id = frame_id;
        out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_FINAL_FORWARD;
        out_decision->action = REHAB_PERSON_FOLLOW_ACTION_FORWARD;
        out_decision->forward_attempted = true;
        out_decision->hold_ms = REHAB_PERSON_FOLLOW_FINAL_FORWARD_HOLD_MS;
        out_decision->final_forward = true;
        out_decision->reason = "final_align_stable_2_of_2_final_forward";
        context->state = out_decision->next_state;
        return ESP_OK;
    }

    float center_ratio = out_decision->person_center_x_ratio;
    if (!isfinite(center_ratio) || center_ratio < 0.0f ||
        center_ratio > 1.0f) {
        out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_RECOVER;
        out_decision->reason = "bbox_center_invalid";
        context->state = out_decision->next_state;
        return ESP_ERR_INVALID_RESPONSE;
    }
    float left_boundary = context->first_align_completed
        ? REHAB_PERSON_FOLLOW_FIRST_ALIGN_LEFT_BOUNDARY
        : PERSON_FOLLOW_LEFT_BOUNDARY;
    float right_boundary = context->first_align_completed
        ? REHAB_PERSON_FOLLOW_FIRST_ALIGN_RIGHT_BOUNDARY
        : PERSON_FOLLOW_RIGHT_BOUNDARY;
    if (center_ratio < left_boundary) {
        out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_ALIGN;
        plan_turn_for_result(context,
                             REHAB_PERSON_FOLLOW_ACTION_TURN_LEFT,
                             false, out_decision);
        out_decision->target_guided_turn = true;
    } else if (center_ratio > right_boundary) {
        out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_ALIGN;
        plan_turn_for_result(context,
                             REHAB_PERSON_FOLLOW_ACTION_TURN_RIGHT,
                             false, out_decision);
        out_decision->target_guided_turn = true;
    } else {
        out_decision->next_state = REHAB_PERSON_FOLLOW_STATE_FOLLOW_FORWARD;
        out_decision->action = REHAB_PERSON_FOLLOW_ACTION_FORWARD;
        out_decision->forward_attempted = true;
        out_decision->hold_ms = forward_hold_ms;
        out_decision->reason = "target_center_forward";
        out_decision->consume_turn_check = context->turn_check_pending;
    }
    context->state = out_decision->next_state;
    return ESP_OK;
}
