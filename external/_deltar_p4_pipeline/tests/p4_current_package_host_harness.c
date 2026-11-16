#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

#include "cJSON.h"
#include "rehab_task_target_resolver.h"


static char *read_file(const char *path)
{
    FILE *file = fopen(path, "rb");
    assert(file != NULL);
    assert(fseek(file, 0, SEEK_END) == 0);
    long size = ftell(file);
    assert(size > 0);
    rewind(file);
    char *bytes = malloc((size_t)size + 1U);
    assert(bytes != NULL);
    assert(fread(bytes, 1, (size_t)size, file) == (size_t)size);
    bytes[size] = '\0';
    fclose(file);
    return bytes;
}


int main(int argc, char **argv)
{
    if (argc != 3) {
        return 2;
    }
    char *bytes = read_file(argv[1]);
    cJSON *root = cJSON_Parse(bytes);
    free(bytes);
    assert(root != NULL);
    cJSON *objects = cJSON_GetObjectItemCaseSensitive(root, "objects");
    assert(cJSON_IsArray(objects));

    rehab_task_target_resolution_t result = {0};
    assert(rehab_task_target_resolve(
               objects, argv[2], NULL, "formal-geometry-d-host",
               &result) == ESP_OK);
    assert(result.semantic_group_count > 0U);
    printf(
        "%s semantic=%u compatible=%u selected=%s support_mm=%.3f\n",
        argv[2], (unsigned)result.semantic_group_count,
        (unsigned)result.compatible_count,
        result.object_id != NULL ? result.object_id : "none",
        result.object != NULL ? result.support_height_mm : -1.0);
    cJSON_Delete(root);
    puts("CURRENT_FORMAL_D_PACKAGE_TARGET_RESOLVER_PASS");
    return 0;
}
