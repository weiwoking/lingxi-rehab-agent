// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_policy.h"

#include <math.h>
#include <string.h>

static const rehab_task_policy_definition_t s_tasks[] = {
    {"OT_CUP_TRANSFER_L1", "水杯横向取放训练（一级）", 1U, "cup", "lateral_transfer", "low"},
    {"OT_CUP_DRINK_L2", "水杯拿取与饮水训练（二级）", 2U, "cup", "drink_water", "low"},
    {"OT_CUP_DRINK_L3", "水杯拿取与饮水训练（三级）", 3U, "cup", "drink_water", "high"},
    {"OT_BALL_TRANSFER_L1", "球类横向取放训练（一级）", 1U, "ball", "lateral_transfer", "low"},
    {"OT_BALL_TRANSFER_L2", "球类横向取放训练（二级）", 2U, "ball", "lateral_transfer", "high"},
};

/* Single production registry used by prompt construction and validation. */
static const rehab_task_scoring_policy_t s_scoring = {
    .difficulty_max = 35.0,
    .difficulty_easier_step = 8.0,
    .difficulty_harder_step = 21.0,
    .advice_match = 25.0,
    .advice_neutral = 20.0,
    .advice_mismatch = 16.0,
    .compensation_max = 20.0,
    .compensation_severity_1 = 0.30,
    .compensation_severity_2 = 0.90,
    .compensation_severity_3_plus = 1.00,
    .relatedness_exact_task = 1.00,
    .relatedness_same_action_family = 0.80,
    .relatedness_same_object_group = 0.60,
    .frequency_0 = 10.0,
    .frequency_1 = 6.0,
    .frequency_2 = 5.0,
    .frequency_3 = 0.0,
    .perception_min = 6.0,
    .perception_span = 4.0,
    .perception_geometry_weight = 0.50,
    .perception_detection_weight = 0.30,
    .perception_height_weight = 0.20,
    .fallback_detection_weight = 0.60,
    .fallback_height_weight = 0.40,
    .validation_tolerance = 0.01,
    .score_decimal_places = 3U,
};

size_t rehab_task_policy_task_count(void)
{
    return sizeof(s_tasks) / sizeof(s_tasks[0]);
}

const rehab_task_policy_definition_t *rehab_task_policy_task_at(size_t index)
{
    return index < rehab_task_policy_task_count() ? &s_tasks[index] : NULL;
}

const rehab_task_policy_definition_t *rehab_task_policy_find(
    const char *task_id)
{
    if (task_id == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < rehab_task_policy_task_count(); ++i) {
        if (strcmp(task_id, s_tasks[i].task_id) == 0) {
            return &s_tasks[i];
        }
    }
    return NULL;
}

const rehab_task_scoring_policy_t *rehab_task_scoring_policy(void)
{
    return &s_scoring;
}

double rehab_task_policy_round_score(double value)
{
    return round(value * 1000.0) / 1000.0;
}

static size_t trim_line(const char **line, size_t length)
{
    const char *start = *line;
    while (length > 0U) {
        if (*start == ' ' || *start == '\t') {
            ++start;
            --length;
        } else if (length >= 3U &&
                   memcmp(start, "　", strlen("　")) == 0) {
            start += strlen("　");
            length -= strlen("　");
        } else {
            break;
        }
    }
    while (length > 0U) {
        if (start[length - 1U] == ' ' || start[length - 1U] == '\t') {
            --length;
        } else if (length >= 3U &&
                   memcmp(start + length - strlen("　"),
                          "　", strlen("　")) == 0) {
            length -= strlen("　");
        } else {
            break;
        }
    }
    *line = start;
    return length;
}

static size_t consume_space(const char *text, size_t length)
{
    size_t used = 0U;
    while (used < length) {
        if (text[used] == ' ' || text[used] == '\t') {
            ++used;
        } else if (length - used >= strlen("　") &&
                   memcmp(text + used, "　", strlen("　")) == 0) {
            used += strlen("　");
        } else {
            break;
        }
    }
    return used;
}

static bool strip_optional_terminal_punctuation(const char *line,
                                                size_t *length)
{
    static const char *const punctuation[] = {"。", ".", "；", ";"};
    if (line == NULL || length == NULL) return false;
    for (size_t i = 0U; i < sizeof(punctuation) / sizeof(punctuation[0]); ++i) {
        size_t size = strlen(punctuation[i]);
        if (*length >= size &&
            memcmp(line + *length - size, punctuation[i], size) == 0) {
            *length -= size;
            return true;
        }
    }
    return false;
}

