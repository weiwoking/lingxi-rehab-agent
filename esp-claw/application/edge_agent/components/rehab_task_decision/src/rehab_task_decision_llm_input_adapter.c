// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_llm_input_adapter.h"

#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "rehab_task_decision_preparation.h"
#include "rehab_task_decision_priv.h"

#define FORMAL_INPUT_SCHEMA_VERSION "rehab_task_decision_input.v4"

static const cJSON *required_member(const cJSON *object, const char *name)
{
    return cJSON_IsObject(object)
        ? cJSON_GetObjectItemCaseSensitive(object, name) : NULL;
}

static bool add_copy(cJSON *destination, const char *destination_name,
                     const cJSON *source, const char *source_name)
{
    const cJSON *item = required_member(source, source_name);
    cJSON *copy = item != NULL ? cJSON_Duplicate(item, true) : NULL;
    if (copy == NULL ||
        !cJSON_AddItemToObject(destination, destination_name, copy)) {
        cJSON_Delete(copy);
        return false;
    }
    return true;
}

static cJSON *add_object(cJSON *parent, const char *name)
{
    cJSON *object = cJSON_CreateObject();
    if (object == NULL || !cJSON_AddItemToObject(parent, name, object)) {
        cJSON_Delete(object);
        return NULL;
    }
    return object;
}

static cJSON *add_array(cJSON *parent, const char *name)
{
    cJSON *array = cJSON_CreateArray();
    if (array == NULL || !cJSON_AddItemToObject(parent, name, array)) {
        cJSON_Delete(array);
        return NULL;
    }
    return array;
}

static bool add_binding(cJSON *compact, const cJSON *formal)
{
    const cJSON *source = required_member(formal, "decision_binding");
    cJSON *binding = add_object(compact, "decision_binding");
    return cJSON_IsObject(source) && binding != NULL &&
        add_copy(binding, "decision_id", source, "decision_id") &&
        add_copy(binding, "generation", source, "generation") &&
        add_copy(binding, "scan_id", source, "source_scan_id") &&
        add_copy(binding, "scene_sha256", source,
                 "source_scene_sha256");
}

static bool add_authority(cJSON *compact)
{
    cJSON *authority = add_object(compact, "authority");
    return authority != NULL &&
        cJSON_AddStringToObject(authority, "machine_facts",
                               FORMAL_INPUT_SCHEMA_VERSION) != NULL &&
        cJSON_AddStringToObject(authority, "business_scoring",
                               "DeepSeek") != NULL &&
        cJSON_AddStringToObject(authority, "deterministic_preparation",
            REHAB_TASK_DECISION_PREPARATION_SCHEMA_VERSION) != NULL &&
        cJSON_AddStringToObject(authority, "output_contract",
                               REHAB_TASK_DECISION_OUTPUT_SCHEMA_VERSION) !=
            NULL &&
        cJSON_AddStringToObject(authority, "validation_input",
                               FORMAL_INPUT_SCHEMA_VERSION) != NULL;
}

static bool add_patient(cJSON *compact, const cJSON *formal)
{
    const cJSON *source_ability = required_member(formal, "patient_ability");
    const cJSON *source_condition = required_member(
        formal, "patient_condition");
    const cJSON *raw_text = required_member(source_condition, "raw_text");
    cJSON *patient = add_object(compact, "patient");
    cJSON *ability = patient != NULL ? add_object(patient, "ability") : NULL;
    cJSON *condition = patient != NULL
        ? add_object(patient, "condition") : NULL;
    cJSON *advice = patient != NULL
        ? add_object(patient, "medical_advice") : NULL;
    return cJSON_IsObject(source_ability) &&
        cJSON_IsObject(source_condition) && cJSON_IsString(raw_text) &&
        raw_text->valuestring != NULL && raw_text->valuestring[0] != '\0' &&
        ability != NULL && condition != NULL && advice != NULL &&
        add_copy(ability, "level", source_ability, "level") &&
        add_copy(ability, "parse_status", source_ability, "parse_status") &&
        add_copy(condition, "raw_text", source_condition, "raw_text") &&
        add_copy(condition, "status", source_condition, "status") &&
        add_copy(condition, "truncated", source_condition, "truncated") &&
        cJSON_AddStringToObject(advice, "source",
                               "patient.condition.raw_text") != NULL &&
        cJSON_AddStringToObject(
            advice, "interpretation",
            "candidate_scoped_free_text_semantics") != NULL;
}

