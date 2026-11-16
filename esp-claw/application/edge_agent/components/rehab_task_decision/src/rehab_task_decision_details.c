// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "claw_paths.h"
#include "esp_log.h"
#include "rehab_task_decision_details_binding.h"

#define DETAILS_SCHEMA "rehab_selected_task_details.v1"
#define DETAILS_DIRECTORY_SUBPATH "rehab"
#define DETAILS_SUBPATH "rehab/selected_task_details.json"
#define DETAILS_NEXT_SUBPATH "rehab/selected_task_details.json.next"
#define DETAILS_PREVIOUS_SUBPATH "rehab/selected_task_details.json.previous"
#define DETAILS_PATH_MAX 192U

static const char *TAG = "rehab_decision_details";

static bool details_valid(const cJSON *root);

static const char *s_breakdown_factors[] = {
    "difficulty", "advice", "compensation",
    "frequency", "perception", "final_raw",
};

static const char *canonical_entry_source(const char *source)
{
    if (source != NULL &&
        (strcmp(source, "local_ui") == 0 ||
         strcmp(source, "wechat") == 0 ||
         strcmp(source, "console") == 0)) {
        return source;
    }
    return "unknown";
}

static bool details_lock(rehab_task_decision_handle_t handle)
{
    return handle != NULL && handle->selected_task_lock != NULL &&
        xSemaphoreTake(handle->selected_task_lock,
                       pdMS_TO_TICKS(2000)) == pdTRUE;
}

static void details_unlock(rehab_task_decision_handle_t handle)
{
    xSemaphoreGive(handle->selected_task_lock);
}

static const cJSON *field(const cJSON *object, const char *name)
{
    return cJSON_IsObject(object)
        ? cJSON_GetObjectItemCaseSensitive(object, name) : NULL;
}

static const cJSON *candidate_by_ref(const cJSON *array, size_t ref)
{
    const cJSON *candidate = NULL;
    cJSON_ArrayForEach(candidate, array) {
        const cJSON *item = field(candidate, "candidate_ref");
        if (cJSON_IsNumber(item) && isfinite(item->valuedouble) &&
            item->valuedouble == (double)ref) {
            return candidate;
        }
    }
    return NULL;
}

static bool add_nullable_string(cJSON *object, const char *name,
                                const cJSON *source)
{
    return cJSON_IsString(source) && source->valuestring != NULL
        ? cJSON_AddStringToObject(object, name, source->valuestring) != NULL
        : cJSON_AddNullToObject(object, name) != NULL;
}

static bool add_nullable_number(cJSON *object, const char *name,
                                const cJSON *source)
{
    return cJSON_IsNumber(source) && isfinite(source->valuedouble)
        ? cJSON_AddNumberToObject(object, name, source->valuedouble) != NULL
        : cJSON_AddNullToObject(object, name) != NULL;
}

static bool add_required_copy(cJSON *object, const char *name,
                              const cJSON *source)
{
    cJSON *copy = source != NULL ? cJSON_Duplicate(source, true) : NULL;
    if (copy == NULL || !cJSON_AddItemToObject(object, name, copy)) {
        cJSON_Delete(copy);
        return false;
    }
    return true;
}

static const char *direction_for(double value)
{
    if (value > 0.0) return "positive";
    if (value < 0.0) return "negative";
    return "neutral";
}

static cJSON *build_breakdown(const cJSON *source)
{
    if (cJSON_IsNull(source)) {
        return cJSON_CreateNull();
    }
    if (!cJSON_IsObject(source)) {
        return NULL;
    }
    cJSON *result = cJSON_CreateObject();
    if (result == NULL) return NULL;
    for (size_t i = 0; i < sizeof(s_breakdown_factors) /
                               sizeof(s_breakdown_factors[0]); ++i) {
        const char *name = s_breakdown_factors[i];
        const cJSON *value = field(source, name);
        cJSON *factor = cJSON_CreateObject();
        if (!cJSON_IsNumber(value) || !isfinite(value->valuedouble) ||
            factor == NULL ||
            cJSON_AddNumberToObject(factor, "value",
                                    value->valuedouble) == NULL ||
            cJSON_AddStringToObject(factor, "direction",
                                    direction_for(value->valuedouble)) == NULL ||
            !cJSON_AddItemToObject(result, name, factor)) {
            cJSON_Delete(factor);
            cJSON_Delete(result);
            return NULL;
        }
    }
    return result;
}

static cJSON *build_target_summary(const cJSON *input_candidate)
{
    const cJSON *facts = field(input_candidate, "target_facts");
    cJSON *summary = cJSON_IsObject(facts)
        ? cJSON_Duplicate(facts, true) : NULL;
    if (summary == NULL) return NULL;
    const cJSON *target_id = field(facts, "target_object_id");
    if (cJSON_AddBoolToObject(summary, "target_available",
                              cJSON_IsString(target_id)) == NULL) {
        cJSON_Delete(summary);
        return NULL;
    }
    return summary;
}

