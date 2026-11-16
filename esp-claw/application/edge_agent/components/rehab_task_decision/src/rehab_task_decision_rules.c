// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "claw_paths.h"
#include "esp_log.h"
#include "mbedtls/sha256.h"

#define RULES_SCHEMA "rehab.task_decision.rules.v1"
#define FORMAL_RULES_SCHEMA "rehab.task_decision.formal_rules.v1"
#define RULES_RESPONSE_SCHEMA "rehab.task_decision.rules_response.v1"
#define RULES_SUBPATH "skills/rehab-task-decision/decision_rules.json"
#define RULES_PATH_MAX 192U
#define RULES_MAX_BYTES (16U * 1024U)

static const char *TAG = "rehab_decision_rules";

typedef struct {
    const char *id;
    const char *name;
    const char *difficulty;
} rule_task_definition_t;

static const rule_task_definition_t s_tasks[REHAB_TASK_DECISION_TASK_COUNT] = {
    {"OT_CUP_TRANSFER_L1", "水杯横向取放训练（一级）", "easy"},
    {"OT_CUP_DRINK_L2", "水杯拿取与饮水训练（二级）", "medium"},
    {"OT_CUP_DRINK_L3", "水杯拿取与饮水训练（三级）", "hard"},
    {"OT_BALL_TRANSFER_L1", "球类横向取放训练（一级）", "easy"},
    {"OT_BALL_TRANSFER_L2", "球类横向取放训练（二级）", "medium"},
};

size_t rehab_task_decision_rules_task_count(void)
{
    return REHAB_TASK_DECISION_TASK_COUNT;
}

const char *rehab_task_decision_rules_task_id_at(size_t index)
{
    return index < REHAB_TASK_DECISION_TASK_COUNT
        ? s_tasks[index].id : NULL;
}

static const char *s_top_fields[] = {
    "schema_version", "decision_rules_version", "default_distance_cm",
    "recent_score_window", "confidence_bonus", "repeat_observation_bonus",
    "recent_score_adjustments", "recent_same_task_penalty",
    "prescription_rules", "tasks",
};

static const char *s_task_fields[] = {
    "task_id", "task_name", "difficulty", "hard_gate",
    "base_score_if_eligible", "context_any", "context_bonus", "table_bonus",
    "multiple_movable_classes_bonus", "virtual_target_zone_bonus",
    "valid_distance_bonus",
};

static const char *s_task_required[] = {
    "task_id", "task_name", "difficulty", "hard_gate",
    "base_score_if_eligible", "valid_distance_bonus",
};

static bool rules_lock(rehab_task_decision_handle_t handle)
{
    return xSemaphoreTake(handle->lock, pdMS_TO_TICKS(1000)) == pdTRUE;
}

static void rules_unlock(rehab_task_decision_handle_t handle)
{
    xSemaphoreGive(handle->lock);
}

static bool string_in(const char *value, const char *const *allowed,
                      size_t allowed_count)
{
    if (value == NULL) {
        return false;
    }
    for (size_t i = 0; i < allowed_count; ++i) {
        if (strcmp(value, allowed[i]) == 0) {
            return true;
        }
    }
    return false;
}

static bool array_string_equals(const cJSON *array, int index,
                                const char *expected)
{
    const cJSON *item = cJSON_IsArray(array)
        ? cJSON_GetArrayItem(array, index) : NULL;
    return cJSON_IsString(item) && item->valuestring != NULL &&
        strcmp(item->valuestring, expected) == 0;
}

static int field_index(const char *name, const char *const *fields,
                       size_t field_count)
{
    if (name == NULL) {
        return -1;
    }
    for (size_t i = 0; i < field_count; ++i) {
        if (strcmp(name, fields[i]) == 0) {
            return (int)i;
        }
    }
    return -1;
}

static bool object_fields(const cJSON *object,
                          const char *const *allowed,
                          size_t allowed_count,
                          const char *const *required,
                          size_t required_count)
{
    if (!cJSON_IsObject(object) || allowed_count > 31U) {
        return false;
    }
    for (size_t i = 0; i < required_count; ++i) {
        if (cJSON_GetObjectItemCaseSensitive(object, required[i]) == NULL) {
            return false;
        }
    }
    uint32_t seen = 0;
    const cJSON *child = NULL;
    cJSON_ArrayForEach(child, object) {
        int index = field_index(child->string, allowed, allowed_count);
        if (index < 0 || (seen & (UINT32_C(1) << index)) != 0) {
            return false;
        }
        seen |= UINT32_C(1) << index;
    }
    return true;
}

static bool exact_fields(const cJSON *object, const char *const *fields,
                         size_t field_count)
{
    return object_fields(object, fields, field_count, fields, field_count) &&
           cJSON_GetArraySize(object) == (int)field_count;
}

static bool json_integer(const cJSON *item, int minimum, int maximum,
                         int *out_value)
{
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
        floor(item->valuedouble) != item->valuedouble ||
        item->valuedouble < minimum || item->valuedouble > maximum) {
        return false;
    }
    if (out_value != NULL) {
        *out_value = (int)item->valuedouble;
    }
    return true;
}

static bool bounded_points(const cJSON *object, const char *field)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, field);
    return item == NULL || json_integer(item, -100, 100, NULL);
}

