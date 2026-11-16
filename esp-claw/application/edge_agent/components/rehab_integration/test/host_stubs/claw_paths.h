#pragma once

#include <stdio.h>

#include "esp_err.h"

typedef enum {
    CLAW_PATH_DATA = 0,
    CLAW_PATH_SYSTEM,
} claw_path_root_t;

extern const char *rehab_test_data_root;

static inline esp_err_t claw_paths_join(claw_path_root_t root,
                                        const char *subpath,
                                        char *out,
                                        size_t out_size)
{
    if (root != CLAW_PATH_DATA || rehab_test_data_root == NULL || out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    int written = subpath != NULL && subpath[0] != '\0'
                      ? snprintf(out, out_size, "%s/%s", rehab_test_data_root, subpath)
                      : snprintf(out, out_size, "%s", rehab_test_data_root);
    return written > 0 && (size_t)written < out_size
               ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