static cJSON *build_candidates(rehab_task_decision_handle_t handle,
                               const cJSON *input_candidates,
                               const cJSON *output_candidates)
{
    cJSON *result = cJSON_CreateArray();
    if (result == NULL) return NULL;
    for (size_t ref = 0; ref < REHAB_TASK_DECISION_TASK_COUNT; ++ref) {
        const cJSON *input = candidate_by_ref(input_candidates, ref);
        const cJSON *output = candidate_by_ref(output_candidates, ref);
        const rehab_task_policy_definition_t *task =
            rehab_task_policy_task_at(ref);
        const cJSON *availability = field(output, "availability");
        const cJSON *gates = field(input, "hard_gates");
        const cJSON *blocked_reason = field(gates, "blocked_reason");
        const cJSON *score = field(output, "score");
        const cJSON *reason = field(output, "reason");
        cJSON *entry = cJSON_CreateObject();
        cJSON *breakdown = build_breakdown(field(output,
                                                   "score_breakdown"));
        cJSON *target_summary = build_target_summary(input);
        if (!cJSON_IsObject(input) || !cJSON_IsObject(output) ||
            task == NULL || !cJSON_IsString(availability) ||
            !cJSON_IsString(blocked_reason) || !cJSON_IsNumber(score) ||
            !cJSON_IsString(reason) || entry == NULL || breakdown == NULL ||
            target_summary == NULL ||
            cJSON_AddNumberToObject(entry, "candidate_ref",
                                    (double)ref) == NULL ||
            cJSON_AddStringToObject(entry, "task_name",
                                     task->task_name) == NULL ||
            cJSON_AddNumberToObject(entry, "level", task->level) == NULL ||
            cJSON_AddStringToObject(entry, "semantic_group",
                                    task->semantic_group) == NULL ||
            cJSON_AddStringToObject(entry, "required_plane",
                                    task->plane) == NULL ||
            cJSON_AddStringToObject(entry, "availability",
                                    availability->valuestring) == NULL ||
            cJSON_AddStringToObject(entry, "blocked_reason",
                                    blocked_reason->valuestring) == NULL ||
            cJSON_AddNumberToObject(entry, "score",
                                    score->valuedouble) == NULL ||
            cJSON_AddStringToObject(entry, "reason",
                                     reason->valuestring) == NULL) {
            cJSON_Delete(breakdown);
            cJSON_Delete(target_summary);
            cJSON_Delete(entry);
            cJSON_Delete(result);
            return NULL;
        }
        if (!add_required_copy(entry, "hard_gates", gates)) {
            cJSON_Delete(breakdown);
            cJSON_Delete(target_summary);
            cJSON_Delete(entry);
            cJSON_Delete(result);
            return NULL;
        }
        if (!cJSON_AddItemToObject(entry, "score_breakdown", breakdown)) {
            cJSON_Delete(breakdown);
            cJSON_Delete(target_summary);
            cJSON_Delete(entry);
            cJSON_Delete(result);
            return NULL;
        }
        if (!cJSON_AddItemToObject(entry, "target_summary",
                                   target_summary)) {
            cJSON_Delete(target_summary);
            cJSON_Delete(entry);
            cJSON_Delete(result);
            return NULL;
        }
        if (!cJSON_AddItemToArray(result, entry)) {
            cJSON_Delete(entry);
            cJSON_Delete(result);
            return NULL;
        }
    }
    for (size_t ref = 0; ref < REHAB_TASK_DECISION_TASK_COUNT; ++ref) {
        cJSON *entry = cJSON_GetArrayItem(result, (int)ref);
        const cJSON *entry_availability = field(entry, "availability");
        const cJSON *entry_score = field(entry, "score");
        if (cJSON_IsString(entry_availability) &&
            strcmp(entry_availability->valuestring, "available") == 0 &&
            cJSON_IsNumber(entry_score)) {
            unsigned rank = 1U;
            for (size_t other = 0; other < REHAB_TASK_DECISION_TASK_COUNT;
                 ++other) {
                const cJSON *other_entry = cJSON_GetArrayItem(
                    result, (int)other);
                const cJSON *other_availability = field(
                    other_entry, "availability");
                const cJSON *other_score = field(other_entry, "score");
                if (cJSON_IsString(other_availability) &&
                    strcmp(other_availability->valuestring, "available") == 0 &&
                    cJSON_IsNumber(other_score) &&
                    (other_score->valuedouble > entry_score->valuedouble ||
                     (fabs(other_score->valuedouble -
                           entry_score->valuedouble) < 0.000001 &&
                      other < ref))) {
                    ++rank;
                }
            }
            if (cJSON_AddNumberToObject(entry, "rank", rank) == NULL) {
                cJSON_Delete(result);
                return NULL;
            }
        } else if (cJSON_AddNullToObject(entry, "rank") == NULL) {
            cJSON_Delete(result);
            return NULL;
        }
    }
    (void)handle;
    return result;
}

static cJSON *build_history(const cJSON *performance)
{
    cJSON *history = cJSON_CreateObject();
    cJSON *records = history != NULL
        ? cJSON_AddArrayToObject(history, "records") : NULL;
    const cJSON *source_records = field(performance, "records");
    if (history == NULL || records == NULL ||
        !add_nullable_number(history, "record_count",
                             field(performance, "record_count")) ||
        !add_nullable_string(history, "tendency_level",
                             field(performance, "tendency_level"))) {
        cJSON_Delete(history);
        return NULL;
    }
    const cJSON *source = NULL;
    cJSON_ArrayForEach(source, source_records) {
        cJSON *record = cJSON_CreateObject();
        if (record == NULL ||
            !add_nullable_string(record, "task_name",
                                 field(source, "task_name")) ||
            !add_nullable_number(record, "average_score",
                                 field(source, "average_score")) ||
            !cJSON_AddItemToArray(records, record)) {
            cJSON_Delete(record);
            cJSON_Delete(history);
            return NULL;
        }
    }
    return history;
}

static cJSON *build_compensation(const cJSON *source)
{
    cJSON *result = cJSON_CreateObject();
    if (result == NULL ||
        !add_nullable_string(result, "status", field(source, "status")) ||
        !add_nullable_string(result, "task_name",
                             field(source, "task_name")) ||
        !add_nullable_string(result, "exact_task_id",
                             field(source, "exact_task_id")) ||
        !add_nullable_string(result, "action_family",
                             field(source, "action_family")) ||
        !add_nullable_string(result, "mapped_group",
                             field(source, "mapped_group")) ||
        !add_nullable_string(result, "mapping_status",
                             field(source, "mapping_status")) ||
        !add_nullable_number(result, "severity",
                             field(source, "severity")) ||
        !add_nullable_number(result, "compensation_action_count",
                             field(source, "compensation_action_count"))) {
        cJSON_Delete(result);
        return NULL;
    }
    return result;
}