static bool string_array(const cJSON *array, const char *const *allowed,
                         size_t allowed_count, bool allow_empty)
{
    if (!cJSON_IsArray(array)) {
        return false;
    }
    int count = cJSON_GetArraySize(array);
    if ((!allow_empty && count == 0) || count > (int)allowed_count) {
        return false;
    }
    for (int i = 0; i < count; ++i) {
        const cJSON *item = cJSON_GetArrayItem(array, i);
        if (!cJSON_IsString(item) || item->valuestring == NULL ||
            !string_in(item->valuestring, allowed, allowed_count)) {
            return false;
        }
        for (int j = 0; j < i; ++j) {
            const cJSON *previous = cJSON_GetArrayItem(array, j);
            if (strcmp(previous->valuestring, item->valuestring) == 0) {
                return false;
            }
        }
    }
    return true;
}

static bool difficulty_points(const cJSON *object)
{
    static const char *fields[] = {"easy", "medium", "hard"};
    if (!exact_fields(object, fields, 3U)) {
        return false;
    }
    for (size_t i = 0; i < 3U; ++i) {
        if (!json_integer(cJSON_GetObjectItemCaseSensitive(object, fields[i]),
                          -100, 100, NULL)) {
            return false;
        }
    }
    return true;
}

static const rule_task_definition_t *task_definition(const char *task_id)
{
    if (task_id == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < REHAB_TASK_DECISION_TASK_COUNT; ++i) {
        if (strcmp(task_id, s_tasks[i].id) == 0) {
            return &s_tasks[i];
        }
    }
    return NULL;
}

static bool validate_task(const cJSON *task, uint32_t *seen_mask)
{
    static const char *required_classes[] = {
        "cup", "bottle", "toothbrush", "comb", "book",
    };
    static const char *context_classes[] = {"table", "washbasin"};
    static const char *hard_gate_fields[] = {"required_any", "on_missing"};
    if (!object_fields(task, s_task_fields,
                       sizeof(s_task_fields) / sizeof(s_task_fields[0]),
                       s_task_required,
                       sizeof(s_task_required) / sizeof(s_task_required[0]))) {
        return false;
    }
    const cJSON *task_id = cJSON_GetObjectItemCaseSensitive(task, "task_id");
    const cJSON *task_name = cJSON_GetObjectItemCaseSensitive(task,
                                                              "task_name");
    const cJSON *difficulty = cJSON_GetObjectItemCaseSensitive(task,
                                                               "difficulty");
    const rule_task_definition_t *definition =
        cJSON_IsString(task_id) && task_id->valuestring != NULL
            ? task_definition(task_id->valuestring) : NULL;
    if (definition == NULL || !cJSON_IsString(task_name) ||
        task_name->valuestring == NULL ||
        strcmp(task_name->valuestring, definition->name) != 0 ||
        !cJSON_IsString(difficulty) || difficulty->valuestring == NULL ||
        strcmp(difficulty->valuestring, definition->difficulty) != 0) {
        return false;
    }
    size_t index = (size_t)(definition - s_tasks);
    if ((*seen_mask & (UINT32_C(1) << index)) != 0) {
        return false;
    }
    const cJSON *context = cJSON_GetObjectItemCaseSensitive(task,
                                                            "context_any");
    const cJSON *context_bonus = cJSON_GetObjectItemCaseSensitive(
        task, "context_bonus");
    const cJSON *hard_gate = cJSON_GetObjectItemCaseSensitive(task,
                                                              "hard_gate");
    const cJSON *on_missing = cJSON_GetObjectItemCaseSensitive(
        hard_gate, "on_missing");
    if (!exact_fields(hard_gate, hard_gate_fields, 2U) ||
        !string_array(cJSON_GetObjectItemCaseSensitive(
                          hard_gate, "required_any"),
                      required_classes,
                      sizeof(required_classes) / sizeof(required_classes[0]),
                      false) ||
        !cJSON_IsString(on_missing) || on_missing->valuestring == NULL ||
        strcmp(on_missing->valuestring, "force_zero") != 0 ||
        !json_integer(cJSON_GetObjectItemCaseSensitive(
                          task, "base_score_if_eligible"),
                      0, 100, NULL) ||
        !json_integer(cJSON_GetObjectItemCaseSensitive(
                          task, "valid_distance_bonus"),
                      -100, 100, NULL) ||
        ((context == NULL) != (context_bonus == NULL)) ||
        (context != NULL &&
         (!string_array(context, context_classes,
                        sizeof(context_classes) / sizeof(context_classes[0]),
                        false) ||
          !json_integer(context_bonus, -100, 100, NULL))) ||
        !bounded_points(task, "table_bonus") ||
        !bounded_points(task, "multiple_movable_classes_bonus") ||
        !bounded_points(task, "virtual_target_zone_bonus")) {
        return false;
    }
    *seen_mask |= UINT32_C(1) << index;
    return true;
}

