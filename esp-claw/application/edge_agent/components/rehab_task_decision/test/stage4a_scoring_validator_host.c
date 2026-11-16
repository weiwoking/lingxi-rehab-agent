// SPDX-License-Identifier: Apache-2.0
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "rehab_task_decision_scoring.h"

bool rehab_task_decision_text_valid(const char *text, size_t max_bytes,
                                    size_t max_codepoints, bool allow_empty)
{
    (void)max_codepoints;
    if (text == NULL) return false;
    size_t length = strlen(text);
    return length <= max_bytes && (allow_empty || length > 0U);
}

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

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    char *input_text = read_file(argv[1]);
    char *output_text = read_file(argv[2]);
    cJSON *input = cJSON_Parse(input_text);
    cJSON *output = cJSON_Parse(output_text);
    char error[2048] = {0};
    bool valid = rehab_task_scoring_validate(
        input, output, error, sizeof(error));
    printf("%s%s%s\n", valid ? "VALID" : "INVALID",
           error[0] != '\0' ? ":" : "", error);
    cJSON_Delete(input);
    cJSON_Delete(output);
    free(input_text);
    free(output_text);
    return valid ? 0 : 1;
}