static cJSON *build_frequency(const cJSON *source)
{
    cJSON *result = cJSON_CreateObject();
    cJSON *records = result != NULL
        ? cJSON_AddArrayToObject(result, "records") : NULL;
    if (result == NULL || records == NULL ||
        !add_nullable_number(result, "mapped_record_count",
                             field(source, "mapped_record_count")) ||
        !add_nullable_number(result, "cup_count",
                             field(source, "cup_count")) ||
        !add_nullable_number(result, "ball_count",
                             field(source, "ball_count"))) {
        cJSON_Delete(result);
        return NULL;
    }
    const cJSON *record = NULL;
    cJSON_ArrayForEach(record, field(source, "records")) {
        cJSON *copy = cJSON_CreateObject();
        if (copy == NULL ||
            !add_nullable_string(copy, "task_name",
                                 field(record, "task_name")) ||
            !add_nullable_string(copy, "mapped_group",
                                 field(record, "mapped_group")) ||
            !cJSON_AddItemToArray(records, copy)) {
            cJSON_Delete(copy);
            cJSON_Delete(result);
            return NULL;
        }
    }
    return result;
}

static cJSON *build_decision_inputs(const cJSON *input,
                                    const cJSON *winner_input)
{
    const cJSON *binding = field(input, "decision_binding");
    const cJSON *ability = field(input, "patient_ability");
    const cJSON *condition = field(input, "patient_condition");
    const cJSON *performance = field(input, "recent_performance");
    const cJSON *compensation = field(input, "latest_compensation");
    const cJSON *frequency = field(input, "item_frequency");
    const cJSON *target_facts = field(winner_input, "target_facts");
    cJSON *result = cJSON_CreateObject();
    cJSON *scene = result != NULL
        ? cJSON_AddObjectToObject(result, "scene_source") : NULL;
    cJSON *history = build_history(performance);
    cJSON *compensation_copy = build_compensation(compensation);
    cJSON *frequency_copy = build_frequency(frequency);
    if (result == NULL || scene == NULL || history == NULL ||
        compensation_copy == NULL || frequency_copy == NULL ||
        !add_nullable_number(result, "ability_level",
                             field(ability, "level")) ||
        !add_nullable_string(result, "user_condition_source",
                             field(condition, "source")) ||
        !add_nullable_number(result, "recent_score_count",
                             field(performance, "record_count")) ||
        !add_nullable_string(scene, "scan_id",
                             field(binding, "source_scan_id")) ||
        !add_nullable_string(scene, "scene_sha256",
                             field(binding, "source_scene_sha256")) ||
        cJSON_AddNumberToObject(result, "candidate_count",
                                REHAB_TASK_DECISION_TASK_COUNT) == NULL ||
        cJSON_AddBoolToObject(result, "target_available",
                              cJSON_IsString(field(
                                  target_facts,
                                  "target_object_id"))) == NULL) {
        cJSON_Delete(history);
        cJSON_Delete(compensation_copy);
        cJSON_Delete(frequency_copy);
        cJSON_Delete(result);
        return NULL;
    }
    if (!cJSON_AddItemToObject(result, "recent_history_summary", history)) {
        cJSON_Delete(history);
        cJSON_Delete(compensation_copy);
        cJSON_Delete(frequency_copy);
        cJSON_Delete(result);
        return NULL;
    }
    if (!cJSON_AddItemToObject(result, "compensation_facts",
                               compensation_copy)) {
        cJSON_Delete(compensation_copy);
        cJSON_Delete(frequency_copy);
        cJSON_Delete(result);
        return NULL;
    }
    if (!cJSON_AddItemToObject(result, "frequency_facts", frequency_copy)) {
        cJSON_Delete(frequency_copy);
        cJSON_Delete(result);
        return NULL;
    }
    return result;
}

static bool normal_created_at(const cJSON *item)
{
    return cJSON_IsString(item) && item->valuestring != NULL &&
        item->valuestring[0] != '\0' &&
        strncmp(item->valuestring, "1970-01-01", 10) != 0;
}

static cJSON *build_target_details(const cJSON *selected,
                                   const cJSON *winner_input)
{
    const cJSON *target = field(selected, "target_object");
    const cJSON *geometry = field(target, "geometry");
    const cJSON *winner_facts = field(winner_input, "target_facts");
    cJSON *result = cJSON_CreateObject();
    if (result == NULL) return NULL;
    const cJSON *sequence = field(target, "source_frame_sequence");
    const cJSON *scan_id = field(selected, "source_scan_id");
    char image_reference[256] = {0};
    if (cJSON_IsString(scan_id) && cJSON_IsNumber(sequence)) {
        int written = snprintf(
            image_reference, sizeof(image_reference),
            "rehab/scans/%s/frames/frame_%06u/image.rgb565",
            scan_id->valuestring, (unsigned)sequence->valuedouble);
        if (written <= 0 || (size_t)written >= sizeof(image_reference)) {
            image_reference[0] = '\0';
        }
    }
    const cJSON *class_name = field(target, "class_name");
    if (!cJSON_IsString(class_name)) class_name = field(target, "class");
    const cJSON *bbox = field(target, "bbox_xyxy");
    cJSON *bbox_copy = cJSON_IsArray(bbox)
        ? cJSON_Duplicate(bbox, true) : cJSON_CreateNull();
    if (bbox_copy == NULL ||
        !add_nullable_string(result, "object_id",
                             field(target, "object_id")) ||
        !add_nullable_string(result, "class_name", class_name) ||
        !add_nullable_string(result, "source_frame_id",
                             field(target, "source_frame_id")) ||
        !add_nullable_number(result, "source_frame_sequence", sequence) ||
        cJSON_AddNullToObject(result, "logical_object_id") == NULL ||
        (image_reference[0] != '\0'
            ? cJSON_AddStringToObject(result, "image_reference_path",
                                      image_reference) == NULL
            : cJSON_AddNullToObject(result,
                                    "image_reference_path") == NULL) ||
        !add_nullable_number(result, "object_height_mm",
                             field(geometry, "object_height_mm")) ||
        !add_nullable_number(result, "support_height_mm",
                             field(geometry, "support_height_mm")) ||
        !add_nullable_number(result, "top_height_mm",
                             field(geometry,
                                   "top_height_above_floor_mm")) ||
        !add_nullable_number(result, "distance_mm",
                             field(geometry, "distance_mm")) ||
        !add_nullable_number(result, "geometry_confidence",
                             field(geometry, "geometry_confidence")) ||
        !add_nullable_number(result, "height_confidence",
                             field(winner_facts, "height_confidence")) ||
        !add_nullable_number(result, "detection_confidence",
                             field(target, "confidence")) ||
        (cJSON_IsObject(geometry)
            ? cJSON_AddBoolToObject(result, "geometry_valid",
                                    cJSON_IsTrue(field(
                                        geometry, "valid"))) == NULL
            : cJSON_AddNullToObject(result, "geometry_valid") == NULL)) {
        cJSON_Delete(bbox_copy);
        cJSON_Delete(result);
        return NULL;
    }
    if (!cJSON_AddItemToObject(result, "bbox", bbox_copy)) {
        cJSON_Delete(bbox_copy);
        cJSON_Delete(result);
        return NULL;
    }
    return result;
}

