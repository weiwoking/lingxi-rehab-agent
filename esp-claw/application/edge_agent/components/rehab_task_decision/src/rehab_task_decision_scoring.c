// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_scoring.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "rehab_task_decision_policy.h"
#include "rehab_task_decision_preparation.h"

#define SCORING_REASON_MAX 512U
#define SCORING_SUMMARY_MAX 512U
#define SCORING_EVIDENCE_MAX 512U
#define SCORING_INPUT_SCHEMA "rehab_task_decision_input.v4"
#define SCORING_OUTPUT_SCHEMA "rehab_task_decision_output.v4"

bool rehab_task_decision_text_valid(const char *text,
                                    size_t max_bytes,
                                    size_t max_codepoints,
                                    bool allow_empty);

static bool add_number(cJSON *object, const char *name, double value)
{
    return cJSON_AddNumberToObject(object, name, value) != NULL;
}

static bool add_string(cJSON *object, const char *name, const char *value)
{
    return cJSON_AddStringToObject(object, name, value) != NULL;
}

esp_err_t rehab_task_scoring_add_policy(cJSON *root)
{
    if (!cJSON_IsObject(root)) {
        return ESP_ERR_INVALID_ARG;
    }
    const rehab_task_scoring_policy_t *p = rehab_task_scoring_policy();
    cJSON *policy = cJSON_AddObjectToObject(root, "scoring_policy");
    cJSON *parameters = policy != NULL
        ? cJSON_AddObjectToObject(policy, "parameters") : NULL;
    cJSON *rules = policy != NULL
        ? cJSON_AddObjectToObject(policy, "component_rules") : NULL;
    cJSON *gates = policy != NULL
        ? cJSON_AddArrayToObject(policy, "hard_gate_rules") : NULL;
    cJSON *tie_order = policy != NULL
        ? cJSON_AddArrayToObject(policy, "tie_break_candidate_refs") : NULL;
    bool ok = p != NULL && policy != NULL && parameters != NULL &&
        rules != NULL && gates != NULL &&
        tie_order != NULL &&
        add_string(policy, "policy_source",
                   "rehab_task_decision_policy registry") &&
        add_string(policy, "decision_rules_version",
                   REHAB_TASK_POLICY_RULES_VERSION) &&
        add_string(policy, "scoring_authority",
                   "P4 is the authority for immutable machine facts and deterministic preparation of difficulty, compensation, frequency, and perception. DeepSeek remains the business authority for patient free-text semantics, medical hard blocks, candidate-scoped advice, winner_ref, reason, advice_basis, and summary. The validator independently recomputes fixed components, final_raw, and score but never chooses advice or reselects the winner") &&
        add_string(policy, "formula",
                   "clamp(difficulty+advice+compensation+frequency+perception,0,100)") &&
        add_string(policy, "score_rounding",
                   "round_half_away_to_3_decimals") &&
        add_number(parameters, "difficulty_max", p->difficulty_max) &&
        add_number(parameters, "difficulty_easier_step", p->difficulty_easier_step) &&
        add_number(parameters, "difficulty_harder_step", p->difficulty_harder_step) &&
        add_number(parameters, "compensation_max", p->compensation_max) &&
        add_number(parameters, "compensation_severity_1", p->compensation_severity_1) &&
        add_number(parameters, "compensation_severity_2", p->compensation_severity_2) &&
        add_number(parameters, "compensation_severity_3_plus", p->compensation_severity_3_plus) &&
        add_number(parameters, "relatedness_exact_task", p->relatedness_exact_task) &&
        add_number(parameters, "relatedness_same_action_family", p->relatedness_same_action_family) &&
        add_number(parameters, "relatedness_same_object_group", p->relatedness_same_object_group) &&
        add_number(parameters, "frequency_0", p->frequency_0) &&
        add_number(parameters, "frequency_1", p->frequency_1) &&
        add_number(parameters, "frequency_2", p->frequency_2) &&
        add_number(parameters, "frequency_3", p->frequency_3) &&
        add_number(parameters, "perception_min", p->perception_min) &&
        add_number(parameters, "perception_span", p->perception_span) &&
        add_number(parameters, "perception_geometry_weight", p->perception_geometry_weight) &&
        add_number(parameters, "perception_detection_weight", p->perception_detection_weight) &&
        add_number(parameters, "perception_height_weight", p->perception_height_weight) &&
        add_number(parameters, "fallback_detection_weight", p->fallback_detection_weight) &&
        add_number(parameters, "fallback_height_weight", p->fallback_height_weight) &&
        add_string(rules, "difficulty",
                   "35 at tendency; subtract 8 per easier level or 21 per harder level; clamp [0,35]") &&
        add_string(rules, "advice",
                   "DeepSeek assigns each available candidate an independent integer advice score in [16,25] from free-text semantics; P4 range-checks it and never derives it from one global direction") &&
        add_string(rules, "compensation_frequency",
                   "severity n=0/1/2/3+ => 0/0.30/0.90/1; relatedness exact/same-action/same-object/unrelated => 1/0.8/0.6/0; compensation=20*(1-severity*relatedness); frequency 0/1/2/3 => 10/6/5/0") &&
        add_string(rules, "height_perception",
                   "no independent height score; perception=6+4*q where q=0.50*geometry+0.30*detection+0.20*height, or q=0.60*detection+0.40*height when geometry_confidence is unavailable or malformed; clamp confidence inputs [0,1]") &&
        add_string(rules, "blocked_breakdown",
                   "availability=blocked requires score_breakdown=null and score=0") &&
        add_string(rules, "reason",
                   "Non-empty readable presentation text in any natural wording, language, punctuation, and fact order; it is not scoring authority and need not repeat structured facts or evidence") &&
        add_string(rules, "summary",
                   "Non-empty readable presentation text with no fixed token template and no hidden reasoning") &&
        add_string(rules, "blocked_reason",
                   "Readable presentation only; P4 enforces frozen input hard gates and hard-block structure/provenance without reinterpreting the reason text") &&
        cJSON_AddItemToArray(gates, cJSON_CreateString(
            "task level must be allowed by patient ability")) &&
        cJSON_AddItemToArray(gates, cJSON_CreateString(
            "semantic group object count must be positive")) &&
        cJSON_AddItemToArray(gates, cJSON_CreateString(
            "a matching-plane resolver target with valid geometry and height must exist"));
    for (size_t i = 0; ok && i < rehab_task_policy_task_count(); ++i) {
        ok = cJSON_AddItemToArray(tie_order,
                                  cJSON_CreateNumber((double)i));
    }
    return ok ? ESP_OK : ESP_ERR_NO_MEM;
}