static bool add_history(cJSON *compact, const cJSON *formal)
{
    const cJSON *recent = required_member(formal, "recent_performance");
    const cJSON *frequency = required_member(formal, "item_frequency");
    const cJSON *records = required_member(recent, "records");
    if (!cJSON_IsObject(recent) || !cJSON_IsObject(frequency) ||
        !cJSON_IsArray(records)) {
        return false;
    }

    cJSON *history = add_object(compact, "history");
    cJSON *summary = history != NULL
        ? add_object(history, "score_history_summary") : NULL;
    cJSON *scores = summary != NULL ? add_array(summary, "scores") : NULL;
    cJSON *counts = history != NULL
        ? add_object(history, "frequency_counts") : NULL;
    if (history == NULL || summary == NULL || scores == NULL ||
        counts == NULL ||
        !add_copy(history, "recent_tendency", recent, "tendency_level") ||
        !add_copy(summary, "status", recent, "status") ||
        !add_copy(summary, "window_limit", recent, "window_limit") ||
        !add_copy(summary, "record_count", recent, "record_count")) {
        return false;
    }

    double score_total = 0.0;
    size_t score_count = 0U;
    const cJSON *record = NULL;
    cJSON_ArrayForEach(record, records) {
        const cJSON *score = required_member(record, "average_score");
        if (!cJSON_IsNumber(score) || !isfinite(score->valuedouble) ||
            !cJSON_AddItemToArray(scores,
                                 cJSON_CreateNumber(score->valuedouble))) {
            return false;
        }
        score_total += score->valuedouble;
        score_count++;
    }
    if (score_count > 0U) {
        if (cJSON_AddNumberToObject(summary, "average_score",
                                    score_total / (double)score_count) ==
            NULL) {
            return false;
        }
    } else if (!cJSON_AddNullToObject(summary, "average_score")) {
        return false;
    }

    return add_copy(counts, "status", frequency, "status") &&
        add_copy(counts, "window_limit", frequency, "window_limit") &&
        add_copy(counts, "mapped_record_count", frequency,
                 "mapped_record_count") &&
        add_copy(counts, "cup", frequency, "cup_count") &&
        add_copy(counts, "ball", frequency, "ball_count");
}

static cJSON *build_target_projection(const cJSON *facts)
{
    const cJSON *target_id = required_member(facts, "target_object_id");
    if (cJSON_IsNull(target_id)) {
        return NULL;
    }
    if (!cJSON_IsString(target_id) || target_id->valuestring == NULL ||
        target_id->valuestring[0] == '\0') {
        return NULL;
    }

    cJSON *target = cJSON_CreateObject();
    cJSON *height = target != NULL ? add_object(target, "height") : NULL;
    cJSON *geometry = target != NULL ? add_object(target, "geometry") : NULL;
    cJSON *perception = target != NULL
        ? add_object(target, "perception") : NULL;
    cJSON *visibility = target != NULL
        ? add_object(target, "visibility") : NULL;
    if (target == NULL || height == NULL || geometry == NULL ||
        perception == NULL || visibility == NULL ||
        !cJSON_AddBoolToObject(target, "exists", true) ||
        !add_copy(target, "target_object_id", facts, "target_object_id") ||
        !add_copy(target, "display_group_name", facts,
                  "display_group_name") ||
        !add_copy(target, "raw_class", facts, "raw_class") ||
        !add_copy(target, "plane", facts, "plane") ||
        !add_copy(height, "status", facts, "height_status") ||
        !add_copy(height, "support_height_mm", facts,
                  "support_height_mm") ||
        !add_copy(height, "confidence", facts, "height_confidence") ||
        !add_copy(geometry, "valid", facts, "geometry_valid") ||
        !add_copy(geometry, "valid_known", facts,
                  "geometry_valid_known") ||
        !add_copy(geometry, "confidence", facts,
                  "geometry_confidence") ||
        !add_copy(geometry, "confidence_status", facts,
                  "geometry_confidence_status") ||
        !add_copy(perception, "mode", facts, "perception_mode") ||
        !add_copy(perception, "detection_confidence", facts,
                  "detection_confidence") ||
        !add_copy(perception, "detection_confidence_status", facts,
                  "detection_confidence_status") ||
        !add_copy(perception, "quality", facts, "perception_quality") ||
        !add_copy(visibility, "full_extent_visible", facts,
                  "full_extent_visible") ||
        !add_copy(visibility, "touches_border", facts, "touches_border")) {
        cJSON_Delete(target);
        return NULL;
    }
    return target;
}