static const char *source_status(const cJSON *source)
{
    if (!cJSON_IsString(source) || source->valuestring == NULL) {
        return "unavailable";
    }
    return strcmp(source->valuestring, "none") == 0
        ? "not_provided" : "available";
}

static cJSON *build_availability(const cJSON *input,
                                 const cJSON *selected,
                                 const cJSON *winner_input)
{
    const cJSON *condition = field(input, "patient_condition");
    const cJSON *performance = field(input, "recent_performance");
    const cJSON *compensation = field(input, "latest_compensation");
    const cJSON *frequency = field(input, "item_frequency");
    const cJSON *target = field(selected, "target_object");
    const cJSON *geometry = field(target, "geometry");
    const cJSON *winner_facts = field(winner_input, "target_facts");
    cJSON *result = cJSON_CreateObject();
    if (result == NULL ||
        cJSON_AddStringToObject(result, "patient_condition",
            source_status(field(condition, "source"))) == NULL ||
        cJSON_AddStringToObject(result, "recent_history",
            cJSON_IsNumber(field(performance, "record_count")) &&
            field(performance, "record_count")->valuedouble > 0.0
                ? "available" : "unavailable") == NULL ||
        cJSON_AddStringToObject(result, "compensation",
            cJSON_IsObject(compensation) ? "available" : "unavailable") == NULL ||
        cJSON_AddStringToObject(result, "frequency",
            cJSON_IsObject(frequency) ? "available" : "unavailable") == NULL ||
        cJSON_AddStringToObject(result, "target_image",
            cJSON_IsString(field(target, "source_frame_id"))
                ? "available" : "unavailable") == NULL ||
        cJSON_AddStringToObject(result, "logical_object_id",
                                "not_integrated") == NULL ||
        cJSON_AddStringToObject(result, "geometry_confidence",
            cJSON_IsNumber(field(geometry, "geometry_confidence"))
                ? "available" : "fallback") == NULL ||
        cJSON_AddStringToObject(result, "height_confidence",
            cJSON_IsNumber(field(winner_facts, "height_confidence"))
                ? "available" : "unavailable") == NULL) {
        cJSON_Delete(result);
        return NULL;
    }
    return result;
}

