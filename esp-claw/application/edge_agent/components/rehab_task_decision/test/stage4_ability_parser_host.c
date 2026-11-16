// SPDX-License-Identifier: Apache-2.0
#include <stdio.h>
#include <stdlib.h>

#include "rehab_task_decision_policy.h"

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
    if (argc != 2) return 2;
    char *text = read_file(argv[1]);
    if (text == NULL) return 3;
    rehab_task_ability_parse_status_t status = REHAB_TASK_ABILITY_MISSING;
    char matched[128] = {0};
    int level = rehab_task_policy_parse_ability_line(
        text, &status, matched, sizeof(matched));
    printf("level=%d\nstatus=%s\nmatched=%s\nraw-begin\n%s\nraw-end\n",
           level, rehab_task_policy_ability_status_name(status), matched,
           text);
    free(text);
    return 0;
}