static bool add_hard_gate(cJSON *candidate, const cJSON *source)
{
    cJSON *gate = add_object(candidate, "hard_gate");
    return gate != NULL &&
        add_copy(gate, "ability_allowed", source, "ability_allowed") &&
        add_copy(gate, "group_object_count", source,
                 "group_object_count") &&
        add_copy(gate, "target_exists", source, "feasible_target") &&
        add_copy(gate, "geometry_height_valid", source,
                 "geometry_height_valid") &&
        add_copy(gate, "blocked", source, "blocked") &&
        add_copy(gate, "blocked_reason", source, "blocked_reason") &&
        add_copy(gate, "score_must_be_zero", source,
                 "score_must_be_zero");
}

static bool add_candidates_and_targets(cJSON *compact, const cJSON *formal)
{
    const cJSON *source_candidates = required_member(formal, "candidates");
    if (!cJSON_IsArray(source_candidates) ||
        cJSON_GetArraySize(source_candidates) !=
            REHAB_TASK_DECISION_TASK_COUNT) {
        return false;
    }

    cJSON *candidates = add_array(compact, "candidates");
    cJSON *targets = add_array(compact, "targets");
    char *target_keys[REHAB_TASK_DECISION_TASK_COUNT] = {0};
    size_t target_count = 0U;
    bool valid = candidates != NULL && targets != NULL;

    for (size_t index = 0U;
         valid && index < REHAB_TASK_DECISION_TASK_COUNT; ++index) {
        const cJSON *source = cJSON_GetArrayItem(source_candidates, index);
        const cJSON *candidate_ref = required_member(source, "candidate_ref");
        const cJSON *gates = required_member(source, "hard_gates");
        const cJSON *facts = required_member(source, "target_facts");
        if (!cJSON_IsObject(source) || !cJSON_IsNumber(candidate_ref) ||
            candidate_ref->valueint != (int)index || !cJSON_IsObject(gates) ||
            !cJSON_IsObject(facts)) {
            valid = false;
            break;
        }

        cJSON *candidate = cJSON_CreateObject();
        if (candidate == NULL ||
            !cJSON_AddItemToArray(candidates, candidate)) {
            cJSON_Delete(candidate);
            valid = false;
            break;
        }
        valid = add_copy(candidate, "candidate_ref", source,
                         "candidate_ref") &&
            add_copy(candidate, "task_id", source, "task_id") &&
            add_copy(candidate, "task_name", source, "task_name") &&
            add_copy(candidate, "level", source, "level") &&
            add_copy(candidate, "semantic_group", source,
                     "semantic_group") &&
            add_copy(candidate, "action_family", source,
                     "action_family") &&
            add_copy(candidate, "required_plane", source,
                     "required_plane") &&
            add_copy(candidate, "availability", source, "availability") &&
            add_hard_gate(candidate, gates);
        if (!valid) {
            break;
        }

        const cJSON *target_id = required_member(facts, "target_object_id");
        if (cJSON_IsNull(target_id)) {
            valid = cJSON_AddNullToObject(candidate, "target_reference") !=
                NULL;
            continue;
        }

        cJSON *target = build_target_projection(facts);
        char *key = target != NULL ? cJSON_PrintUnformatted(target) : NULL;
        if (target == NULL || key == NULL) {
            cJSON_Delete(target);
            cJSON_free(key);
            valid = false;
            break;
        }
        size_t target_index = 0U;
        while (target_index < target_count &&
               strcmp(key, target_keys[target_index]) != 0) {
            target_index++;
        }
        char target_ref[24] = {0};
        if (snprintf(target_ref, sizeof(target_ref), "target-%u",
                     (unsigned)target_index) < 0) {
            cJSON_Delete(target);
            cJSON_free(key);
            valid = false;
            break;
        }
        if (target_index == target_count) {
            target_keys[target_count++] = key;
            key = NULL;
            if (cJSON_AddStringToObject(target, "target_ref",
                                       target_ref) == NULL) {
                cJSON_Delete(target);
                valid = false;
                break;
            }
            if (!cJSON_AddItemToArray(targets, target)) {
                cJSON_Delete(target);
                valid = false;
                break;
            }
            target = NULL;
        }
        cJSON_Delete(target);
        cJSON_free(key);
        valid = cJSON_AddStringToObject(candidate, "target_reference",
                                        target_ref) != NULL;
    }

    for (size_t index = 0U; index < target_count; ++index) {
        cJSON_free(target_keys[index]);
    }
    return valid;
}

