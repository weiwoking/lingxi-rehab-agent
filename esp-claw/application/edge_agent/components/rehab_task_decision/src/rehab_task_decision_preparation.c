// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_preparation.h"

#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "rehab_task_decision_policy.h"

#define PREPARATION_ADVICE_MIN 16
#define PREPARATION_ADVICE_MAX 25

typedef struct {
    double difficulty_max;
    double difficulty_easier_step;
    double difficulty_harder_step;
    double compensation_max;
    double relatedness_exact_task;
    double relatedness_same_action_family;
    double relatedness_same_object_group;
    double frequency_0;
    double frequency_1;
    double frequency_2;
    double frequency_3;
    double perception_min;
    double perception_span;
    double perception_geometry_weight;
    double perception_detection_weight;
    double perception_height_weight;
    double fallback_detection_weight;
    double fallback_height_weight;
    double validation_tolerance;
} preparation_policy_t;

static const cJSON *member(const cJSON *object, const char *name)
{
    return cJSON_IsObject(object)
        ? cJSON_GetObjectItemCaseSensitive(object, name) : NULL;
}

static bool finite_number(const cJSON *item, double *out_value)
{
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble)) {
        return false;
    }
    if (out_value != NULL) {
        *out_value = item->valuedouble;
    }
    return true;
}

static bool nonnegative_integer(const cJSON *item, unsigned *out_value)
{
    double value = 0.0;
    if (!finite_number(item, &value) || value < 0.0 ||
        floor(value) != value || value > (double)UINT_MAX) {
        return false;
    }
    *out_value = (unsigned)value;
    return true;
}

static double clamp(double value, double minimum, double maximum)
{
    return value < minimum ? minimum :
        (value > maximum ? maximum : value);
}

static bool policy_number(const cJSON *parameters, const char *name,
                          double *out_value)
{
    return finite_number(member(parameters, name), out_value);
}

static bool load_policy(const cJSON *formal, preparation_policy_t *out)
{
    const cJSON *policy = member(formal, "scoring_policy");
    const cJSON *parameters = member(policy, "parameters");
    const cJSON *rounding = member(policy, "score_rounding");
    const rehab_task_scoring_policy_t *registry =
        rehab_task_scoring_policy();
    if (registry == NULL) {
        return false;
    }
    out->validation_tolerance = registry->validation_tolerance;
    return cJSON_IsObject(parameters) && cJSON_IsString(rounding) &&
        rounding->valuestring != NULL &&
        strcmp(rounding->valuestring,
               "round_half_away_to_3_decimals") == 0 &&
        policy_number(parameters, "difficulty_max",
                      &out->difficulty_max) &&
        policy_number(parameters, "difficulty_easier_step",
                      &out->difficulty_easier_step) &&
        policy_number(parameters, "difficulty_harder_step",
                      &out->difficulty_harder_step) &&
        policy_number(parameters, "compensation_max",
                      &out->compensation_max) &&
        policy_number(parameters, "relatedness_exact_task",
                      &out->relatedness_exact_task) &&
        policy_number(parameters, "relatedness_same_action_family",
                      &out->relatedness_same_action_family) &&
        policy_number(parameters, "relatedness_same_object_group",
                      &out->relatedness_same_object_group) &&
        policy_number(parameters, "frequency_0", &out->frequency_0) &&
        policy_number(parameters, "frequency_1", &out->frequency_1) &&
        policy_number(parameters, "frequency_2", &out->frequency_2) &&
        policy_number(parameters, "frequency_3", &out->frequency_3) &&
        policy_number(parameters, "perception_min",
                      &out->perception_min) &&
        policy_number(parameters, "perception_span",
                      &out->perception_span) &&
        policy_number(parameters, "perception_geometry_weight",
                      &out->perception_geometry_weight) &&
        policy_number(parameters, "perception_detection_weight",
                      &out->perception_detection_weight) &&
        policy_number(parameters, "perception_height_weight",
                      &out->perception_height_weight) &&
        policy_number(parameters, "fallback_detection_weight",
                      &out->fallback_detection_weight) &&
        policy_number(parameters, "fallback_height_weight",
                      &out->fallback_height_weight);
}