static void fail(char *error, size_t size, const char *code, size_t index)
{
    if (error == NULL || size == 0U) return;
    if (index < rehab_task_policy_task_count()) {
        snprintf(error, size, "candidate[%u].%s", (unsigned)index, code);
    } else {
        snprintf(error, size, "%s", code);
    }
}

static bool number_near(const cJSON *item, double expected, double tolerance)
{
    return cJSON_IsNumber(item) && isfinite(item->valuedouble) &&
        fabs(item->valuedouble - expected) <= tolerance;
}

static bool number_in_range(const cJSON *item, double minimum,
                            double maximum)
{
    return cJSON_IsNumber(item) && isfinite(item->valuedouble) &&
        item->valuedouble >= minimum && item->valuedouble <= maximum;
}

static bool integer_ref(const cJSON *item, size_t count, size_t *out_ref)
{
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) ||
        item->valuedouble < 0.0 || floor(item->valuedouble) !=
        item->valuedouble || item->valuedouble >= (double)count) {
        return false;
    }
    if (out_ref != NULL) *out_ref = (size_t)item->valuedouble;
    return true;
}

static bool presentation_text_valid(const char *text, size_t maximum)
{
    return maximum > 0U && rehab_task_decision_text_valid(
        text, maximum - 1U, maximum - 1U, false);
}

static bool object_has_exact_fields(const cJSON *object,
                                    const char *const *fields,
                                    size_t field_count)
{
    if (!cJSON_IsObject(object) ||
        cJSON_GetArraySize(object) != (int)field_count) {
        return false;
    }
    for (size_t i = 0; i < field_count; ++i) {
        if (cJSON_GetObjectItemCaseSensitive(object, fields[i]) == NULL) {
            return false;
        }
    }
    const cJSON *child = NULL;
    cJSON_ArrayForEach(child, object) {
        bool allowed = false;
        for (size_t i = 0; i < field_count; ++i) {
            if (child->string != NULL && strcmp(child->string, fields[i]) == 0) {
                allowed = true;
                break;
            }
        }
        if (!allowed) return false;
    }
    return true;
}

