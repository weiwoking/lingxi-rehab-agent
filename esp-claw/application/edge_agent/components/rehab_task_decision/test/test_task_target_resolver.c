// SPDX-License-Identifier: Apache-2.0
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "rehab_task_decision_policy.h"
#include "rehab_task_target_resolver.h"

#define CUP_LOW "OT_CUP_TRANSFER_L1"
#define CUP_HIGH "OT_CUP_DRINK_L3"
#define BALL_LOW "OT_BALL_TRANSFER_L1"
#define BALL_HIGH "OT_BALL_TRANSFER_L2"

static void close_to(double actual, double expected)
{
    assert(fabs(actual - expected) < 0.000001);
}

static cJSON *make_object(const char *id, const char *class_name,
                          bool geometry_valid, bool support_present,
                          double support_height_mm, bool full_extent,
                          bool touches_border, double confidence)
{
    cJSON *object = cJSON_CreateObject();
    assert(cJSON_AddStringToObject(object, "object_id", id));
    assert(cJSON_AddStringToObject(object, "class_name", class_name));
    assert(cJSON_AddNumberToObject(object, "confidence", confidence));
    cJSON *geometry = cJSON_AddObjectToObject(object, "geometry");
    assert(geometry);
    assert(cJSON_AddBoolToObject(geometry, "valid", geometry_valid));
    if (support_present) {
        assert(cJSON_AddNumberToObject(
            geometry, "support_height_mm", support_height_mm));
    }
    cJSON *visibility = cJSON_AddObjectToObject(object, "visibility");
    assert(visibility);
    assert(cJSON_AddBoolToObject(
        visibility, "full_extent_visible", full_extent));
    assert(cJSON_AddBoolToObject(
        visibility, "touches_border", touches_border));
    return object;
}

static rehab_task_target_resolution_t resolve(cJSON *objects,
                                                const char *task_id)
{
    rehab_task_target_resolution_t result = {0};
    assert(rehab_task_target_resolve(
        objects, task_id, NULL, "host-test", &result) == ESP_OK);
    return result;
}

static void test_policy(void)
{
    assert(rehab_task_policy_task_count() == 5U);
    bool parsed = false;
    static const struct { const char *text; int level; } cases[] = {
        {"当前任务能力等级：一级", 1},
        {"当前任务能力等级：二级", 2},
        {"当前任务能力等级：三级", 3},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        assert(rehab_task_policy_parse_ability(
                   cases[i].text, &parsed) == cases[i].level && parsed);
    }
    static const char *invalid_lines[] = {
        "当前任务能力等级：二级。",
        "当前任务能力等级 : 1级",
        "当前任务能力等级: 2级",
        "当前任务能力等级 ： 3级",
    };
    for (size_t i = 0; i < sizeof(invalid_lines) / sizeof(invalid_lines[0]);
         ++i) {
        assert(rehab_task_policy_parse_ability(
                   invalid_lines[i], &parsed) == 1 && !parsed);
    }
    assert(rehab_task_policy_parse_ability("非法", &parsed) == 1 && !parsed);
    assert(strcmp(rehab_task_policy_semantic_group("cup"), "cup") == 0);
    assert(strcmp(rehab_task_policy_semantic_group("bottle"), "cup") == 0);
    assert(strcmp(rehab_task_policy_semantic_group("sports ball"), "ball") == 0);
    assert(strcmp(rehab_task_policy_semantic_group("ball"), "ball") == 0);
    assert(strcmp(rehab_task_policy_semantic_group("volleyball"), "ball") == 0);
    assert(rehab_task_policy_semantic_group("book") == NULL);
    close_to(rehab_task_policy_height_confidence(710.0, "low"), 1.0);
    close_to(rehab_task_policy_height_confidence(1070.0, "high"), 1.0);
    close_to(rehab_task_policy_height_confidence(890.0, "low"), 0.1);
    close_to(rehab_task_policy_height_confidence(900.0, "high"), 0.15);
    assert(strcmp(rehab_task_policy_height_plane(550.0), "low") == 0);
    assert(strcmp(rehab_task_policy_height_plane(880.0), "low") == 0);
    assert(strcmp(rehab_task_policy_height_plane(890.0), "low") == 0);
    assert(strcmp(rehab_task_policy_height_plane(900.0), "high") == 0);
    assert(rehab_task_policy_height_confidence(1270.0, "high") == 0.0);
}