cJSON *rehab_task_decision_details_build(
    rehab_task_decision_handle_t handle,
    const char *entry_source,
    const cJSON *decision_input,
    const cJSON *validated_output,
    const cJSON *committed_selected,
    const rehab_task_decision_selected_snapshot_t *readback)
{
    if (handle == NULL || !cJSON_IsObject(decision_input) ||
        !cJSON_IsObject(validated_output) ||
        !cJSON_IsObject(committed_selected) || readback == NULL ||
        !readback->available || readback->decision_id[0] == '\0') {
        return NULL;
    }
    const cJSON *winner = field(validated_output, "winner_ref");
    if (!cJSON_IsNumber(winner) || winner->valuedouble < 0.0 ||
        winner->valuedouble >= REHAB_TASK_DECISION_TASK_COUNT) {
        return NULL;
    }
    size_t winner_ref = (size_t)winner->valuedouble;
    const cJSON *input_candidates = field(decision_input, "candidates");
    const cJSON *output_candidates = field(validated_output, "candidates");
    const cJSON *winner_input = candidate_by_ref(input_candidates,
                                                  winner_ref);
    const cJSON *winner_output = candidate_by_ref(output_candidates,
                                                   winner_ref);
    const cJSON *created_at = field(committed_selected, "created_at");
    const cJSON *generation = field(committed_selected,
                                    "decision_generation");
    const cJSON *winner_availability = field(winner_output,
                                              "availability");
    const cJSON *deepseek_summary = field(validated_output, "summary");
    const cJSON *advice_basis = field(validated_output, "advice_basis");
    cJSON *root = cJSON_CreateObject();
    cJSON *selected_task = root != NULL
        ? cJSON_AddObjectToObject(root, "selected_task") : NULL;
    cJSON *inputs = build_decision_inputs(decision_input, winner_input);
    cJSON *candidates = build_candidates(handle, input_candidates,
                                          output_candidates);
    cJSON *target = build_target_details(committed_selected, winner_input);
    cJSON *availability = build_availability(
        decision_input, committed_selected, winner_input);
    const bool time_available = normal_created_at(created_at);
    if (root == NULL || selected_task == NULL || inputs == NULL ||
        candidates == NULL || target == NULL || availability == NULL ||
        cJSON_AddStringToObject(root, "schema_version",
                                DETAILS_SCHEMA) == NULL ||
        cJSON_AddStringToObject(root, "decision_id",
                                 readback->decision_id) == NULL ||
        cJSON_AddStringToObject(root, "source_scan_id",
                                readback->source_scan_id) == NULL ||
        cJSON_AddStringToObject(root, "source_scene_sha256",
                                readback->source_scene_sha256) == NULL ||
        (!cJSON_IsNumber(generation) ||
         cJSON_AddNumberToObject(root, "generation",
                                 generation->valuedouble) == NULL) ||
        cJSON_AddStringToObject(root, "entry_source",
                                canonical_entry_source(entry_source)) == NULL ||
        (time_available
            ? cJSON_AddStringToObject(root, "created_at",
                                      created_at->valuestring) == NULL
            : cJSON_AddNullToObject(root, "created_at") == NULL) ||
        cJSON_AddStringToObject(root, "time_status",
            time_available ? "available" : "unavailable") == NULL ||
        cJSON_AddStringToObject(root, "details_generation_status",
                                "committed") == NULL ||
        cJSON_AddNumberToObject(root, "winner_ref",
                                (double)winner_ref) == NULL ||
        cJSON_AddNumberToObject(selected_task, "winner_ref",
                                (double)winner_ref) == NULL ||
        cJSON_AddStringToObject(selected_task, "task_id",
                                readback->task_id) == NULL ||
        cJSON_AddStringToObject(selected_task, "task_name",
                                readback->task_name) == NULL ||
        cJSON_AddNumberToObject(selected_task, "score",
                                readback->score) == NULL ||
        cJSON_AddStringToObject(selected_task, "reason",
                                readback->reason) == NULL ||
        (!cJSON_IsString(winner_availability) ||
         cJSON_AddStringToObject(selected_task, "availability",
                                  winner_availability->valuestring) == NULL)) {
        cJSON_Delete(inputs);
        cJSON_Delete(candidates);
        cJSON_Delete(target);
        cJSON_Delete(availability);
        cJSON_Delete(root);
        return NULL;
    }
    const char *copy_names[] = {
        "patient_condition", "patient_ability",
        "recent_performance", "item_frequency", "latest_compensation",
        "scoring_policy",
    };
    for (size_t i = 0U; i < sizeof(copy_names) / sizeof(copy_names[0]); ++i) {
        if (!add_required_copy(root, copy_names[i],
                               field(decision_input, copy_names[i]))) {
            cJSON_Delete(inputs);
            cJSON_Delete(candidates);
            cJSON_Delete(target);
            cJSON_Delete(availability);
            cJSON_Delete(root);
            return NULL;
        }
    }
    if (!add_required_copy(root, "advice_basis", advice_basis) ||
        !cJSON_IsString(deepseek_summary) ||
        cJSON_AddStringToObject(root, "deepseek_summary",
                                deepseek_summary->valuestring) == NULL) {
        cJSON_Delete(inputs);
        cJSON_Delete(candidates);
        cJSON_Delete(target);
        cJSON_Delete(availability);
        cJSON_Delete(root);
        return NULL;
    }
    if (!add_required_copy(root, "winner", selected_task)) {
        cJSON_Delete(inputs);
        cJSON_Delete(candidates);
        cJSON_Delete(target);
        cJSON_Delete(availability);
        cJSON_Delete(root);
        return NULL;
    }
    if (!cJSON_AddItemToObject(root, "decision_inputs", inputs)) {
        cJSON_Delete(inputs);
        cJSON_Delete(candidates);
        cJSON_Delete(target);
        cJSON_Delete(availability);
        cJSON_Delete(root);
        return NULL;
    }
    if (!cJSON_AddItemToObject(root, "candidates", candidates)) {
        cJSON_Delete(candidates);
        cJSON_Delete(target);
        cJSON_Delete(availability);
        cJSON_Delete(root);
        return NULL;
    }
    if (!cJSON_AddItemToObject(root, "target_details", target)) {
        cJSON_Delete(target);
        cJSON_Delete(availability);
        cJSON_Delete(root);
        return NULL;
    }
    if (!cJSON_AddItemToObject(root, "data_availability", availability)) {
        cJSON_Delete(availability);
        cJSON_Delete(root);
        return NULL;
    }
    ESP_LOGI(TAG,
             "SELECTED_TASK_DETAILS_BUILD decision_id=%s candidate_count=%u winner_ref=%u",
             readback->decision_id, (unsigned)REHAB_TASK_DECISION_TASK_COUNT,
             (unsigned)winner_ref);
    return root;
}

static esp_err_t details_paths(char directory[DETAILS_PATH_MAX],
                               char final_path[DETAILS_PATH_MAX],
                               char next_path[DETAILS_PATH_MAX],
                               char previous_path[DETAILS_PATH_MAX])
{
    esp_err_t err = claw_paths_join(CLAW_PATH_DATA,
                                    DETAILS_DIRECTORY_SUBPATH,
                                    directory, DETAILS_PATH_MAX);
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA, DETAILS_SUBPATH,
                              final_path, DETAILS_PATH_MAX);
    }
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA, DETAILS_NEXT_SUBPATH,
                              next_path, DETAILS_PATH_MAX);
    }
    if (err == ESP_OK) {
        err = claw_paths_join(CLAW_PATH_DATA, DETAILS_PREVIOUS_SUBPATH,
                              previous_path, DETAILS_PATH_MAX);
    }
    return err;
}

static esp_err_t ensure_directory(const char *path)
{
    struct stat info = {0};
    if (stat(path, &info) == 0) {
        return S_ISDIR(info.st_mode) ? ESP_OK : ESP_ERR_INVALID_STATE;
    }
    if (errno != ENOENT) return ESP_FAIL;
    return mkdir(path, 0755) == 0 || errno == EEXIST ? ESP_OK : ESP_FAIL;
}

static bool regular_file_exists(const char *path)
{
    struct stat info = {0};
    return path != NULL && stat(path, &info) == 0 && S_ISREG(info.st_mode);
}