static const char *object_missing_field(const cJSON *object,
                                        const char *const *fields,
                                        size_t field_count)
{
    if (!cJSON_IsObject(object)) return NULL;
    for (size_t i = 0; i < field_count; ++i) {
        if (cJSON_GetObjectItemCaseSensitive(object, fields[i]) == NULL) {
            return fields[i];
        }
    }
    return NULL;
}

static const char *object_unexpected_field(const cJSON *object,
                                           const char *const *fields,
                                           size_t field_count)
{
    if (!cJSON_IsObject(object)) return NULL;
    const cJSON *child = NULL;
    cJSON_ArrayForEach(child, object) {
        bool allowed = false;
        for (size_t i = 0; i < field_count; ++i) {
            if (child->string != NULL && strcmp(child->string, fields[i]) == 0) {
                allowed = true;
                break;
            }
        }
        if (!allowed) return child->string != NULL ? child->string : "unnamed";
    }
    return NULL;
}

static const cJSON *input_candidate_by_ref(const cJSON *input,
                                           size_t candidate_ref)
{
    const cJSON *candidates = cJSON_GetObjectItemCaseSensitive(
        input, "candidates");
    const cJSON *candidate = NULL;
    cJSON_ArrayForEach(candidate, candidates) {
        size_t current = SIZE_MAX;
        if (integer_ref(cJSON_GetObjectItemCaseSensitive(
                            candidate, "candidate_ref"),
                        rehab_task_policy_task_count(), &current) &&
            current == candidate_ref) {
            return candidate;
        }
    }
    return NULL;
}

static const cJSON *json_field(const cJSON *object, const char *name)
{
    return cJSON_IsObject(object)
        ? cJSON_GetObjectItemCaseSensitive(object, name) : NULL;
}

static bool hard_block_evidence_has_provenance(const char *raw_text,
                                               const char *evidence)
{
    return raw_text != NULL && evidence != NULL && evidence[0] != '\0' &&
        strstr(raw_text, evidence) != NULL;
}