static bool __attribute__((unused)) validate_legacy_rules_json(
    const char *json)
{
    if (json == NULL ||
        !rehab_task_decision_text_valid(json, RULES_MAX_BYTES,
                                        RULES_MAX_BYTES, false)) {
        return false;
    }
    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    if (!exact_fields(root, s_top_fields,
                      sizeof(s_top_fields) / sizeof(s_top_fields[0]))) {
        cJSON_Delete(root);
        return false;
    }
    const cJSON *schema = cJSON_GetObjectItemCaseSensitive(root,
                                                           "schema_version");
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(
        root, "decision_rules_version");
    if (!cJSON_IsString(schema) || schema->valuestring == NULL ||
        strcmp(schema->valuestring, RULES_SCHEMA) != 0 ||
        !cJSON_IsString(version) || version->valuestring == NULL ||
        strcmp(version->valuestring, REHAB_TASK_DECISION_RULES_VERSION) != 0 ||
        !json_integer(cJSON_GetObjectItemCaseSensitive(
                          root, "default_distance_cm"),
                      1, 300, NULL) ||
        !json_integer(cJSON_GetObjectItemCaseSensitive(
                          root, "recent_score_window"),
                      1, 10, NULL) ||
        !json_integer(cJSON_GetObjectItemCaseSensitive(
                          root, "recent_same_task_penalty"),
                      -100, 100, NULL)) {
        cJSON_Delete(root);
        return false;
    }

    static const char *bonus_fields[] = {"min", "points"};
    const cJSON *confidence = cJSON_GetObjectItemCaseSensitive(
        root, "confidence_bonus");
    if (!cJSON_IsArray(confidence) || cJSON_GetArraySize(confidence) != 2) {
        cJSON_Delete(root);
        return false;
    }
    double previous_min = 2.0;
    for (int i = 0; i < 2; ++i) {
        const cJSON *entry = cJSON_GetArrayItem(confidence, i);
        const cJSON *minimum = cJSON_GetObjectItemCaseSensitive(entry, "min");
        if (!exact_fields(entry, bonus_fields, 2U) ||
            !cJSON_IsNumber(minimum) || !isfinite(minimum->valuedouble) ||
            minimum->valuedouble < 0.0 || minimum->valuedouble > 1.0 ||
            minimum->valuedouble >= previous_min ||
            !json_integer(cJSON_GetObjectItemCaseSensitive(entry, "points"),
                          -100, 100, NULL)) {
            cJSON_Delete(root);
            return false;
        }
        previous_min = minimum->valuedouble;
    }

    static const char *repeat_fields[] = {"min_seen_count", "points"};
    const cJSON *repeat = cJSON_GetObjectItemCaseSensitive(
        root, "repeat_observation_bonus");
    if (!exact_fields(repeat, repeat_fields, 2U) ||
        !json_integer(cJSON_GetObjectItemCaseSensitive(
                          repeat, "min_seen_count"),
                      1, 100, NULL) ||
        !json_integer(cJSON_GetObjectItemCaseSensitive(repeat, "points"),
                      -100, 100, NULL)) {
        cJSON_Delete(root);
        return false;
    }

    static const char *recent_fields[] = {
        "below_60", "from_60_to_79", "at_least_80", "no_history",
    };
    const cJSON *recent = cJSON_GetObjectItemCaseSensitive(
        root, "recent_score_adjustments");
    if (!exact_fields(recent, recent_fields, 4U)) {
        cJSON_Delete(root);
        return false;
    }
    for (size_t i = 0; i < 4U; ++i) {
        if (!difficulty_points(cJSON_GetObjectItemCaseSensitive(
                recent, recent_fields[i]))) {
            cJSON_Delete(root);
            return false;
        }
    }

    static const char *prescription_fields[] = {
        "explicitly_forbidden", "recommended", "strongly_prioritized",
        "beginner", "increase_difficulty", "avoid_overhead_reach",
        "unmentioned",
    };
    const cJSON *prescription = cJSON_GetObjectItemCaseSensitive(
        root, "prescription_rules");
    const cJSON *forbidden = cJSON_GetObjectItemCaseSensitive(
        prescription, "explicitly_forbidden");
    static const char *avoid_fields[] = {"force_zero_task_ids"};
    const cJSON *avoid = cJSON_GetObjectItemCaseSensitive(
        prescription, "avoid_overhead_reach");
    static const char *comb_task[] = {"OT_CUP_DRINK_L3"};
    if (!exact_fields(prescription, prescription_fields, 7U) ||
        !cJSON_IsString(forbidden) || forbidden->valuestring == NULL ||
        strcmp(forbidden->valuestring, "force_zero") != 0 ||
        !json_integer(cJSON_GetObjectItemCaseSensitive(
                          prescription, "recommended"),
                      -100, 100, NULL) ||
        !json_integer(cJSON_GetObjectItemCaseSensitive(
                          prescription, "strongly_prioritized"),
                      -100, 100, NULL) ||
        !difficulty_points(cJSON_GetObjectItemCaseSensitive(
            prescription, "beginner")) ||
        !difficulty_points(cJSON_GetObjectItemCaseSensitive(
            prescription, "increase_difficulty")) ||
        !exact_fields(avoid, avoid_fields, 1U) ||
        !string_array(cJSON_GetObjectItemCaseSensitive(
                          avoid, "force_zero_task_ids"),
                      comb_task, 1U, false) ||
        !json_integer(cJSON_GetObjectItemCaseSensitive(
                          prescription, "unmentioned"),
                      -100, 100, NULL)) {
        cJSON_Delete(root);
        return false;
    }

    const cJSON *tasks = cJSON_GetObjectItemCaseSensitive(root, "tasks");
    if (!cJSON_IsArray(tasks) ||
        cJSON_GetArraySize(tasks) != (int)REHAB_TASK_DECISION_TASK_COUNT) {
        cJSON_Delete(root);
        return false;
    }
    uint32_t seen_mask = 0;
    for (size_t i = 0; i < REHAB_TASK_DECISION_TASK_COUNT; ++i) {
        if (!validate_task(cJSON_GetArrayItem(tasks, (int)i), &seen_mask)) {
            cJSON_Delete(root);
            return false;
        }
    }
    bool valid = seen_mask ==
        ((UINT32_C(1) << REHAB_TASK_DECISION_TASK_COUNT) - 1U);
    cJSON_Delete(root);
    return valid;
}

