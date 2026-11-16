// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <stdbool.h>
#include <stddef.h>

#define REHAB_TASK_POLICY_TASK_COUNT 5U
#define REHAB_TASK_POLICY_RULES_VERSION \
    "rehab.stage4.final_business_alignment.rules.v4"

typedef struct {
    const char *task_id;
    const char *task_name;
    unsigned level;
    const char *semantic_group;
    const char *action_family;
    const char *plane;
} rehab_task_policy_definition_t;

typedef struct {
    double difficulty_max;
    double difficulty_easier_step;
    double difficulty_harder_step;
    double advice_match;
    double advice_neutral;
    double advice_mismatch;
    double compensation_max;
    double compensation_severity_1;
    double compensation_severity_2;
    double compensation_severity_3_plus;
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
    unsigned score_decimal_places;
} rehab_task_scoring_policy_t;

typedef enum {
    REHAB_TASK_ABILITY_PARSED = 0,
    REHAB_TASK_ABILITY_MISSING,
    REHAB_TASK_ABILITY_DUPLICATE,
    REHAB_TASK_ABILITY_INVALID,
} rehab_task_ability_parse_status_t;

size_t rehab_task_policy_task_count(void);
const rehab_task_policy_definition_t *rehab_task_policy_task_at(size_t index);
const rehab_task_policy_definition_t *rehab_task_policy_find(
    const char *task_id);
const rehab_task_scoring_policy_t *rehab_task_scoring_policy(void);
double rehab_task_policy_round_score(double value);

int rehab_task_policy_parse_ability(const char *text, bool *out_parsed);
int rehab_task_policy_parse_ability_line(
    const char *text,
    rehab_task_ability_parse_status_t *out_status,
    char *matched_line,
    size_t matched_line_size);
const char *rehab_task_policy_ability_status_name(
    rehab_task_ability_parse_status_t status);
bool rehab_task_policy_line_is_exact_ability(const char *line,
                                              size_t length);
const char *rehab_task_policy_semantic_group(const char *class_name);
const char *rehab_task_policy_group_zh(const char *semantic_group);
const char *rehab_task_policy_height_plane(double support_height_mm);
double rehab_task_policy_height_confidence(double support_height_mm,
                                           const char *plane);

bool rehab_task_policy_normalize_reason(const char *text,
                                        char *output,
                                        size_t output_size,
                                        bool *out_changed);