static bool validate_available_breakdown(const cJSON *input,
                                         const cJSON *candidate,
                                         size_t candidate_ref,
                                         char *error, size_t error_size)
{
    static const char *const breakdown_fields[] = {
        "difficulty", "advice", "compensation", "frequency",
        "perception", "final_raw",
    };
    const cJSON *breakdown = cJSON_GetObjectItemCaseSensitive(
        candidate, "score_breakdown");
    const cJSON *difficulty = cJSON_GetObjectItemCaseSensitive(
        breakdown, "difficulty");
    const cJSON *advice = cJSON_GetObjectItemCaseSensitive(
        breakdown, "advice");
    const cJSON *compensation = cJSON_GetObjectItemCaseSensitive(
        breakdown, "compensation");
    const cJSON *frequency = cJSON_GetObjectItemCaseSensitive(
        breakdown, "frequency");
    const cJSON *perception = cJSON_GetObjectItemCaseSensitive(
        breakdown, "perception");
    const cJSON *final_raw = cJSON_GetObjectItemCaseSensitive(
        breakdown, "final_raw");
    const cJSON *score = cJSON_GetObjectItemCaseSensitive(candidate, "score");
    const char *missing = object_missing_field(
        breakdown, breakdown_fields,
        sizeof(breakdown_fields) / sizeof(breakdown_fields[0]));
    const char *unexpected = object_unexpected_field(
        breakdown, breakdown_fields,
        sizeof(breakdown_fields) / sizeof(breakdown_fields[0]));
    if (!object_has_exact_fields(
            breakdown, breakdown_fields,
            sizeof(breakdown_fields) / sizeof(breakdown_fields[0]))) {
        char detail[96] = {0};
        snprintf(detail, sizeof(detail), "score_breakdown.fields.%s:%s",
                 missing != NULL ? "missing" : "forbidden",
                 missing != NULL ? missing :
                    (unexpected != NULL ? unexpected : "unknown"));
        fail(error, error_size, detail, candidate_ref);
        return false;
    }
    const cJSON *values[] = {
        difficulty, advice, compensation, frequency, perception,
    };
    const double minimums[] = {0.0, 16.0, 0.0, 0.0, 6.0};
    const double maximums[] = {35.0, 25.0, 20.0, 10.0, 10.0};
    const char *range_errors[] = {
        "component.difficulty_range", "component.advice_range",
        "component.compensation_range", "component.frequency_range",
        "component.perception_range",
    };
    if (!number_in_range(advice, 16.0, 25.0) ||
        floor(advice->valuedouble) != advice->valuedouble) {
        fail(error, error_size, "component.advice_range", candidate_ref);
        return false;
    }
    for (size_t i = 0U; i < 5U; ++i) {
        if (i == 1U) continue;
        if (!number_in_range(values[i], minimums[i], maximums[i])) {
            fail(error, error_size, range_errors[i], candidate_ref);
            return false;
        }
    }
    if (!number_in_range(final_raw, -100.0, 200.0) ||
        !number_in_range(score, 0.0, 100.0)) {
        fail(error, error_size, "score_breakdown.range", candidate_ref);
        return false;
    }
    rehab_task_decision_prepared_candidate_t prepared = {0};
    if (rehab_task_decision_preparation_calculate(
            input, candidate_ref, &prepared) != ESP_OK || prepared.blocked) {
        fail(error, error_size, "preparation.invalid", candidate_ref);
        return false;
    }
    const double tolerance = prepared.validation_tolerance > 0.0
        ? prepared.validation_tolerance : 0.000001;
    const struct {
        const cJSON *actual;
        double expected;
        const char *error;
    } prepared_fields[] = {
        {difficulty, prepared.difficulty,
         "component.difficulty_prepared_mismatch"},
        {compensation, prepared.compensation,
         "component.compensation_prepared_mismatch"},
        {frequency, prepared.frequency,
         "component.frequency_prepared_mismatch"},
        {perception, prepared.perception,
         "component.perception_prepared_mismatch"},
    };
    for (size_t i = 0U;
         i < sizeof(prepared_fields) / sizeof(prepared_fields[0]); ++i) {
        if (!number_near(prepared_fields[i].actual,
                         prepared_fields[i].expected, tolerance)) {
            fail(error, error_size, prepared_fields[i].error,
                 candidate_ref);
            return false;
        }
    }
    const double expected_final_raw = prepared.fixed_total +
        advice->valuedouble;
    const double clamped = expected_final_raw < 0.0 ? 0.0 :
        (expected_final_raw > 100.0 ? 100.0 : expected_final_raw);
    const double expected_score = rehab_task_policy_round_score(clamped);
    if (!number_near(final_raw, expected_final_raw, tolerance)) {
        fail(error, error_size, "final_raw.prepared_mismatch",
             candidate_ref);
        return false;
    }
    if (!number_near(score, expected_score, tolerance)) {
        fail(error, error_size, "score.prepared_mismatch", candidate_ref);
        return false;
    }
    return true;
}