static rehab_task_details_authority_t details_authority(
    const rehab_task_decision_selected_snapshot_t *selected)
{
    rehab_task_details_authority_t authority = {0};
    if (selected != NULL) {
        authority.decision_id = selected->decision_id;
        authority.generation = selected->decision_generation;
        authority.source_scan_id = selected->source_scan_id;
        authority.source_scene_sha256 = selected->source_scene_sha256;
        authority.task_id = selected->task_id;
        authority.task_name = selected->task_name;
        authority.reason = selected->reason;
        authority.score = selected->score;
        authority.target_available = selected->target_available;
        authority.target_object_id = selected->target_object_id;
    }
    return authority;
}

static esp_err_t read_and_validate_details(
    const char *path,
    const rehab_task_details_authority_t *authority,
    const char **stage,
    const char *read_stage,
    const char *validate_stage,
    const char *bind_stage)
{
    if (stage != NULL) *stage = read_stage;
    struct stat info = {0};
    if (path == NULL || authority == NULL) return ESP_ERR_INVALID_ARG;
    if (stat(path, &info) != 0) {
        return errno == ENOENT ? ESP_ERR_NOT_FOUND : ESP_FAIL;
    }
    if (!S_ISREG(info.st_mode) || info.st_size <= 0 ||
        (uint64_t)info.st_size >= REHAB_TASK_DECISION_DETAILS_JSON_MAX) {
        return ESP_ERR_INVALID_SIZE;
    }
    char *bytes = calloc(1U, (size_t)info.st_size + 1U);
    if (bytes == NULL) return ESP_ERR_NO_MEM;
    FILE *file = fopen(path, "rb");
    esp_err_t err = file != NULL ? ESP_OK : ESP_FAIL;
    if (err == ESP_OK && fread(bytes, 1U, (size_t)info.st_size, file) !=
                            (size_t)info.st_size) {
        err = ESP_FAIL;
    }
    if (file != NULL && fclose(file) != 0 && err == ESP_OK) err = ESP_FAIL;
    cJSON *root = err == ESP_OK
        ? cJSON_ParseWithOpts(bytes, NULL, true) : NULL;
    free(bytes);
    if (err == ESP_OK) {
        if (stage != NULL) *stage = validate_stage;
        if (!details_valid(root)) err = ESP_ERR_INVALID_RESPONSE;
    }
    if (err == ESP_OK) {
        if (stage != NULL) *stage = bind_stage;
        if (!rehab_task_decision_details_binding_matches(root, authority)) {
            err = ESP_ERR_INVALID_RESPONSE;
        }
    }
    if (err == ESP_OK && root == NULL) {
        err = ESP_ERR_INVALID_RESPONSE;
    }
    cJSON_Delete(root);
    return err;
}

static void details_failure_log(const cJSON *details, const char *stage,
                                esp_err_t err, int saved_errno,
                                size_t serialized_bytes,
                                const char *final_path,
                                const char *next_path,
                                const char *previous_path)
{
    const cJSON *decision_id = field(details, "decision_id");
    ESP_LOGW(TAG,
             "SELECTED_TASK_DETAILS_TRANSACTION_FAILED decision_id=%s stage=%s error=%s(%d) errno=%d errno_text=%s serialized_bytes=%u max_bytes=%u final=%d next=%d previous=%d",
             cJSON_IsString(decision_id) ? decision_id->valuestring : "unknown",
             stage != NULL ? stage : "unknown", esp_err_to_name(err), (int)err,
             saved_errno, saved_errno != 0 ? strerror(saved_errno) : "none",
             (unsigned)serialized_bytes,
             (unsigned)REHAB_TASK_DECISION_DETAILS_JSON_MAX,
             regular_file_exists(final_path), regular_file_exists(next_path),
             regular_file_exists(previous_path));
}