static int exact_ability_level(const char *line, size_t length)
{
    static const char label[] = "当前任务能力等级";
    static const struct {
        const char *value;
        int level;
    } values[] = {
        {"一级", 1}, {"二级", 2}, {"三级", 3},
    };
    if (line == NULL) return 0;
    (void)strip_optional_terminal_punctuation(line, &length);
    const char *trimmed = line;
    length = trim_line(&trimmed, length);
    if (length < strlen(label) ||
        memcmp(trimmed, label, strlen(label)) != 0) {
        return 0;
    }
    size_t offset = strlen(label);
    offset += consume_space(trimmed + offset, length - offset);
    if (offset >= length) return 0;
    if (trimmed[offset] == ':') {
        ++offset;
    } else if (length - offset >= strlen("：") &&
               memcmp(trimmed + offset, "：", strlen("：")) == 0) {
        offset += strlen("：");
    } else {
        return 0;
    }
    offset += consume_space(trimmed + offset, length - offset);
    for (size_t i = 0; i < sizeof(values) / sizeof(values[0]); ++i) {
        if (length - offset == strlen(values[i].value) &&
            memcmp(trimmed + offset, values[i].value,
                   strlen(values[i].value)) == 0) {
            return values[i].level;
        }
    }
    return 0;
}

bool rehab_task_policy_line_is_exact_ability(const char *line,
                                              size_t length)
{
    if (line == NULL) return false;
    if (length > 0U && line[length - 1U] == '\r') --length;
    length = trim_line(&line, length);
    return exact_ability_level(line, length) != 0;
}

const char *rehab_task_policy_ability_status_name(
    rehab_task_ability_parse_status_t status)
{
    switch (status) {
        case REHAB_TASK_ABILITY_PARSED: return "parsed";
        case REHAB_TASK_ABILITY_DUPLICATE: return "duplicate";
        case REHAB_TASK_ABILITY_INVALID: return "invalid";
        case REHAB_TASK_ABILITY_MISSING:
        default: return "missing";
    }
}

int rehab_task_policy_parse_ability_line(
    const char *text,
    rehab_task_ability_parse_status_t *out_status,
    char *matched_line,
    size_t matched_line_size)
{
    static const char label[] = "当前任务能力等级";
    rehab_task_ability_parse_status_t status = REHAB_TASK_ABILITY_MISSING;
    int matched_level = 1;
    unsigned exact_count = 0U;
    bool invalid_candidate = false;
    if (matched_line != NULL && matched_line_size > 0U) matched_line[0] = '\0';
    if (text != NULL) {
        const char *cursor = text;
        if (strncmp(cursor, "\xEF\xBB\xBF", 3U) == 0) cursor += 3U;
        while (*cursor != '\0') {
            const char *end = strchr(cursor, '\n');
            size_t length = end != NULL ? (size_t)(end - cursor)
                                        : strlen(cursor);
            if (length > 0U && cursor[length - 1U] == '\r') --length;
            const char *trimmed = cursor;
            length = trim_line(&trimmed, length);
            int level = exact_ability_level(trimmed, length);
            if (level != 0) {
                ++exact_count;
                if (exact_count == 1U) {
                    matched_level = level;
                    if (matched_line != NULL && matched_line_size > 0U) {
                        size_t copy = length < matched_line_size - 1U
                            ? length : matched_line_size - 1U;
                        memcpy(matched_line, trimmed, copy);
                        matched_line[copy] = '\0';
                    }
                }
            } else if (length >= strlen(label) &&
                       memcmp(trimmed, label, strlen(label)) == 0) {
                invalid_candidate = true;
            }
            cursor = end != NULL ? end + 1 : cursor + strlen(cursor);
        }
    }
    if (exact_count > 1U) {
        status = REHAB_TASK_ABILITY_DUPLICATE;
        matched_level = 1;
        if (matched_line != NULL && matched_line_size > 0U) {
            matched_line[0] = '\0';
        }
    } else if (exact_count == 1U) {
        status = REHAB_TASK_ABILITY_PARSED;
    } else if (invalid_candidate) {
        status = REHAB_TASK_ABILITY_INVALID;
    }
    if (out_status != NULL) *out_status = status;
    return matched_level;
}

