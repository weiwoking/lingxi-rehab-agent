// SPDX-License-Identifier: Apache-2.0
#include <stdio.h>
#include <stdlib.h>

#include "cJSON.h"
#include "rehab_task_decision_preparation.h"

static char *read_file(const char *path)
{
    FILE *file = fopen(path, "rb");
    if (file == NULL || fseek(file, 0, SEEK_END) != 0) {
        if (file != NULL) fclose(file);
        return NULL;
    }
    long size = ftell(file);
    if (size < 0 || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return NULL;
    }
    char *buffer = calloc(1U, (size_t)size + 1U);
    if (buffer == NULL || fread(buffer, 1U, (size_t)size, file) !=
            (size_t)size) {
        free(buffer);
        fclose(file);
        return NULL;
    }
    fclose(file);
    return buffer;
}

int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    char *text = read_file(argv[1]);
    cJSON *formal = text != NULL
        ? cJSON_ParseWithOpts(text, NULL, 1) : NULL;
    cJSON *compact = cJSON_CreateObject();
    free(text);
    if (!cJSON_IsObject(formal) || compact == NULL ||
        rehab_task_decision_preparation_add(compact, formal) != ESP_OK) {
        cJSON_Delete(formal);
        cJSON_Delete(compact);
        return 3;
    }
    char *output = cJSON_PrintUnformatted(compact);
    cJSON_Delete(formal);
    cJSON_Delete(compact);
    if (output == NULL) return 4;
    puts(output);
    cJSON_free(output);
    return 0;
}