esp_err_t rehab_task_decision_details_write_atomic(
    rehab_task_decision_handle_t handle,
    const cJSON *details,
    const rehab_task_decision_selected_snapshot_t *selected,
    const char **out_stage)
{
    const char *stage = "serialize";
    if (out_stage != NULL) *out_stage = stage;
    if (handle == NULL || !cJSON_IsObject(details) || selected == NULL ||
        !selected->available) {
        return ESP_ERR_INVALID_ARG;
    }
    char *bytes = cJSON_PrintUnformatted(details);
    if (bytes == NULL) {
        ESP_LOGW(TAG,
                 "SELECTED_TASK_DETAILS_TRANSACTION_FAILED decision_id=unknown stage=serialize error=%s(%d) errno=0 errno_text=none serialized_bytes=0 max_bytes=%u final=0 next=0 previous=0",
                 esp_err_to_name(ESP_ERR_NO_MEM), (int)ESP_ERR_NO_MEM,
                 (unsigned)REHAB_TASK_DECISION_DETAILS_JSON_MAX);
        return ESP_ERR_NO_MEM;
    }
    size_t length = strlen(bytes);
    stage = "size_check";
    if (out_stage != NULL) *out_stage = stage;
    if (length == 0U || length >= REHAB_TASK_DECISION_DETAILS_JSON_MAX) {
        details_failure_log(details, stage, ESP_ERR_INVALID_SIZE, 0, length,
                            NULL, NULL, NULL);
        cJSON_free(bytes);
        return ESP_ERR_INVALID_SIZE;
    }
    stage = "lock";
    if (out_stage != NULL) *out_stage = stage;
    if (!details_lock(handle)) {
        cJSON_free(bytes);
        return ESP_ERR_TIMEOUT;
    }
    char directory[DETAILS_PATH_MAX] = {0};
    char final_path[DETAILS_PATH_MAX] = {0};
    char next_path[DETAILS_PATH_MAX] = {0};
    char previous_path[DETAILS_PATH_MAX] = {0};
    FILE *file = NULL;
    bool backed_up = false;
    bool promoted = false;
    int saved_errno = 0;
    rehab_task_details_authority_t authority = details_authority(selected);

    stage = "path_resolve";
    esp_err_t err = details_paths(directory, final_path, next_path,
                                  previous_path);
    if (err != ESP_OK) goto done;
    stage = "mkdir";
    err = ensure_directory(directory);
    if (err != ESP_OK) { saved_errno = errno; goto done; }

    stage = "stale_recovery";
    if (regular_file_exists(previous_path)) {
        if (regular_file_exists(final_path)) {
            if (remove(previous_path) != 0) {
                err = ESP_FAIL; saved_errno = errno; goto done;
            }
        } else if (rename(previous_path, final_path) != 0) {
            err = ESP_FAIL; saved_errno = errno; goto done;
        }
    }
    stage = "remove_next";
    if (regular_file_exists(next_path) && remove(next_path) != 0) {
        err = ESP_FAIL; saved_errno = errno; goto done;
    }
    stage = "open_next";
    file = fopen(next_path, "wb");
    if (file == NULL) { err = ESP_FAIL; saved_errno = errno; goto done; }
    stage = "write_next";
    if (fwrite(bytes, 1U, length, file) != length) {
        err = ESP_FAIL; saved_errno = errno; goto done;
    }
    stage = "flush_next";
    if (fflush(file) != 0) { err = ESP_FAIL; saved_errno = errno; goto done; }
    stage = "fsync_next";
    if (fsync(fileno(file)) != 0) {
        err = ESP_FAIL; saved_errno = errno; goto done;
    }
    stage = "close_next";
    if (fclose(file) != 0) {
        file = NULL; err = ESP_FAIL; saved_errno = errno; goto done;
    }
    file = NULL;
    errno = 0;
    err = read_and_validate_details(next_path, &authority, &stage,
                                    "readback_next", "validate_next",
                                    "bind_next");
    if (err != ESP_OK) { saved_errno = errno; goto done; }

    stage = "remove_previous";
    if (regular_file_exists(previous_path) && remove(previous_path) != 0) {
        err = ESP_FAIL; saved_errno = errno; goto done;
    }
    if (regular_file_exists(final_path)) {
        stage = "backup_final";
        if (rename(final_path, previous_path) != 0) {
            err = ESP_FAIL; saved_errno = errno; goto done;
        }
        backed_up = true;
    }
    stage = "promote_next";
    if (rename(next_path, final_path) != 0) {
        err = ESP_FAIL; saved_errno = errno; goto done;
    }
    promoted = true;
    errno = 0;
    err = read_and_validate_details(final_path, &authority, &stage,
                                    "readback_final", "validate_final",
                                    "bind_final");
    if (err != ESP_OK) { saved_errno = errno; goto done; }
    backed_up = false;
    promoted = false;
    stage = "cleanup";
    if (regular_file_exists(previous_path) && remove(previous_path) != 0) {
        err = ESP_FAIL; saved_errno = errno; goto done;
    }

done:
    if (file != NULL) {
        if (fclose(file) != 0 && err == ESP_OK) {
            err = ESP_FAIL;
            saved_errno = errno;
            stage = "close_next";
        }
        file = NULL;
    }
    if (err != ESP_OK) {
        const char *reported_stage = stage;
        (void)remove(next_path);
        if (promoted && regular_file_exists(final_path)) {
            (void)remove(final_path);
        }
        if (backed_up && regular_file_exists(previous_path)) {
            if (rename(previous_path, final_path) != 0) {
                saved_errno = errno;
                reported_stage = "restore_previous";
            }
        }
        stage = reported_stage;
        details_failure_log(details, reported_stage, err, saved_errno, length,
                            final_path, next_path, previous_path);
    }
    if (out_stage != NULL) *out_stage = stage;
    details_unlock(handle);
    cJSON_free(bytes);
    if (err == ESP_OK) {
        const cJSON *decision_id = field(details, "decision_id");
        ESP_LOGI(TAG, "SELECTED_TASK_DETAILS_COMMIT decision_id=%s path=%s",
                 cJSON_IsString(decision_id)
                    ? decision_id->valuestring : "unknown", final_path);
    }
    return err;
}