static const cJSON *candidate_at(const cJSON *formal, size_t candidate_ref)
{
    const cJSON *candidates = member(formal, "candidates");
    const cJSON *candidate = cJSON_IsArray(candidates)
        ? cJSON_GetArrayItem(candidates, (int)candidate_ref) : NULL;
    const cJSON *ref = member(candidate, "candidate_ref");
    return cJSON_IsObject(candidate) && cJSON_IsNumber(ref) &&
        isfinite(ref->valuedouble) && ref->valuedouble ==
            (double)candidate_ref
        ? candidate : NULL;
}

static bool parse_tendency(const cJSON *formal, unsigned *out_level)
{
    const cJSON *recent = member(formal, "recent_performance");
    const cJSON *tendency = member(recent, "tendency_level");
    unsigned level = 0U;
    char canonical[32] = {0};
    if (!cJSON_IsString(tendency) || tendency->valuestring == NULL ||
        sscanf(tendency->valuestring, "level_%u", &level) != 1 ||
        level == 0U ||
        snprintf(canonical, sizeof(canonical), "level_%u", level) < 0 ||
        strcmp(canonical, tendency->valuestring) != 0) {
        return false;
    }
    *out_level = level;
    return true;
}

static bool strings_equal(const cJSON *left, const cJSON *right)
{
    return cJSON_IsString(left) && left->valuestring != NULL &&
        cJSON_IsString(right) && right->valuestring != NULL &&
        strcmp(left->valuestring, right->valuestring) == 0;
}

static bool frequency_for_candidate(const cJSON *formal,
                                    const cJSON *candidate,
                                    const preparation_policy_t *policy,
                                    double *out_score)
{
    const cJSON *frequency = member(formal, "item_frequency");
    const cJSON *group = member(candidate, "semantic_group");
    const char *count_name = cJSON_IsString(group) &&
        group->valuestring != NULL && strcmp(group->valuestring, "cup") == 0
        ? "cup_count" :
        (cJSON_IsString(group) && group->valuestring != NULL &&
         strcmp(group->valuestring, "ball") == 0 ? "ball_count" : NULL);
    unsigned count = 0U;
    if (count_name == NULL ||
        !nonnegative_integer(member(frequency, count_name), &count)) {
        return false;
    }
    *out_score = count == 0U ? policy->frequency_0 :
        (count == 1U ? policy->frequency_1 :
         (count == 2U ? policy->frequency_2 : policy->frequency_3));
    return true;
}

static bool compensation_for_candidate(
    const cJSON *formal, const cJSON *candidate,
    const preparation_policy_t *policy, double *out_score)
{
    const cJSON *latest = member(formal, "latest_compensation");
    const cJSON *status = member(latest, "status");
    double severity = 0.0;
    if (!cJSON_IsString(status) || status->valuestring == NULL ||
        !finite_number(member(latest, "severity"), &severity) ||
        severity < 0.0 || severity > 1.0) {
        return false;
    }
    double relatedness = 0.0;
    if (strcmp(status->valuestring, "available") == 0) {
        if (strings_equal(member(latest, "exact_task_id"),
                          member(candidate, "task_id"))) {
            relatedness = policy->relatedness_exact_task;
        } else if (strings_equal(member(latest, "action_family"),
                                 member(candidate, "action_family"))) {
            relatedness = policy->relatedness_same_action_family;
        } else if (strings_equal(member(latest, "mapped_group"),
                                 member(candidate, "semantic_group"))) {
            relatedness = policy->relatedness_same_object_group;
        }
    } else if (strcmp(status->valuestring, "none") != 0) {
        return false;
    }
    *out_score = policy->compensation_max *
        (1.0 - severity * relatedness);
    return isfinite(*out_score);
}

