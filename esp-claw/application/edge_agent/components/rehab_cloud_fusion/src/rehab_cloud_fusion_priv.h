// SPDX-License-Identifier: Apache-2.0
#pragma once

#include "cJSON.h"
#include "rehab_cloud_fusion.h"

#define REHAB_CLOUD_RESPONSE_INITIAL (8U * 1024U)
#define REHAB_CLOUD_RESPONSE_MAX     (64U * 1024U)

esp_err_t rehab_cloud_fusion_parse_response(
    const char *json,
    const char *expected_capture_id,
    rehab_cloud_fusion_result_t *result);