static bool details_valid(const cJSON *root)
{
    const cJSON *schema = field(root, "schema_version");
    const cJSON *decision_id = field(root, "decision_id");
    const cJSON *generation = field(root, "generation");
    const cJSON *source_scan_id = field(root, "source_scan_id");
    const cJSON *source_scene_sha256 = field(root, "source_scene_sha256");
    const cJSON *entry_source = field(root, "entry_source");
    const cJSON *winner = field(root, "winner_ref");
    const cJSON *selected = field(root, "selected_task");
    const cJSON *candidates = field(root, "candidates");
    if (!cJSON_IsString(schema) ||
        strcmp(schema->valuestring, DETAILS_SCHEMA) != 0 ||
        !cJSON_IsString(decision_id) || decision_id->valuestring[0] == '\0' ||
        !cJSON_IsNumber(generation) || generation->valuedouble < 1.0 ||
        floor(generation->valuedouble) != generation->valuedouble ||
        !cJSON_IsString(source_scan_id) ||
        source_scan_id->valuestring[0] == '\0' ||
        !cJSON_IsString(source_scene_sha256) ||
        strlen(source_scene_sha256->valuestring) != 64U ||
        !cJSON_IsString(entry_source) || entry_source->valuestring == NULL ||
        strcmp(canonical_entry_source(entry_source->valuestring),
               entry_source->valuestring) != 0 ||
        !cJSON_IsNumber(winner) || winner->valuedouble < 0.0 ||
        winner->valuedouble >= REHAB_TASK_DECISION_TASK_COUNT ||
        floor(winner->valuedouble) != winner->valuedouble ||
        !cJSON_IsObject(selected) ||
        !cJSON_IsString(field(selected, "task_id")) ||
        !cJSON_IsString(field(selected, "task_name")) ||
        !cJSON_IsNumber(field(selected, "score")) ||
        !cJSON_IsString(field(selected, "reason")) ||
        !cJSON_IsString(field(selected, "availability")) ||
        !cJSON_IsArray(candidates) ||
        !cJSON_IsObject(field(root, "patient_condition")) ||
        !cJSON_IsObject(field(root, "patient_ability")) ||
        !cJSON_IsObject(field(root, "advice_basis")) ||
        !cJSON_IsObject(field(root, "recent_performance")) ||
        !cJSON_IsObject(field(root, "item_frequency")) ||
        !cJSON_IsObject(field(root, "latest_compensation")) ||
        !cJSON_IsObject(field(root, "scoring_policy")) ||
        !cJSON_IsObject(field(root, "winner")) ||
        !cJSON_IsObject(field(root, "target_details")) ||
        !cJSON_IsString(field(root, "deepseek_summary")) ||
        cJSON_GetArraySize(candidates) != REHAB_TASK_DECISION_TASK_COUNT) {
        return false;
    }
    for (size_t ref = 0; ref < REHAB_TASK_DECISION_TASK_COUNT; ++ref) {
        const cJSON *candidate = cJSON_GetArrayItem(candidates, (int)ref);
        const cJSON *candidate_ref = field(candidate, "candidate_ref");
        if (!cJSON_IsObject(candidate) || !cJSON_IsNumber(candidate_ref) ||
            candidate_ref->valuedouble != (double)ref ||
            !cJSON_IsString(field(candidate, "task_name")) ||
            !cJSON_IsString(field(candidate, "availability")) ||
            !cJSON_IsString(field(candidate, "blocked_reason")) ||
            !cJSON_IsNumber(field(candidate, "score")) ||
            !cJSON_IsString(field(candidate, "reason")) ||
            field(candidate, "score_breakdown") == NULL ||
            !cJSON_IsObject(field(candidate, "target_summary"))) {
            return false;
        }
    }
    const cJSON *winner_candidate = cJSON_GetArrayItem(
        candidates, (int)winner->valuedouble);
    return strcmp(field(winner_candidate, "task_name")->valuestring,
                  field(selected, "task_name")->valuestring) == 0 &&
        strcmp(field(winner_candidate, "reason")->valuestring,
               field(selected, "reason")->valuestring) == 0 &&
        fabs(field(winner_candidate, "score")->valuedouble -
             field(selected, "score")->valuedouble) < 0.000001;
}

esp_err_t rehab_task_decision_details_render(
    rehab_task_decision_handle_t handle,
    const char *expected_decision_id,
    char *output,
    size_t output_size)
{
    if (handle == NULL || output == NULL || output_size == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    output[0] = '\0';
    if (!details_lock(handle)) return ESP_ERR_TIMEOUT;
    char directory[DETAILS_PATH_MAX] = {0};
    char path[DETAILS_PATH_MAX] = {0};
    char next_path[DETAILS_PATH_MAX] = {0};
    char previous_path[DETAILS_PATH_MAX] = {0};
    esp_err_t err = details_paths(directory, path, next_path, previous_path);
    struct stat info = {0};
    if (err == ESP_OK && stat(path, &info) != 0) {
        err = errno == ENOENT ? ESP_ERR_NOT_FOUND : ESP_FAIL;
    }
    if (err == ESP_OK && (!S_ISREG(info.st_mode) || info.st_size <= 0 ||
                          (uint64_t)info.st_size >=
                              REHAB_TASK_DECISION_DETAILS_JSON_MAX)) {
        err = ESP_ERR_INVALID_SIZE;
    }
    char *bytes = err == ESP_OK
        ? calloc(1U, (size_t)info.st_size + 1U) : NULL;
    if (err == ESP_OK && bytes == NULL) err = ESP_ERR_NO_MEM;
    FILE *file = err == ESP_OK ? fopen(path, "rb") : NULL;
    if (err == ESP_OK && file == NULL) err = ESP_FAIL;
    if (err == ESP_OK && fread(bytes, 1, (size_t)info.st_size, file) !=
                            (size_t)info.st_size) {
        err = ESP_FAIL;
    }
    if (file != NULL) fclose(file);
    details_unlock(handle);
    cJSON *root = err == ESP_OK
        ? cJSON_ParseWithOpts(bytes, NULL, true) : NULL;
    free(bytes);
    if (err == ESP_OK && !details_valid(root)) {
        err = ESP_ERR_INVALID_RESPONSE;
    }
    rehab_task_decision_selected_snapshot_t selected = {0};
    if (err == ESP_OK) {
        esp_err_t selected_err = rehab_task_decision_selected_get(
            handle, &selected);
        rehab_task_details_authority_t authority = {
            .decision_id = selected.decision_id,
            .generation = selected.decision_generation,
            .source_scan_id = selected.source_scan_id,
            .source_scene_sha256 = selected.source_scene_sha256,
            .task_id = selected.task_id,
            .task_name = selected.task_name,
            .reason = selected.reason,
            .score = selected.score,
            .target_available = selected.target_available,
            .target_object_id = selected.target_object_id,
        };
        if (selected_err != ESP_OK || !selected.available ||
            !rehab_task_decision_details_binding_matches(root, &authority)) {
            err = selected_err != ESP_OK
                ? selected_err : ESP_ERR_INVALID_STATE;
        }
    }
    const cJSON *decision_id = field(root, "decision_id");
    if (err == ESP_OK && expected_decision_id != NULL &&
        expected_decision_id[0] != '\0' &&
        strcmp(decision_id->valuestring, expected_decision_id) != 0) {
        err = ESP_ERR_INVALID_STATE;
    }
    char *rendered = err == ESP_OK ? cJSON_PrintUnformatted(root) : NULL;
    if (err == ESP_OK && rendered == NULL) err = ESP_ERR_NO_MEM;
    if (err == ESP_OK && strlen(rendered) >= output_size) {
        err = ESP_ERR_INVALID_SIZE;
    }
    if (err == ESP_OK) strlcpy(output, rendered, output_size);
    cJSON_free(rendered);
    cJSON_Delete(root);
    return err;
}