static bool validate_formal_rules_json(const char *json)
{
    static const char *top_fields[] = {
        "schema_version", "decision_rules_version",
        "recent_score_window", "scoring_guidance", "height_policy",
        "semantic_groups", "tasks",
    };
    static const char *task_fields[] = {
        "task_id", "task_name", "difficulty", "level",
        "semantic_group", "action_family", "plane",
    };
    if (json == NULL ||
        !rehab_task_decision_text_valid(json, RULES_MAX_BYTES,
                                        RULES_MAX_BYTES, false)) {
        return false;
    }
    cJSON *root = cJSON_ParseWithOpts(json, NULL, true);
    if (!exact_fields(root, top_fields,
                      sizeof(top_fields) / sizeof(top_fields[0]))) {
        cJSON_Delete(root);
        return false;
    }
    const cJSON *schema = cJSON_GetObjectItemCaseSensitive(
        root, "schema_version");
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(
        root, "decision_rules_version");
    int recent_score_window = 0;
    const cJSON *tasks = cJSON_GetObjectItemCaseSensitive(root, "tasks");
    const cJSON *guidance = cJSON_GetObjectItemCaseSensitive(
        root, "scoring_guidance");
    const cJSON *height = cJSON_GetObjectItemCaseSensitive(
        root, "height_policy");
    const cJSON *groups = cJSON_GetObjectItemCaseSensitive(
        root, "semantic_groups");
    static const char *guidance_fields[] = {
        "difficulty_max", "difficulty_easier_step",
        "difficulty_harder_step", "advice_match", "advice_neutral",
        "advice_mismatch", "compensation_max",
        "compensation_severity_1", "compensation_severity_2",
        "compensation_severity_3_plus", "relatedness_exact_task",
        "relatedness_same_action_family", "relatedness_same_object_group",
        "frequency_0", "frequency_1", "frequency_2", "frequency_3",
        "perception_min", "perception_span",
        "perception_geometry_weight", "perception_detection_weight",
        "perception_height_weight", "fallback_detection_weight",
        "fallback_height_weight", "score_decimal_places",
        "validation_tolerance",
    };
    static const char *height_fields[] = {
        "low_min_mm", "low_max_mm_inclusive", "low_center_mm",
        "high_min_exclusive_mm", "high_center_mm", "confidence",
    };
    static const char *group_fields[] = {"cup", "ball"};
    const rehab_task_scoring_policy_t *scoring =
        rehab_task_scoring_policy();
#define POLICY_NUMBER(name, expected) \
    (cJSON_IsNumber(cJSON_GetObjectItemCaseSensitive(guidance, (name))) && \
     fabs(cJSON_GetObjectItemCaseSensitive(guidance, (name))->valuedouble - \
          (expected)) < 0.000001)
    bool valid =
        cJSON_IsString(schema) && schema->valuestring != NULL &&
        strcmp(schema->valuestring, FORMAL_RULES_SCHEMA) == 0 &&
        cJSON_IsString(version) && version->valuestring != NULL &&
        strcmp(version->valuestring,
               REHAB_TASK_DECISION_RULES_VERSION) == 0 &&
        json_integer(cJSON_GetObjectItemCaseSensitive(
                         root, "recent_score_window"),
                     1, 10, &recent_score_window) &&
        recent_score_window == 3 &&
        exact_fields(guidance, guidance_fields,
                     sizeof(guidance_fields) / sizeof(guidance_fields[0])) &&
        scoring != NULL &&
        POLICY_NUMBER("difficulty_max", scoring->difficulty_max) &&
        POLICY_NUMBER("difficulty_easier_step",
                      scoring->difficulty_easier_step) &&
        POLICY_NUMBER("difficulty_harder_step",
                      scoring->difficulty_harder_step) &&
        POLICY_NUMBER("advice_match", scoring->advice_match) &&
        POLICY_NUMBER("advice_neutral", scoring->advice_neutral) &&
        POLICY_NUMBER("advice_mismatch", scoring->advice_mismatch) &&
        POLICY_NUMBER("compensation_max", scoring->compensation_max) &&
        POLICY_NUMBER("compensation_severity_1",
                      scoring->compensation_severity_1) &&
        POLICY_NUMBER("compensation_severity_2",
                      scoring->compensation_severity_2) &&
        POLICY_NUMBER("compensation_severity_3_plus",
                      scoring->compensation_severity_3_plus) &&
        POLICY_NUMBER("relatedness_exact_task",
                      scoring->relatedness_exact_task) &&
        POLICY_NUMBER("relatedness_same_action_family",
                      scoring->relatedness_same_action_family) &&
        POLICY_NUMBER("relatedness_same_object_group",
                      scoring->relatedness_same_object_group) &&
        POLICY_NUMBER("frequency_0", scoring->frequency_0) &&
        POLICY_NUMBER("frequency_1", scoring->frequency_1) &&
        POLICY_NUMBER("frequency_2", scoring->frequency_2) &&
        POLICY_NUMBER("frequency_3", scoring->frequency_3) &&
        POLICY_NUMBER("perception_min", scoring->perception_min) &&
        POLICY_NUMBER("perception_span", scoring->perception_span) &&
        POLICY_NUMBER("perception_geometry_weight",
                      scoring->perception_geometry_weight) &&
        POLICY_NUMBER("perception_detection_weight",
                      scoring->perception_detection_weight) &&
        POLICY_NUMBER("perception_height_weight",
                      scoring->perception_height_weight) &&
        POLICY_NUMBER("fallback_detection_weight",
                      scoring->fallback_detection_weight) &&
        POLICY_NUMBER("fallback_height_weight",
                      scoring->fallback_height_weight) &&
        POLICY_NUMBER("score_decimal_places",
                      scoring->score_decimal_places) &&
        POLICY_NUMBER("validation_tolerance",
                      scoring->validation_tolerance) &&
        exact_fields(height, height_fields,
                     sizeof(height_fields) / sizeof(height_fields[0])) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(height, "low_min_mm"),
                     550, 550, NULL) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(height, "low_max_mm_inclusive"),
                     890, 890, NULL) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(height, "low_center_mm"),
                     710, 710, NULL) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(height, "high_min_exclusive_mm"),
                     890, 890, NULL) &&
        json_integer(cJSON_GetObjectItemCaseSensitive(height, "high_center_mm"),
                     1070, 1070, NULL) &&
        cJSON_IsString(cJSON_GetObjectItemCaseSensitive(height, "confidence")) &&
        strcmp(cJSON_GetObjectItemCaseSensitive(height, "confidence")->valuestring,
               "clamp(1-abs(support_height_mm-center_mm)/200,0,1)") == 0 &&
        exact_fields(groups, group_fields, 2U) &&
        cJSON_IsArray(cJSON_GetObjectItemCaseSensitive(groups, "cup")) &&
        cJSON_GetArraySize(cJSON_GetObjectItemCaseSensitive(groups, "cup")) == 2 &&
        cJSON_IsArray(cJSON_GetObjectItemCaseSensitive(groups, "ball")) &&
        cJSON_GetArraySize(cJSON_GetObjectItemCaseSensitive(groups, "ball")) == 3 &&
        array_string_equals(cJSON_GetObjectItemCaseSensitive(groups, "cup"), 0, "cup") &&
        array_string_equals(cJSON_GetObjectItemCaseSensitive(groups, "cup"), 1, "bottle") &&
        array_string_equals(cJSON_GetObjectItemCaseSensitive(groups, "ball"), 0, "sports ball") &&
        array_string_equals(cJSON_GetObjectItemCaseSensitive(groups, "ball"), 1, "ball") &&
        array_string_equals(cJSON_GetObjectItemCaseSensitive(groups, "ball"), 2, "volleyball") &&
        cJSON_IsArray(tasks) &&
        cJSON_GetArraySize(tasks) ==
            (int)REHAB_TASK_DECISION_TASK_COUNT;
    for (size_t i = 0; valid &&
         i < REHAB_TASK_DECISION_TASK_COUNT; ++i) {
        const cJSON *task = cJSON_GetArrayItem(tasks, (int)i);
        const cJSON *task_id = cJSON_GetObjectItemCaseSensitive(
            task, "task_id");
        const cJSON *task_name = cJSON_GetObjectItemCaseSensitive(
            task, "task_name");
        const cJSON *difficulty = cJSON_GetObjectItemCaseSensitive(
            task, "difficulty");
        const cJSON *level = cJSON_GetObjectItemCaseSensitive(task, "level");
        const cJSON *semantic_group = cJSON_GetObjectItemCaseSensitive(
            task, "semantic_group");
        const cJSON *action_family = cJSON_GetObjectItemCaseSensitive(
            task, "action_family");
        const cJSON *plane = cJSON_GetObjectItemCaseSensitive(task, "plane");
        const rehab_task_policy_definition_t *policy =
            rehab_task_policy_task_at(i);
        valid =
            exact_fields(task, task_fields,
                         sizeof(task_fields) / sizeof(task_fields[0])) &&
            cJSON_IsString(task_id) && task_id->valuestring != NULL &&
            strcmp(task_id->valuestring, s_tasks[i].id) == 0 &&
            cJSON_IsString(task_name) && task_name->valuestring != NULL &&
            strcmp(task_name->valuestring, s_tasks[i].name) == 0 &&
            cJSON_IsString(difficulty) &&
            difficulty->valuestring != NULL &&
            strcmp(difficulty->valuestring,
                   s_tasks[i].difficulty) == 0 &&
            policy != NULL &&
            json_integer(level, 1, 3, NULL) &&
            (unsigned)level->valueint == policy->level &&
            cJSON_IsString(semantic_group) &&
            strcmp(semantic_group->valuestring,
                   policy->semantic_group) == 0 &&
            cJSON_IsString(action_family) &&
            strcmp(action_family->valuestring,
                   policy->action_family) == 0 &&
            cJSON_IsString(plane) &&
            strcmp(plane->valuestring, policy->plane) == 0;
    }
    cJSON_Delete(root);