bool rehab_task_scoring_validate(const cJSON *input, cJSON *output,
                                 char *error, size_t error_size)
{
    if (error != NULL && error_size > 0U) error[0] = '\0';
    static const char *const root_fields[] = {
        "schema_version", "decision_id", "advice_basis", "candidates",
        "winner_ref", "summary",
    };
    static const char *const candidate_fields[] = {
        "candidate_ref", "availability", "advice_evidence",
        "score_breakdown", "score", "reason",
    };
    const size_t count = rehab_task_policy_task_count();
    const cJSON *input_schema = cJSON_GetObjectItemCaseSensitive(
        input, "schema_version");
    const cJSON *output_schema = cJSON_GetObjectItemCaseSensitive(
        output, "schema_version");
    const cJSON *binding = cJSON_GetObjectItemCaseSensitive(
        input, "decision_binding");
    const cJSON *expected_decision = cJSON_GetObjectItemCaseSensitive(
        binding, "decision_id");
    const cJSON *actual_decision = cJSON_GetObjectItemCaseSensitive(
        output, "decision_id");
    if (!cJSON_IsString(input_schema) ||
        strcmp(input_schema->valuestring, SCORING_INPUT_SCHEMA) != 0 ||
        !cJSON_IsString(output_schema) ||
        strcmp(output_schema->valuestring, SCORING_OUTPUT_SCHEMA) != 0) {
        fail(error, error_size, "schema_version", SIZE_MAX);
        return false;
    }
    const char *root_missing = object_missing_field(
        output, root_fields,
        sizeof(root_fields) / sizeof(root_fields[0]));
    const char *root_unexpected = object_unexpected_field(
        output, root_fields,
        sizeof(root_fields) / sizeof(root_fields[0]));
    if (!object_has_exact_fields(
            output, root_fields,
            sizeof(root_fields) / sizeof(root_fields[0]))) {
        if (error != NULL && error_size > 0U) {
            snprintf(error, error_size, "root.fields.%s:%s",
                     root_missing != NULL ? "missing" : "forbidden",
                     root_missing != NULL ? root_missing :
                        (root_unexpected != NULL
                            ? root_unexpected : "unknown"));
        }
        return false;
    }
    if (!cJSON_IsString(expected_decision) ||
        !cJSON_IsString(actual_decision) ||
        strcmp(expected_decision->valuestring,
               actual_decision->valuestring) != 0) {
        fail(error, error_size, "decision_id", SIZE_MAX);
        return false;
    }
    static const char *const advice_fields[] = {"direction", "evidence"};
    const cJSON *advice_basis = cJSON_GetObjectItemCaseSensitive(
        output, "advice_basis");
    const cJSON *direction = json_field(advice_basis, "direction");
    const cJSON *evidence = json_field(advice_basis, "evidence");
    const cJSON *patient_condition = cJSON_GetObjectItemCaseSensitive(
        input, "patient_condition");
    const cJSON *advice_text = json_field(patient_condition, "raw_text");
    if (!object_has_exact_fields(advice_basis, advice_fields, 2U) ||
        !cJSON_IsString(direction) || direction->valuestring == NULL ||
        (strcmp(direction->valuestring, "cup") != 0 &&
         strcmp(direction->valuestring, "ball") != 0 &&
         strcmp(direction->valuestring, "neutral") != 0 &&
         strcmp(direction->valuestring, "mixed") != 0) ||
        !cJSON_IsString(evidence) || evidence->valuestring == NULL ||
        !presentation_text_valid(evidence->valuestring,
                                 SCORING_EVIDENCE_MAX) ||
        !cJSON_IsString(advice_text) || advice_text->valuestring == NULL) {
        fail(error, error_size, "advice_basis.shape", SIZE_MAX);
        return false;
    }
    const cJSON *input_candidates = cJSON_GetObjectItemCaseSensitive(
        input, "candidates");
    cJSON *candidates = cJSON_GetObjectItemCaseSensitive(output, "candidates");
    const cJSON *summary = cJSON_GetObjectItemCaseSensitive(output, "summary");
    if (!cJSON_IsArray(input_candidates) ||
        cJSON_GetArraySize(input_candidates) != (int)count ||
        !cJSON_IsArray(candidates) ||
        cJSON_GetArraySize(candidates) != (int)count ||
        !cJSON_IsString(summary) || summary->valuestring == NULL ||
        !presentation_text_valid(summary->valuestring,
                                 SCORING_SUMMARY_MAX)) {
        fail(error, error_size, "candidate_count_or_summary", SIZE_MAX);
        return false;
    }

    bool seen[REHAB_TASK_POLICY_TASK_COUNT] = {0};
    bool available[REHAB_TASK_POLICY_TASK_COUNT] = {0};
    cJSON *candidate = NULL;
    size_t output_index = 0U;
    cJSON_ArrayForEach(candidate, candidates) {
        size_t candidate_ref = SIZE_MAX;
        const char *candidate_missing = object_missing_field(
            candidate, candidate_fields,
            sizeof(candidate_fields) / sizeof(candidate_fields[0]));
        const char *candidate_unexpected = object_unexpected_field(
            candidate, candidate_fields,
            sizeof(candidate_fields) / sizeof(candidate_fields[0]));
        if (!object_has_exact_fields(
                candidate, candidate_fields,
                sizeof(candidate_fields) / sizeof(candidate_fields[0]))) {
            char detail[128] = {0};
            snprintf(detail, sizeof(detail), "fields.%s:%s",
                     candidate_missing != NULL ? "missing" : "forbidden",
                     candidate_missing != NULL ? candidate_missing :
                        (candidate_unexpected != NULL
                            ? candidate_unexpected : "unknown"));
            fail(error, error_size, detail, output_index);
            return false;
        }
        if (!integer_ref(cJSON_GetObjectItemCaseSensitive(
                             candidate, "candidate_ref"),
                         count, &candidate_ref)) {
            fail(error, error_size, "candidate_ref.unknown", output_index);
            return false;
        }
        if (seen[candidate_ref]) {
            fail(error, error_size, "candidate_ref.duplicate", candidate_ref);
            return false;
        }
        if (candidate_ref != output_index) {
            fail(error, error_size, "candidate_ref.reordered", candidate_ref);
            return false;
        }
        seen[candidate_ref] = true;
        const cJSON *candidate_in = input_candidate_by_ref(input, candidate_ref);
        const cJSON *expected_availability = cJSON_GetObjectItemCaseSensitive(
            candidate_in, "availability");
        const cJSON *actual_availability = cJSON_GetObjectItemCaseSensitive(
            candidate, "availability");
        const cJSON *reason = cJSON_GetObjectItemCaseSensitive(
            candidate, "reason");
        const cJSON *score = cJSON_GetObjectItemCaseSensitive(
            candidate, "score");
        const cJSON *candidate_evidence = cJSON_GetObjectItemCaseSensitive(
            candidate, "advice_evidence");
        const bool input_blocked = cJSON_IsString(expected_availability) &&
            expected_availability->valuestring != NULL &&
            strcmp(expected_availability->valuestring, "blocked") == 0;
        if (candidate_in == NULL || !cJSON_IsString(expected_availability) ||
            !cJSON_IsString(actual_availability) ||
            (input_blocked && strcmp(actual_availability->valuestring,
                                     "blocked") != 0) ||
            (strcmp(actual_availability->valuestring, "available") != 0 &&
             strcmp(actual_availability->valuestring, "blocked") != 0)) {
            fail(error, error_size, "availability", candidate_ref);
            return false;
        }
        if (!cJSON_IsString(reason) || reason->valuestring == NULL ||
            !presentation_text_valid(reason->valuestring,
                                     SCORING_REASON_MAX) ||
            !cJSON_IsString(candidate_evidence) ||
            candidate_evidence->valuestring == NULL ||
            !presentation_text_valid(candidate_evidence->valuestring,
                                     SCORING_EVIDENCE_MAX)) {
            fail(error, error_size, "reason", candidate_ref);
            return false;
        }
        if (strcmp(actual_availability->valuestring, "blocked") == 0) {
            if (!number_near(score, 0.0, 0.000001) ||
                !cJSON_IsNull(cJSON_GetObjectItemCaseSensitive(
                    candidate, "score_breakdown"))) {
                fail(error, error_size, "blocked_contract", candidate_ref);
                return false;
            }
            if (!input_blocked && !hard_block_evidence_has_provenance(
                    advice_text->valuestring,
                    candidate_evidence->valuestring)) {
                fail(error, error_size, "medical_block.provenance",
                     candidate_ref);
                return false;
            }
            available[candidate_ref] = false;
        } else {
            if (!validate_available_breakdown(
                    input, candidate, candidate_ref, error, error_size)) {
                return false;
            }
            available[candidate_ref] = true;
        }
        ++output_index;
    }
    for (size_t i = 0; i < count; ++i) {
        if (!seen[i] || input_candidate_by_ref(input, i) == NULL) {
            fail(error, error_size, "candidate_ref.missing", i);
            return false;
        }
    }

    size_t winner_ref = SIZE_MAX;
    if (!integer_ref(cJSON_GetObjectItemCaseSensitive(
                         output, "winner_ref"),
                     count, &winner_ref) || !available[winner_ref]) {
        fail(error, error_size, "winner_ref.invalid", SIZE_MAX);
        return false;
    }
    return true;
}