static void test_reason_normalization(void)
{
    char output[385] = {0};
    bool changed = false;
    assert(rehab_task_policy_normalize_reason(
        "检测到瓶子", output, sizeof(output), &changed));
    assert(changed && strcmp(output, "检测到杯子") == 0);
    assert(rehab_task_policy_normalize_reason(
        "volleyball位于低平面", output, sizeof(output), &changed));
    assert(changed && strcmp(output, "排球位于低平面") == 0);
    assert(rehab_task_policy_normalize_reason(
        "sports ball与ball均可用", output, sizeof(output), &changed));
    assert(changed && strcmp(output, "排球与排球均可用") == 0);
    assert(rehab_task_policy_normalize_reason(
        "football位于低平面", output, sizeof(output), &changed));
    assert(!changed && strcmp(output, "football位于低平面") == 0);
    assert(rehab_task_policy_normalize_reason(
        "杯子位于低平面", output, sizeof(output), &changed));
    assert(!changed && strcmp(output, "杯子位于低平面") == 0);
    assert(!rehab_task_policy_normalize_reason(
        "检测到瓶子", output, 4U, &changed));
}

static void test_group_plane_geometry_and_quality(void)
{
    cJSON *objects = cJSON_CreateArray();
    cJSON_AddItemToArray(objects, make_object(
        "cup-low-poor", "cup", true, true, 710.0, false, true, 0.1));
    cJSON_AddItemToArray(objects, make_object(
        "bottle-low-best", "bottle", true, true, 710.0, true, false, 0.9));
    cJSON_AddItemToArray(objects, make_object(
        "cup-high", "cup", true, true, 1070.0, true, false, 0.8));
    cJSON_AddItemToArray(objects, make_object(
        "ball-low", "volleyball", true, true, 710.0, true, false, 0.8));
    cJSON_AddItemToArray(objects, make_object(
        "ball-invalid", "sports ball", false, true, 1070.0, true, false, 1.0));
    cJSON_AddItemToArray(objects, make_object(
        "ball-missing", "ball", true, false, 0.0, true, false, 1.0));

    rehab_task_target_resolution_t cup_low = resolve(objects, CUP_LOW);
    assert(cup_low.semantic_group_count == 3U);
    assert(cup_low.compatible_count == 2U);
    assert(strcmp(cup_low.object_id, "bottle-low-best") == 0);
    assert(strcmp(cJSON_GetObjectItemCaseSensitive(
                      cup_low.object, "class_name")->valuestring,
                  "bottle") == 0);
    rehab_task_target_resolution_t cup_high = resolve(objects, CUP_HIGH);
    assert(strcmp(cup_high.object_id, "cup-high") == 0);
    rehab_task_target_resolution_t ball_low = resolve(objects, BALL_LOW);
    assert(strcmp(ball_low.object_id, "ball-low") == 0);
    rehab_task_target_resolution_t ball_high = resolve(objects, BALL_HIGH);
    assert(ball_high.semantic_group_count == 3U);
    assert(ball_high.compatible_count == 0U);
    assert(ball_high.object == NULL);
    cJSON_Delete(objects);
}

static char *read_file(const char *path)
{
    FILE *file = fopen(path, "rb");
    assert(file);
    assert(fseek(file, 0, SEEK_END) == 0);
    long size = ftell(file);
    assert(size > 0);
    rewind(file);
    char *bytes = malloc((size_t)size + 1U);
    assert(bytes);
    assert(fread(bytes, 1, (size_t)size, file) == (size_t)size);
    bytes[size] = '\0';
    fclose(file);
    return bytes;
}

static void replay_no_ball_scene(const char *path)
{
    char *bytes = read_file(path);
    cJSON *root = cJSON_Parse(bytes);
    free(bytes);
    assert(root);
    cJSON *objects = cJSON_GetObjectItemCaseSensitive(root, "objects");
    rehab_task_target_resolution_t cup_low = resolve(objects, CUP_LOW);
    rehab_task_target_resolution_t cup_high = resolve(objects, CUP_HIGH);
    rehab_task_target_resolution_t ball_low = resolve(objects, BALL_LOW);
    rehab_task_target_resolution_t ball_high = resolve(objects, BALL_HIGH);
    assert(cup_low.object != NULL);
    assert(cup_high.object != NULL);
    assert(ball_low.object == NULL && ball_low.semantic_group_count == 0U);
    assert(ball_high.object == NULL && ball_high.semantic_group_count == 0U);
    puts("NO_BALL_SCENE_REPLAY_PASS");
    cJSON_Delete(root);
}

int main(int argc, char **argv)
{
    test_policy();
    test_reason_normalization();
    test_group_plane_geometry_and_quality();
    if (argc > 1) {
        replay_no_ball_scene(argv[1]);
    }
    puts("TARGET_RESOLVER_HOST_TESTS_PASS");
    return 0;
}