static bool perception_for_candidate(
    const cJSON *candidate, const preparation_policy_t *policy,
    double *out_score)
{
    const cJSON *facts = member(candidate, "target_facts");
    double quality = 0.0;
    const bool projected_quality_valid =
        finite_number(member(facts, "perception_quality"), &quality) &&
        quality >= 0.0 && quality <= 1.0;
    if (!projected_quality_valid) {
        double detection = 0.0;
        double height = 0.0;
        double geometry = 0.0;
        (void)finite_number(member(facts, "detection_confidence"),
                            &detection);
        (void)finite_number(member(facts, "height_confidence"), &height);
        detection = clamp(detection, 0.0, 1.0);
        height = clamp(height, 0.0, 1.0);
        if (finite_number(member(facts, "geometry_confidence"),
                          &geometry)) {
            quality = policy->perception_geometry_weight *
                          clamp(geometry, 0.0, 1.0) +
                policy->perception_detection_weight * detection +
                policy->perception_height_weight * height;
        } else {
            quality = policy->fallback_detection_weight * detection +
                policy->fallback_height_weight * height;
        }
        quality = clamp(quality, 0.0, 1.0);
    }
    *out_score = policy->perception_min +
        policy->perception_span * quality;
    return isfinite(*out_score);
}

esp_err_t rehab_task_decision_preparation_calculate(
    const cJSON *formal,
    size_t candidate_ref,
    rehab_task_decision_prepared_candidate_t *out_prepared)
{
    if (!cJSON_IsObject(formal) || out_prepared == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_prepared, 0, sizeof(*out_prepared));
    preparation_policy_t policy = {0};
    const cJSON *candidate = candidate_at(formal, candidate_ref);
    const cJSON *availability = member(candidate, "availability");
    if (candidate == NULL || !load_policy(formal, &policy) ||
        !cJSON_IsString(availability) || availability->valuestring == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    out_prepared->validation_tolerance = policy.validation_tolerance;
    if (strcmp(availability->valuestring, "blocked") == 0) {
        out_prepared->blocked = true;
        return ESP_OK;
    }
    if (strcmp(availability->valuestring, "available") != 0) {
        return ESP_ERR_INVALID_STATE;
    }

    unsigned tendency = 0U;
    unsigned level = 0U;
    if (!parse_tendency(formal, &tendency) ||
        !nonnegative_integer(member(candidate, "level"), &level) ||
        level == 0U) {
        return ESP_ERR_INVALID_STATE;
    }
    const int delta = (int)level - (int)tendency;
    double difficulty = policy.difficulty_max;
    if (delta < 0) {
        difficulty -= policy.difficulty_easier_step * (double)(-delta);
    } else if (delta > 0) {
        difficulty -= policy.difficulty_harder_step * (double)delta;
    }
    out_prepared->difficulty = clamp(
        difficulty, 0.0, policy.difficulty_max);
    if (!compensation_for_candidate(formal, candidate, &policy,
                                    &out_prepared->compensation) ||
        !frequency_for_candidate(formal, candidate, &policy,
                                 &out_prepared->frequency) ||
        !perception_for_candidate(candidate, &policy,
                                  &out_prepared->perception)) {
        return ESP_ERR_INVALID_STATE;
    }
    out_prepared->fixed_total = out_prepared->difficulty +
        out_prepared->compensation + out_prepared->frequency +
        out_prepared->perception;
    return isfinite(out_prepared->fixed_total) ? ESP_OK :
        ESP_ERR_INVALID_STATE;
}

static bool add_number(cJSON *object, const char *name, double value)
{
    return cJSON_AddNumberToObject(object, name, value) != NULL;
}

static bool add_number_pair(cJSON *object, const char *name,
                            double first, double second)
{
    cJSON *pair = cJSON_AddArrayToObject(object, name);
    return pair != NULL &&
        cJSON_AddItemToArray(pair, cJSON_CreateNumber(first)) &&
        cJSON_AddItemToArray(pair, cJSON_CreateNumber(second));
}

esp_err_t rehab_task_decision_preparation_add(cJSON *compact,
                                              const cJSON *formal)
{
    if (!cJSON_IsObject(compact) || !cJSON_IsObject(formal)) {
        return ESP_ERR_INVALID_ARG;
    }
    const cJSON *source_policy = member(formal, "scoring_policy");
    const cJSON *policy_version = member(source_policy,
                                         "decision_rules_version");
    const cJSON *source_tie_order = member(source_policy,
                                           "tie_break_candidate_refs");
    cJSON *root = cJSON_AddObjectToObject(compact,
                                          "decision_preparation");
    cJSON *advice_policy = root != NULL
        ? cJSON_AddObjectToObject(root, "advice_policy") : NULL;
    cJSON *candidates = root != NULL
        ? cJSON_AddArrayToObject(root, "candidates") : NULL;
    cJSON *tie_order = cJSON_IsArray(source_tie_order)
        ? cJSON_Duplicate(source_tie_order, true) : NULL;
    if (root == NULL || advice_policy == NULL || candidates == NULL ||
        tie_order == NULL ||
        !cJSON_IsString(policy_version) || policy_version->valuestring == NULL ||
        cJSON_AddStringToObject(root, "schema_version",
            REHAB_TASK_DECISION_PREPARATION_SCHEMA_VERSION) == NULL ||
        cJSON_AddStringToObject(root, "source_policy_version",
            policy_version->valuestring) == NULL ||
        cJSON_AddStringToObject(root, "fixed_component_authority",
                               "P4") == NULL ||
        cJSON_AddStringToObject(root, "business_authority",
                               "DeepSeek") == NULL ||
        !add_number_pair(advice_policy, "range", PREPARATION_ADVICE_MIN,
                         PREPARATION_ADVICE_MAX) ||
        !add_number_pair(advice_policy, "positive_range", 21.0, 25.0) ||
        !add_number_pair(advice_policy, "negative_range", 16.0, 19.0) ||
        !add_number(advice_policy, "neutral", 20.0) ||
        !cJSON_AddItemToObject(root, "tie_break_candidate_refs",
                               tie_order)) {
        cJSON_Delete(tie_order);
        return ESP_ERR_NO_MEM;
    }

    for (size_t ref = 0U; ref < rehab_task_policy_task_count(); ++ref) {
        rehab_task_decision_prepared_candidate_t prepared = {0};
        esp_err_t err = rehab_task_decision_preparation_calculate(
            formal, ref, &prepared);
        if (err != ESP_OK) {
            return err;
        }
        cJSON *entry = cJSON_CreateObject();
        if (entry == NULL ||
            !add_number(entry, "candidate_ref", (double)ref) ||
            cJSON_AddStringToObject(entry, "availability",
                prepared.blocked ? "blocked" : "available") == NULL) {
            cJSON_Delete(entry);
            return ESP_ERR_NO_MEM;
        }
        if (prepared.blocked) {
            if (!cJSON_AddNullToObject(entry, "fixed_components")) {
                cJSON_Delete(entry);
                return ESP_ERR_NO_MEM;
            }
            if (!cJSON_AddItemToArray(candidates, entry)) {
                cJSON_Delete(entry);
                return ESP_ERR_NO_MEM;
            }
            continue;
        }
        cJSON *fixed = cJSON_AddObjectToObject(entry, "fixed_components");
        if (fixed == NULL ||
            !add_number(fixed, "difficulty", prepared.difficulty) ||
            !add_number(fixed, "compensation", prepared.compensation) ||
            !add_number(fixed, "frequency", prepared.frequency) ||
            !add_number(fixed, "perception", prepared.perception) ||
            !add_number(fixed, "fixed_total", prepared.fixed_total)) {
            cJSON_Delete(entry);
            return ESP_ERR_NO_MEM;
        }
        if (!cJSON_AddItemToArray(candidates, entry)) {
            cJSON_Delete(entry);
            return ESP_ERR_NO_MEM;
        }
    }
    return ESP_OK;
}