#undef POLICY_NUMBER
    return valid;
}

static bool validate_rules_json(const char *json)
{
    return validate_formal_rules_json(json);
}

static esp_err_t bytes_sha256(const unsigned char *bytes, size_t length,
                              char output[65])
{
    unsigned char digest[32] = {0};
    mbedtls_sha256_context context;
    mbedtls_sha256_init(&context);
    int result = mbedtls_sha256_starts(&context, 0);
    if (result == 0) {
        result = mbedtls_sha256_update(&context, bytes, length);
    }
    if (result == 0) {
        result = mbedtls_sha256_finish(&context, digest);
    }
    mbedtls_sha256_free(&context);
    if (result != 0) {
        return ESP_FAIL;
    }
    for (size_t i = 0; i < sizeof(digest); ++i) {
        snprintf(output + i * 2U, 3U, "%02x", digest[i]);
    }
    output[64] = '\0';
    return ESP_OK;
}

static esp_err_t read_rules_file(claw_path_root_t root,
                                 char **out_json,
                                 size_t *out_length,
                                 char out_path[RULES_PATH_MAX],
                                 char out_sha256[65])
{
    if (out_json == NULL || out_length == NULL || out_path == NULL ||
        out_sha256 == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_json = NULL;
    *out_length = 0;
    out_path[0] = '\0';
    out_sha256[0] = '\0';
    esp_err_t err = claw_paths_join(root, RULES_SUBPATH,
                                    out_path, RULES_PATH_MAX);
    if (err != ESP_OK) {
        return err;
    }
    struct stat info = {0};
    if (stat(out_path, &info) != 0) {
        return errno == ENOENT ? ESP_ERR_NOT_FOUND : ESP_FAIL;
    }
    if (!S_ISREG(info.st_mode) || info.st_size <= 0 ||
        (uint64_t)info.st_size > RULES_MAX_BYTES) {
        return ESP_ERR_INVALID_SIZE;
    }
    size_t length = (size_t)info.st_size;
    char *json = calloc(1, length + 1U);
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }
    FILE *file = fopen(out_path, "rb");
    if (file == NULL) {
        free(json);
        return ESP_FAIL;
    }
    size_t received = fread(json, 1, length, file);
    bool ok = received == length && !ferror(file);
    if (fclose(file) != 0) {
        ok = false;
    }
    if (!ok) {
        free(json);
        return ESP_FAIL;
    }
    json[length] = '\0';
    if (strlen(json) != length) {
        free(json);
        return ESP_ERR_INVALID_RESPONSE;
    }
    err = bytes_sha256((const unsigned char *)json, length, out_sha256);
    if (err != ESP_OK || !validate_rules_json(json)) {
        free(json);
        return err == ESP_OK ? ESP_ERR_INVALID_RESPONSE : err;
    }
    *out_json = json;
    *out_length = length;
    return ESP_OK;
}