int rehab_task_policy_parse_ability(const char *text, bool *out_parsed)
{
    rehab_task_ability_parse_status_t status = REHAB_TASK_ABILITY_MISSING;
    int level = rehab_task_policy_parse_ability_line(
        text, &status, NULL, 0U);
    if (out_parsed != NULL) {
        *out_parsed = status == REHAB_TASK_ABILITY_PARSED;
    }
    return level;
}


const char *rehab_task_policy_semantic_group(const char *class_name)
{
    if (class_name == NULL) {
        return NULL;
    }
    if (strcmp(class_name, "cup") == 0 ||
        strcmp(class_name, "bottle") == 0) {
        return "cup";
    }
    if (strcmp(class_name, "sports ball") == 0 ||
        strcmp(class_name, "ball") == 0 ||
        strcmp(class_name, "volleyball") == 0) {
        return "ball";
    }
    return NULL;
}

const char *rehab_task_policy_group_zh(const char *semantic_group)
{
    if (semantic_group != NULL && strcmp(semantic_group, "cup") == 0) {
        return "杯子";
    }
    if (semantic_group != NULL && strcmp(semantic_group, "ball") == 0) {
        return "排球";
    }
    return "训练物品";
}

const char *rehab_task_policy_height_plane(double support_height_mm)
{
    if (!isfinite(support_height_mm) || support_height_mm < 550.0) {
        return NULL;
    }
    return support_height_mm <= 890.0 ? "low" : "high";
}

double rehab_task_policy_height_confidence(double support_height_mm,
                                           const char *plane)
{
    const char *actual_plane = rehab_task_policy_height_plane(
        support_height_mm);
    if (actual_plane == NULL || plane == NULL ||
        strcmp(actual_plane, plane) != 0) {
        return 0.0;
    }
    const double center = strcmp(plane, "low") == 0 ? 710.0 : 1070.0;
    double confidence = 1.0 - fabs(support_height_mm - center) / 200.0;
    if (confidence < 0.0) {
        return 0.0;
    }
    return confidence > 1.0 ? 1.0 : confidence;
}

static bool ascii_word_byte(unsigned char value)
{
    return (value >= 'A' && value <= 'Z') ||
        (value >= 'a' && value <= 'z') ||
        (value >= '0' && value <= '9') || value == '_';
}

static bool reason_term_at(const char *text, size_t offset, size_t length,
                           const char *term, bool standalone_word)
{
    const size_t term_length = strlen(term);
    if (offset + term_length > length ||
        memcmp(text + offset, term, term_length) != 0) {
        return false;
    }
    if (!standalone_word) {
        return true;
    }
    const bool left_ok = offset == 0U ||
        !ascii_word_byte((unsigned char)text[offset - 1U]);
    const bool right_ok = offset + term_length == length ||
        !ascii_word_byte((unsigned char)text[offset + term_length]);
    return left_ok && right_ok;
}

bool rehab_task_policy_normalize_reason(const char *text,
                                        char *output,
                                        size_t output_size,
                                        bool *out_changed)
{
    static const struct {
        const char *term;
        const char *replacement;
        bool standalone_word;
    } replacements[] = {
        {"sports ball", "排球", false},
        {"volleyball", "排球", false},
        {"水瓶", "杯子", false},
        {"瓶子", "杯子", false},
        {"ball", "排球", true},
    };
    if (text == NULL || output == NULL || output_size == 0U ||
        out_changed == NULL) {
        return false;
    }
    const size_t length = strlen(text);
    size_t input_offset = 0U;
    size_t output_offset = 0U;
    *out_changed = false;
    while (input_offset < length) {
        const char *replacement = NULL;
        size_t consumed = 0U;
        for (size_t i = 0U;
             i < sizeof(replacements) / sizeof(replacements[0]); ++i) {
            if (reason_term_at(text, input_offset, length,
                               replacements[i].term,
                               replacements[i].standalone_word)) {
                replacement = replacements[i].replacement;
                consumed = strlen(replacements[i].term);
                break;
            }
        }
        if (replacement == NULL) {
            if (output_offset + 1U >= output_size) {
                return false;
            }
            output[output_offset++] = text[input_offset++];
            continue;
        }
        const size_t replacement_length = strlen(replacement);
        if (output_offset + replacement_length >= output_size) {
            return false;
        }
        memcpy(output + output_offset, replacement, replacement_length);
        output_offset += replacement_length;
        input_offset += consumed;
        *out_changed = true;
    }
    output[output_offset] = '\0';
    return true;
}