static bool add_compensation(cJSON *compact, const cJSON *formal)
{
    const cJSON *source = required_member(formal, "latest_compensation");
    cJSON *compensation = add_object(compact, "latest_compensation");
    return cJSON_IsObject(source) && compensation != NULL &&
        add_copy(compensation, "status", source, "status") &&
        add_copy(compensation, "latest_task", source, "task_name") &&
        add_copy(compensation, "compensation_action_count", source,
                 "compensation_action_count") &&
        add_copy(compensation, "exact_task_id", source, "exact_task_id") &&
        add_copy(compensation, "action_family", source, "action_family") &&
        add_copy(compensation, "mapped_group", source, "mapped_group") &&
        add_copy(compensation, "severity", source, "severity");
}

esp_err_t rehab_task_decision_llm_input_compact(
    const char *formal_input,
    char **out_compact)
{
    if (formal_input == NULL || out_compact == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_compact = NULL;

    cJSON *formal = cJSON_ParseWithOpts(formal_input, NULL, true);
    const cJSON *schema = required_member(formal, "schema_version");
    if (!cJSON_IsObject(formal) || !cJSON_IsString(schema) ||
        schema->valuestring == NULL ||
        strcmp(schema->valuestring, FORMAL_INPUT_SCHEMA_VERSION) != 0) {
        cJSON_Delete(formal);
        return ESP_ERR_INVALID_ARG;
    }

    cJSON *compact = cJSON_CreateObject();
    bool valid = compact != NULL &&
        cJSON_AddStringToObject(
            compact, "schema_version",
            REHAB_TASK_DECISION_LLM_INPUT_SCHEMA_VERSION) != NULL &&
        cJSON_AddStringToObject(compact, "source_schema_version",
                               FORMAL_INPUT_SCHEMA_VERSION) != NULL &&
        add_binding(compact, formal) && add_authority(compact) &&
        add_patient(compact, formal) && add_history(compact, formal) &&
        add_candidates_and_targets(compact, formal) &&
        add_compensation(compact, formal) &&
        rehab_task_decision_preparation_add(compact, formal) == ESP_OK;
    cJSON_Delete(formal);
    if (!valid) {
        cJSON_Delete(compact);
        return ESP_ERR_INVALID_STATE;
    }

    char *json = cJSON_PrintUnformatted(compact);
    cJSON_Delete(compact);
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }
    if (strlen(json) >= REHAB_TASK_DECISION_LLM_INPUT_MAX) {
        cJSON_free(json);
        return ESP_ERR_INVALID_SIZE;
    }
    *out_compact = json;
    return ESP_OK;
}