static esp_err_t build_builtin_rules(char **out_json, size_t *out_length)
{
    if (out_json == NULL || out_length == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_json = NULL;
    *out_length = 0;
    cJSON *root = cJSON_CreateObject();
    bool valid =
        root != NULL &&
        cJSON_AddStringToObject(root, "schema_version",
                               FORMAL_RULES_SCHEMA) != NULL &&
        cJSON_AddStringToObject(root, "decision_rules_version",
                               REHAB_TASK_DECISION_RULES_VERSION) != NULL &&
        cJSON_AddNumberToObject(root, "recent_score_window", 3) != NULL;
    cJSON *guidance = valid
        ? cJSON_AddObjectToObject(root, "scoring_guidance") : NULL;
    const rehab_task_scoring_policy_t *scoring =
        rehab_task_scoring_policy();
    valid = valid && guidance != NULL && scoring != NULL &&
        cJSON_AddNumberToObject(guidance, "difficulty_max",
                                scoring->difficulty_max) != NULL &&
        cJSON_AddNumberToObject(guidance, "difficulty_easier_step",
                                scoring->difficulty_easier_step) != NULL &&
        cJSON_AddNumberToObject(guidance, "difficulty_harder_step",
                                scoring->difficulty_harder_step) != NULL &&
        cJSON_AddNumberToObject(guidance, "advice_match",
                                scoring->advice_match) != NULL &&
        cJSON_AddNumberToObject(guidance, "advice_neutral",
                                scoring->advice_neutral) != NULL &&
        cJSON_AddNumberToObject(guidance, "advice_mismatch",
                                scoring->advice_mismatch) != NULL &&
        cJSON_AddNumberToObject(guidance, "compensation_max",
                                scoring->compensation_max) != NULL &&
        cJSON_AddNumberToObject(guidance, "compensation_severity_1",
                                scoring->compensation_severity_1) != NULL &&
        cJSON_AddNumberToObject(guidance, "compensation_severity_2",
                                scoring->compensation_severity_2) != NULL &&
        cJSON_AddNumberToObject(guidance, "compensation_severity_3_plus",
                                scoring->compensation_severity_3_plus) != NULL &&
        cJSON_AddNumberToObject(guidance, "relatedness_exact_task",
                                scoring->relatedness_exact_task) != NULL &&
        cJSON_AddNumberToObject(guidance, "relatedness_same_action_family",
                                scoring->relatedness_same_action_family) != NULL &&
        cJSON_AddNumberToObject(guidance, "relatedness_same_object_group",
                                scoring->relatedness_same_object_group) != NULL &&
        cJSON_AddNumberToObject(guidance, "frequency_0",
                                scoring->frequency_0) != NULL &&
        cJSON_AddNumberToObject(guidance, "frequency_1",
                                scoring->frequency_1) != NULL &&
        cJSON_AddNumberToObject(guidance, "frequency_2",
                                scoring->frequency_2) != NULL &&
        cJSON_AddNumberToObject(guidance, "frequency_3",
                                scoring->frequency_3) != NULL &&
        cJSON_AddNumberToObject(guidance, "perception_min",
                                scoring->perception_min) != NULL &&
        cJSON_AddNumberToObject(guidance, "perception_span",
                                scoring->perception_span) != NULL &&
        cJSON_AddNumberToObject(guidance, "perception_geometry_weight",
                                scoring->perception_geometry_weight) != NULL &&
        cJSON_AddNumberToObject(guidance, "perception_detection_weight",
                                scoring->perception_detection_weight) != NULL &&
        cJSON_AddNumberToObject(guidance, "perception_height_weight",
                                scoring->perception_height_weight) != NULL &&
        cJSON_AddNumberToObject(guidance, "fallback_detection_weight",
                                scoring->fallback_detection_weight) != NULL &&
        cJSON_AddNumberToObject(guidance, "fallback_height_weight",
                                scoring->fallback_height_weight) != NULL &&
        cJSON_AddNumberToObject(guidance, "score_decimal_places",
                                scoring->score_decimal_places) != NULL &&
        cJSON_AddNumberToObject(guidance, "validation_tolerance",
                                scoring->validation_tolerance) != NULL;
    cJSON *height = valid
        ? cJSON_AddObjectToObject(root, "height_policy") : NULL;
    valid = valid && height != NULL &&
        cJSON_AddNumberToObject(height, "low_min_mm", 550) != NULL &&
        cJSON_AddNumberToObject(height, "low_max_mm_inclusive", 890) != NULL &&
        cJSON_AddNumberToObject(height, "low_center_mm", 710) != NULL &&
        cJSON_AddNumberToObject(height, "high_min_exclusive_mm", 890) != NULL &&
        cJSON_AddNumberToObject(height, "high_center_mm", 1070) != NULL &&
        cJSON_AddStringToObject(
            height, "confidence",
            "clamp(1-abs(support_height_mm-center_mm)/200,0,1)") != NULL;
    cJSON *groups = valid
        ? cJSON_AddObjectToObject(root, "semantic_groups") : NULL;
    cJSON *cup = groups != NULL ? cJSON_AddArrayToObject(groups, "cup") : NULL;
    cJSON *ball = groups != NULL ? cJSON_AddArrayToObject(groups, "ball") : NULL;
    valid = valid && groups != NULL && cup != NULL && ball != NULL;
    const char *cup_classes[] = {"cup", "bottle"};
    const char *ball_classes[] = {"sports ball", "ball", "volleyball"};
    for (size_t i = 0; valid && i < 2U; ++i) {
        valid = cJSON_AddItemToArray(cup,
                    cJSON_CreateString(cup_classes[i]));
    }
    for (size_t i = 0; valid && i < 3U; ++i) {
        valid = cJSON_AddItemToArray(ball,
                    cJSON_CreateString(ball_classes[i]));
    }
    cJSON *tasks = valid ? cJSON_AddArrayToObject(root, "tasks") : NULL;
    valid = valid && tasks != NULL;
    for (size_t i = 0; valid &&
         i < REHAB_TASK_DECISION_TASK_COUNT; ++i) {
        cJSON *task = cJSON_CreateObject();
        const rehab_task_policy_definition_t *policy =
            rehab_task_policy_task_at(i);
        valid =
            task != NULL && policy != NULL &&
            cJSON_AddStringToObject(task, "task_id",
                                   s_tasks[i].id) != NULL &&
            cJSON_AddStringToObject(task, "task_name",
                                   s_tasks[i].name) != NULL &&
            cJSON_AddStringToObject(task, "difficulty",
                                   s_tasks[i].difficulty) != NULL &&
            cJSON_AddNumberToObject(task, "level", policy->level) != NULL &&
            cJSON_AddStringToObject(task, "semantic_group",
                                   policy->semantic_group) != NULL &&
            cJSON_AddStringToObject(task, "action_family",
                                   policy->action_family) != NULL &&
            cJSON_AddStringToObject(task, "plane", policy->plane) != NULL;
        if (valid) {
            cJSON_AddItemToArray(tasks, task);
        } else {
            cJSON_Delete(task);
        }
    }
    char *rendered = valid ? cJSON_PrintUnformatted(root) : NULL;
    cJSON_Delete(root);
    if (rendered == NULL) {
        return ESP_ERR_NO_MEM;
    }
    size_t length = strlen(rendered);
    if (!validate_formal_rules_json(rendered)) {
        cJSON_free(rendered);
        return ESP_ERR_INVALID_RESPONSE;
    }
    char *owned = strdup(rendered);
    cJSON_free(rendered);
    if (owned == NULL) {
        return ESP_ERR_NO_MEM;
    }
    *out_json = owned;
    *out_length = length;
    return ESP_OK;
}

esp_err_t rehab_task_decision_rules_initialize(
    rehab_task_decision_handle_t handle)
{
    if (handle == NULL || handle->decision_rules_json != NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    static const claw_path_root_t roots[] = {
        CLAW_PATH_DATA,
        CLAW_PATH_SYSTEM,
    };
    for (size_t i = 0; i < sizeof(roots) / sizeof(roots[0]); ++i) {
        char path[RULES_PATH_MAX] = {0};
        char sha256[65] = {0};
        esp_err_t err = read_rules_file(
            roots[i], &handle->decision_rules_json,
            &handle->decision_rules_json_length, path, sha256);
        const char *source = roots[i] == CLAW_PATH_SYSTEM
            ? "system" : "data";
        if (roots[i] == CLAW_PATH_DATA) {
            const char *data_root = claw_paths_get(CLAW_PATH_DATA);
            if (data_root != NULL &&
                strcmp(data_root, "/sdcard") == 0) {
                source = "sdcard";
            }
        }
        if (err == ESP_OK) {
            ESP_LOGI(TAG,
                     "TASK_DECISION_RULES_READY source=%s path=%s "
                     "version=%s sha256=%s",
                     source, path, REHAB_TASK_DECISION_RULES_VERSION,
                     sha256);
            return ESP_OK;
        }
        if (err != ESP_ERR_NOT_FOUND) {
            ESP_LOGE(TAG,
                     "TASK_DECISION_RULES_INVALID source=%s path=%s err=%s",
                     source, path, esp_err_to_name(err));
            return err;
        }
    }
    esp_err_t err = build_builtin_rules(
        &handle->decision_rules_json,
        &handle->decision_rules_json_length);
    if (err == ESP_OK) {
        ESP_LOGI(TAG,
                 "TASK_DECISION_RULES_READY source=builtin_fallback "
                 "version=%s",
                 REHAB_TASK_DECISION_RULES_VERSION);
    } else {
        ESP_LOGE(TAG, "RULES_INIT_FAILED source=builtin_fallback err=%s",
                 esp_err_to_name(err));
    }
    return err;
}

void rehab_task_decision_rules_release(rehab_task_decision_handle_t handle)
{
    if (handle != NULL) {
        free(handle->decision_rules_json);
        handle->decision_rules_json = NULL;
        handle->decision_rules_json_length = 0;
    }
}

static esp_err_t rules_finish(cJSON *root, char *output, size_t output_size)
{
    if (root == NULL || output == NULL || output_size == 0) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (json == NULL) {
        return ESP_ERR_NO_MEM;
    }
    size_t length = strlen(json);
    if (length >= output_size) {
        cJSON_free(json);
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(output, json, length + 1U);
    cJSON_free(json);
    return ESP_OK;
}

esp_err_t rehab_task_decision_rules_render(
    rehab_task_decision_handle_t handle,
    uint32_t request_id,
    char *output,
    size_t output_size)
{
    if (handle == NULL || request_id == 0 || output == NULL ||
        output_size == 0 || handle->decision_rules_json == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    output[0] = '\0';
    cJSON *rules = cJSON_ParseWithOpts(handle->decision_rules_json, NULL, true);
    cJSON *root = cJSON_CreateObject();
    if (rules == NULL || root == NULL) {
        cJSON_Delete(rules);
        cJSON_Delete(root);
        return ESP_ERR_NO_MEM;
    }
    cJSON_AddStringToObject(root, "schema_version", RULES_RESPONSE_SCHEMA);
    cJSON_AddItemToObject(root, "decision_rules", rules);

    if (!rules_lock(handle)) {
        cJSON_Delete(root);
        return ESP_ERR_TIMEOUT;
    }
    uint32_t generation = handle->status.generation;
    bool current = handle->status.state == REHAB_TASK_DECISION_STATE_DECIDING &&
        handle->status.final_state_frozen && !handle->status.worker_active &&
        !handle->status.perception_in_flight && generation != 0 &&
        handle->decision_context_generation == generation &&
        handle->decision_context_request_id == request_id &&
        handle->decision_rules_generation == 0 &&
        handle->decision_rules_request_id == 0;
    if (current) {
        handle->decision_rules_generation = generation;
        handle->decision_rules_request_id = request_id;
    }
    rules_unlock(handle);
    if (!current) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = rules_finish(root, output, output_size);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "RULES_GET generation=%lu request_id=%lu",
                 (unsigned long)generation, (unsigned long)request_id);
    }
    return err;
}

esp_err_t rehab_task_decision_rules_task_metadata(
    rehab_task_decision_handle_t handle,
    const char *task_id,
    char *task_name,
    size_t task_name_size,
    char *rules_version,
    size_t rules_version_size)
{
    if (handle == NULL || handle->decision_rules_json == NULL ||
        task_id == NULL || task_name == NULL || task_name_size == 0 ||
        rules_version == NULL || rules_version_size == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = cJSON_ParseWithOpts(handle->decision_rules_json, NULL, true);
    if (root == NULL) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(
        root, "decision_rules_version");
    const cJSON *tasks = cJSON_GetObjectItemCaseSensitive(root, "tasks");
    const char *name = NULL;
    int count = cJSON_IsArray(tasks) ? cJSON_GetArraySize(tasks) : 0;
    for (int i = 0; i < count; ++i) {
        const cJSON *task = cJSON_GetArrayItem(tasks, i);
        const cJSON *id = cJSON_GetObjectItemCaseSensitive(task, "task_id");
        const cJSON *candidate = cJSON_GetObjectItemCaseSensitive(task,
                                                                  "task_name");
        if (cJSON_IsString(id) && id->valuestring != NULL &&
            strcmp(id->valuestring, task_id) == 0 &&
            cJSON_IsString(candidate) && candidate->valuestring != NULL) {
            name = candidate->valuestring;
            break;
        }
    }
    bool valid = name != NULL && cJSON_IsString(version) &&
        version->valuestring != NULL &&
        strlen(name) < task_name_size &&
        strlen(version->valuestring) < rules_version_size;
    if (valid) {
        strlcpy(task_name, name, task_name_size);
        strlcpy(rules_version, version->valuestring, rules_version_size);
    }
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_NOT_FOUND;
}

esp_err_t rehab_task_decision_rules_recent_score_window(
    rehab_task_decision_handle_t handle,
    size_t *out_window)
{
    if (handle == NULL || handle->decision_rules_json == NULL ||
        out_window == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = cJSON_ParseWithOpts(handle->decision_rules_json, NULL, true);
    int window = 0;
    bool valid = root != NULL &&
        json_integer(cJSON_GetObjectItemCaseSensitive(
                         root, "recent_score_window"),
                     1, 10, &window);
    cJSON_Delete(root);
    if (!valid) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    *out_window = (size_t)window;
    return ESP_OK;
}

esp_err_t rehab_task_decision_rules_hard_gate_eligible(
    rehab_task_decision_handle_t handle,
    const char *task_id,
    const rehab_task_decision_status_t *context_status,
    bool *out_eligible)
{
    if (handle == NULL || handle->decision_rules_json == NULL ||
        task_id == NULL || context_status == NULL || out_eligible == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_eligible = false;
    const rehab_task_policy_definition_t *task =
        rehab_task_policy_find(task_id);
    if (task == NULL) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    size_t class_count = context_status->class_count <
        REHAB_TASK_DECISION_MAX_CLASSES
            ? context_status->class_count : REHAB_TASK_DECISION_MAX_CLASSES;
    for (size_t class_index = 0; class_index < class_count;
         ++class_index) {
        const rehab_environment_class_summary_t *observed =
            &context_status->classes[class_index];
        const char *group = rehab_task_policy_semantic_group(
            observed->class_name);
        if (observed->seen_count > 0 && group != NULL &&
            strcmp(group, task->semantic_group) == 0) {
            *out_eligible = true;
            break;
        }
    }
    return ESP_OK;
}
