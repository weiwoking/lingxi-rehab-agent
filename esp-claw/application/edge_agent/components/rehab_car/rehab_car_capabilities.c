/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "rehab_car.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "claw_cap.h"
#include "esp_log.h"

#define TAG "REHAB_CAR_CAP"

static rehab_car_handle_t s_car;

static esp_err_t car_cap_write(char *output,
                               size_t output_size,
                               const char *format,
                               ...)
{
    if (output == NULL || output_size == 0 || format == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    va_list args;
    va_start(args, format);
    int written = vsnprintf(output, output_size, format, args);
    va_end(args);
    if (written < 0 || (size_t)written >= output_size) {
        output[0] = '\0';
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

static esp_err_t car_cap_error(char *output,
                               size_t output_size,
                               const char *code,
                               esp_err_t err)
{
    esp_err_t render = car_cap_write(output,
                                     output_size,
                                     "{\"accepted\":false,\"code\":\"%s\",\"error\":\"%s\"}",
                                     code,
                                     esp_err_to_name(err));
    return render == ESP_OK ? err : render;
}

static esp_err_t car_cap_parse_empty(const char *input_json)
{
    cJSON *root = cJSON_Parse(input_json ? input_json : "{}");
    bool valid = root != NULL && cJSON_IsObject(root) && cJSON_GetArraySize(root) == 0;
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_ARG;
}

/* Internal parser seam kept non-static for the component Unity contract tests. */
esp_err_t rehab_car_cap_parse_handheld_mode(
    const char *input_json,
    rehab_car_s3_input_mode_t *mode)
{
    if (mode == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = cJSON_Parse(input_json ? input_json : "{}");
    cJSON *item = root ? cJSON_GetObjectItemCaseSensitive(root, "mode") : NULL;
    bool valid = root != NULL && cJSON_IsObject(root) &&
                 cJSON_GetArraySize(root) == 1 && cJSON_IsString(item) &&
                 (strcmp(item->valuestring, "BLE") == 0 ||
                  strcmp(item->valuestring, "SERIAL") == 0);
    if (valid) {
        *mode = strcmp(item->valuestring, "BLE") == 0
            ? REHAB_CAR_S3_INPUT_MODE_BLE
            : REHAB_CAR_S3_INPUT_MODE_SERIAL;
    }
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_ARG;
}

static const char *car_cap_result_code(esp_err_t err)
{
    if (err == ESP_ERR_INVALID_ARG) {
        return "invalid_argument";
    }
    if (err == ESP_ERR_INVALID_STATE) {
        rehab_car_status_t status = {0};
        if (s_car != NULL && rehab_car_get_status(s_car, &status) == ESP_OK &&
            status.control_owner == REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW) {
            return "controlled_by_target_follow";
        }
        if (status.control_owner == REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW) {
            return "controlled_by_person_follow";
        }
        return "busy_or_demo_active";
    }
    return "car_service_error";
}

static esp_err_t car_status_execute(const char *input_json,
                                    const claw_cap_call_context_t *ctx,
                                    char *output,
                                    size_t output_size)
{
    (void)ctx;
    if (car_cap_parse_empty(input_json) != ESP_OK) {
        return car_cap_error(output, output_size, "empty_object_required",
                             ESP_ERR_INVALID_ARG);
    }

    rehab_car_status_t status;
    esp_err_t err = rehab_car_get_status(s_car, &status);
    if (err != ESP_OK) {
        return car_cap_error(output, output_size, "status_unavailable", err);
    }
    return car_cap_write(output,
                         output_size,
                         "{\"schema_version\":\"rehab.car.status.v1\","
                         "\"target_ip\":\"%s\",\"target_port\":%u,\"speed\":%u,"
                         "\"transport\":\"%s\",\"uart_initialized\":%s,"
                         "\"uart_port\":%d,\"uart_tx_gpio\":%d,"
                         "\"uart_rx_gpio\":%d,\"uart_baud_rate\":%d,"
                         "\"requested_mode\":\"%s\","
                         "\"confirmation\":\"local_uart_write_only_no_ack\","
                         "\"last_mode_switch_result\":\"%s\","
                         "\"control_owner\":\"%s\","
                         "\"socket_open\":%s,\"motion_active\":%s,"
                         "\"demo_active\":%s,\"demo_cancel_requested\":%s,"
                         "\"demo_step\":%u,\"demo_step_count\":%u,"
                         "\"last_command\":\"%s\",\"last_error\":%d}",
                         status.target_ipv4,
                         (unsigned)status.target_port,
                         (unsigned)status.speed_level,
                         rehab_car_transport_name(status.transport),
                         status.uart_initialized ? "true" : "false",
                         status.uart_port,
                         status.uart_tx_gpio,
                         status.uart_rx_gpio,
                         status.uart_baud_rate,
                         rehab_car_s3_input_mode_name(status.requested_s3_input_mode),
                         esp_err_to_name(status.last_s3_mode_switch_result),
                         rehab_car_control_owner_name(status.control_owner),
                         status.socket_open ? "true" : "false",
                         status.motion_active ? "true" : "false",
                         status.sequence_active ? "true" : "false",
                         status.sequence_cancel_requested ? "true" : "false",
                         (unsigned)status.sequence_step_index,
                         (unsigned)status.sequence_step_count,
                         status.last_command,
                         status.last_error);
}

static esp_err_t car_handheld_debug_set_execute(
    const char *input_json,
    const claw_cap_call_context_t *ctx,
    char *output,
    size_t output_size)
{
    (void)ctx;
    rehab_car_s3_input_mode_t mode;
    if (rehab_car_cap_parse_handheld_mode(input_json, &mode) != ESP_OK) {
        return car_cap_error(output, output_size, "mode_ble_or_serial_required",
                             ESP_ERR_INVALID_ARG);
    }
    ESP_LOGI(TAG, "handheld_debug requested_mode=%s",
             rehab_car_s3_input_mode_name(mode));
    if (s_car == NULL) {
        return car_cap_error(output, output_size, "car_service_unavailable",
                             ESP_ERR_INVALID_STATE);
    }
    esp_err_t err = rehab_car_set_s3_input_mode(s_car, mode);
    if (err != ESP_OK) {
        rehab_car_status_t status = {0};
        if (rehab_car_get_status(s_car, &status) == ESP_OK &&
            status.transport != REHAB_CAR_TRANSPORT_UART) {
            return car_cap_error(output, output_size, "uart_transport_required", err);
        }
        return car_cap_error(output, output_size, car_cap_result_code(err), err);
    }

    rehab_car_status_t status = {0};
    err = rehab_car_get_status(s_car, &status);
    if (err != ESP_OK) {
        return car_cap_error(output, output_size, "status_readback_failed", err);
    }
    return car_cap_write(output,
                         output_size,
                         "{\"accepted\":true,\"requested_mode\":\"%s\","
                         "\"confirmation\":\"local_uart_write_only_no_ack\","
                         "\"sequence\":[\"STOP\",\"MODE %s\"]}",
                         rehab_car_s3_input_mode_name(status.requested_s3_input_mode),
                         rehab_car_s3_input_mode_name(status.requested_s3_input_mode));
}

static esp_err_t car_stop_execute(const char *input_json,
                                  const claw_cap_call_context_t *ctx,
                                  char *output,
                                  size_t output_size)
{
    (void)ctx;
    if (car_cap_parse_empty(input_json) != ESP_OK) {
        return car_cap_error(output, output_size, "empty_object_required",
                             ESP_ERR_INVALID_ARG);
    }
    esp_err_t err = rehab_car_stop(s_car);
    if (err != ESP_OK) {
        return car_cap_error(output, output_size, "stop_failed", err);
    }
    return car_cap_write(output, output_size,
                         "{\"accepted\":true,\"stopped\":true,\"demo_cancel_requested\":true}");
}

static esp_err_t car_demo_status_execute(const char *input_json,
                                         const claw_cap_call_context_t *ctx,
                                         char *output,
                                         size_t output_size)
{
    (void)ctx;
    if (car_cap_parse_empty(input_json) != ESP_OK) {
        return car_cap_error(output, output_size, "empty_object_required",
                             ESP_ERR_INVALID_ARG);
    }
    rehab_car_status_t status;
    esp_err_t err = rehab_car_get_status(s_car, &status);
    if (err != ESP_OK) {
        return car_cap_error(output, output_size, "status_unavailable", err);
    }
    return car_cap_write(output,
                         output_size,
                         "{\"schema_version\":\"rehab.car.demo_status.v1\","
                         "\"active\":%s,\"cancel_requested\":%s,"
                         "\"step\":%u,\"step_count\":%u}",
                         status.sequence_active ? "true" : "false",
                         status.sequence_cancel_requested ? "true" : "false",
                         (unsigned)status.sequence_step_index,
                         (unsigned)status.sequence_step_count);
}

static esp_err_t car_demo_cancel_execute(const char *input_json,
                                         const claw_cap_call_context_t *ctx,
                                         char *output,
                                         size_t output_size)
{
    (void)ctx;
    if (car_cap_parse_empty(input_json) != ESP_OK) {
        return car_cap_error(output, output_size, "empty_object_required",
                             ESP_ERR_INVALID_ARG);
    }
    esp_err_t err = rehab_car_sequence_cancel(s_car);
    if (err != ESP_OK) {
        return car_cap_error(output, output_size, "cancel_stop_failed", err);
    }
    return car_cap_write(output, output_size,
                         "{\"accepted\":true,\"cancel_requested\":true,\"stop_attempted\":true}");
}

static bool car_cap_json_integer_in_range(const cJSON *item, int minimum, int maximum)
{
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble)) {
        return false;
    }
    double value = item->valuedouble;
    return value >= minimum && value <= maximum && value == (double)(int)value;
}

static bool car_cap_parse_direction(const char *value,
                                    rehab_car_direction_t *direction)
{
    if (value == NULL || direction == NULL) {
        return false;
    }
    if (strcmp(value, "forward") == 0) {
        *direction = REHAB_CAR_DIRECTION_FORWARD;
    } else if (strcmp(value, "backward") == 0) {
        *direction = REHAB_CAR_DIRECTION_BACKWARD;
    } else if (strcmp(value, "strafe_left") == 0) {
        *direction = REHAB_CAR_DIRECTION_STRAFE_LEFT;
    } else if (strcmp(value, "strafe_right") == 0) {
        *direction = REHAB_CAR_DIRECTION_STRAFE_RIGHT;
    } else if (strcmp(value, "rotate_ccw") == 0) {
        *direction = REHAB_CAR_DIRECTION_ROTATE_CCW;
    } else if (strcmp(value, "rotate_cw") == 0) {
        *direction = REHAB_CAR_DIRECTION_ROTATE_CW;
    } else {
        return false;
    }
    return true;
}

static esp_err_t car_move_execute(const char *input_json,
                                  const claw_cap_call_context_t *ctx,
                                  char *output,
                                  size_t output_size)
{
    (void)ctx;
    cJSON *root = cJSON_Parse(input_json ? input_json : "{}");
    cJSON *direction_item = NULL;
    cJSON *speed_item = NULL;
    cJSON *hold_item = NULL;
    size_t direction_count = 0;
    size_t speed_count = 0;
    size_t hold_count = 0;

    if (root == NULL || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return car_cap_error(output, output_size, "invalid_json", ESP_ERR_INVALID_ARG);
    }
    cJSON *field = NULL;
    cJSON_ArrayForEach(field, root) {
        if (field->string == NULL) {
            cJSON_Delete(root);
            return car_cap_error(output, output_size, "unexpected_field", ESP_ERR_INVALID_ARG);
        }
        if (strcmp(field->string, "direction") == 0) {
            direction_item = field;
            ++direction_count;
        } else if (strcmp(field->string, "speed") == 0) {
            speed_item = field;
            ++speed_count;
        } else if (strcmp(field->string, "hold_ms") == 0) {
            hold_item = field;
            ++hold_count;
        } else {
            cJSON_Delete(root);
            return car_cap_error(output, output_size, "unexpected_field", ESP_ERR_INVALID_ARG);
        }
    }

    rehab_car_direction_t direction;
    bool valid = direction_count == 1 && speed_count == 1 && hold_count == 1 &&
                 cJSON_IsString(direction_item) && direction_item->valuestring != NULL &&
                 car_cap_parse_direction(direction_item->valuestring, &direction) &&
                 car_cap_json_integer_in_range(speed_item, 1, 3) &&
                 car_cap_json_integer_in_range(hold_item,
                                               REHAB_CAR_HOLD_MIN_MS,
                                               REHAB_CAR_CAP_HOLD_MAX_MS);
    if (!valid) {
        cJSON_Delete(root);
        return car_cap_error(output, output_size, "invalid_arguments", ESP_ERR_INVALID_ARG);
    }

    uint8_t speed = (uint8_t)speed_item->valueint;
    uint32_t hold_ms = (uint32_t)hold_item->valueint;
    char direction_text[16];
    strlcpy(direction_text, direction_item->valuestring, sizeof(direction_text));
    cJSON_Delete(root);

    esp_err_t err = rehab_car_set_speed(s_car, speed);
    if (err == ESP_OK) {
        err = rehab_car_move(s_car, direction, hold_ms);
    }
    if (err != ESP_OK) {
        return car_cap_error(output, output_size, car_cap_result_code(err), err);
    }
    return car_cap_write(output,
                         output_size,
                         "{\"accepted\":true,\"direction\":\"%s\","
                         "\"speed\":%u,\"hold_ms\":%u,\"final_stop_attempted\":true}",
                         direction_text,
                         (unsigned)speed,
                         (unsigned)hold_ms);
}

/* Safe descriptors may be exposed by explicitly adding this group to visibility. */
static const claw_cap_descriptor_t s_safe_descriptors[] = {
    {
        .id = "car_status",
        .name = "car_status",
        .family = "rehab_car",
        .description = "Read local car-service status without sending UDP.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = car_status_execute,
    },
    {
        .id = "car_stop",
        .name = "car_stop",
        .family = "rehab_car",
        .description = "Immediately cancel a car demo and send exact STOP through the local deterministic service.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM | CLAW_CAP_FLAG_RESTRICTED,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = car_stop_execute,
    },
    {
        .id = "car_demo_status",
        .name = "car_demo_status",
        .family = "rehab_car",
        .description = "Read the bounded manual demo state without sending UDP.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = car_demo_status_execute,
    },
    {
        .id = "car_demo_cancel",
        .name = "car_demo_cancel",
        .family = "rehab_car",
        .description = "Cancel the active manual demo if present and send exact STOP.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_CALLABLE_BY_LLM | CLAW_CAP_FLAG_RESTRICTED,
        .input_schema_json = "{\"type\":\"object\",\"properties\":{},\"additionalProperties\":false}",
        .execute = car_demo_cancel_execute,
    },
};

/* No CALLABLE_BY_LLM flag: Agent callers are rejected even if the group is listed. */
static const claw_cap_descriptor_t s_motion_descriptors[] = {
    {
        .id = "car_move",
        .name = "car_move",
        .family = "rehab_car",
        .description = "Deterministic bounded six-direction car movement for explicit system/console calls only.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_RESTRICTED,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{"
        "\"direction\":{\"type\":\"string\",\"enum\":[\"forward\",\"backward\",\"strafe_left\",\"strafe_right\",\"rotate_ccw\",\"rotate_cw\"]},"
        "\"speed\":{\"type\":\"integer\",\"minimum\":1,\"maximum\":3},"
        "\"hold_ms\":{\"type\":\"integer\",\"minimum\":100,\"maximum\":3000}},"
        "\"required\":[\"direction\",\"speed\",\"hold_ms\"],\"additionalProperties\":false}",
        .execute = car_move_execute,
    },
    {
        .id = "car_handheld_debug_set",
        .name = "car_handheld_debug_set",
        .family = "rehab_car",
        .description = "Locally request BLE handheld debug or restore P4 serial control over the existing UART service.",
        .kind = CLAW_CAP_KIND_CALLABLE,
        .cap_flags = CLAW_CAP_FLAG_RESTRICTED,
        .input_schema_json =
        "{\"type\":\"object\",\"properties\":{"
        "\"mode\":{\"type\":\"string\",\"enum\":[\"BLE\",\"SERIAL\"]}},"
        "\"required\":[\"mode\"],\"additionalProperties\":false}",
        .execute = car_handheld_debug_set_execute,
    },
};

static const claw_cap_group_t s_safe_group = {
    .group_id = "rehab_car_safe",
    .plugin_name = "Rehab Car Safe Controls",
    .version = "1",
    .descriptors = s_safe_descriptors,
    .descriptor_count = sizeof(s_safe_descriptors) / sizeof(s_safe_descriptors[0]),
};

static const claw_cap_group_t s_motion_group = {
    .group_id = "rehab_car_motion",
    .plugin_name = "Rehab Car Motion",
    .version = "1",
    .descriptors = s_motion_descriptors,
    .descriptor_count = sizeof(s_motion_descriptors) / sizeof(s_motion_descriptors[0]),
};

esp_err_t rehab_car_register_capabilities(rehab_car_handle_t handle)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_car != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    s_car = handle;

    esp_err_t err = claw_cap_register_group(&s_safe_group);
    if (err != ESP_OK) {
        s_car = NULL;
        return err;
    }
    err = claw_cap_register_group(&s_motion_group);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "motion capability registration failed after safe group: %s",
                 esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG,
             "registered safe_group=%s count=%u motion_group=%s count=%u "
             "car_move_llm_visible=0 handheld_debug_llm_visible=0",
             s_safe_group.group_id,
             (unsigned)s_safe_group.descriptor_count,
             s_motion_group.group_id,
             (unsigned)s_motion_group.descriptor_count);
    return ESP_OK;
}
