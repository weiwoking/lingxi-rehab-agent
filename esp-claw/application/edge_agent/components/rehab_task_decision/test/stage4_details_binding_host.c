// SPDX-License-Identifier: Apache-2.0
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "rehab_task_decision_details_binding.h"

static char *read_file(const char *path)
{
    FILE *file = fopen(path, "rb");
    if (file == NULL) return NULL;
    fseek(file, 0, SEEK_END);
    long length = ftell(file);
    rewind(file);
    char *data = length >= 0 ? calloc(1U, (size_t)length + 1U) : NULL;
    if (data == NULL || fread(data, 1U, (size_t)length, file) !=
        (size_t)length) {
        free(data);
        data = NULL;
    }
    fclose(file);
    return data;
}

static const cJSON *field(const cJSON *object, const char *name)
{
    return cJSON_IsObject(object)
        ? cJSON_GetObjectItemCaseSensitive(object, name) : NULL;
}

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    char *details_text = read_file(argv[1]);
    char *selected_text = read_file(argv[2]);
    cJSON *details = cJSON_Parse(details_text);
    cJSON *selected = cJSON_Parse(selected_text);
    const cJSON *target_id = field(selected, "target_object_id");
    const cJSON *decision_id = field(selected, "decision_id");
    const cJSON *generation = field(selected, "decision_generation");
    const cJSON *scan_id = field(selected, "source_scan_id");
    const cJSON *sha = field(selected, "source_scene_sha256");
    const cJSON *task_id = field(selected, "task_id");
    const cJSON *task_name = field(selected, "task_name");
    const cJSON *reason = field(selected, "reason");
    const cJSON *score = field(selected, "score");
    bool selected_shape = cJSON_IsObject(selected) &&
        cJSON_IsString(decision_id) && cJSON_IsNumber(generation) &&
        cJSON_IsString(scan_id) && cJSON_IsString(sha) &&
        cJSON_IsString(task_id) && cJSON_IsString(task_name) &&
        cJSON_IsString(reason) && cJSON_IsNumber(score) &&
        (cJSON_IsString(target_id) || cJSON_IsNull(target_id));
    rehab_task_details_authority_t authority = {
        .decision_id = selected_shape ? decision_id->valuestring : NULL,
        .generation = selected_shape ? (uint32_t)generation->valuedouble : 0U,
        .source_scan_id = selected_shape ? scan_id->valuestring : NULL,
        .source_scene_sha256 = selected_shape ? sha->valuestring : NULL,
        .task_id = selected_shape ? task_id->valuestring : NULL,
        .task_name = selected_shape ? task_name->valuestring : NULL,
        .reason = selected_shape ? reason->valuestring : NULL,
        .score = selected_shape ? score->valuedouble : 0.0,
        .target_available = selected_shape && cJSON_IsString(target_id),
        .target_object_id = selected_shape && cJSON_IsString(target_id)
            ? target_id->valuestring : NULL,
    };
    bool valid = selected_shape &&
        rehab_task_decision_details_binding_matches(details, &authority);
    puts(valid ? "VALID" : "INVALID");
    cJSON_Delete(details);
    cJSON_Delete(selected);
    free(details_text);
    free(selected_text);
    return valid ? 0 : 1;
}
