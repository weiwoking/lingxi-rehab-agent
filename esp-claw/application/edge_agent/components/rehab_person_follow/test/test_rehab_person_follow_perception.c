// SPDX-License-Identifier: Apache-2.0
#include <stdio.h>
#include <string.h>

#include "unity.h"
#include "../src/rehab_person_follow_priv.h"

static rehab_person_follow_handle_t s_active_handle;
static esp_err_t s_camera_result;
static esp_err_t s_http_result;
static bool s_invalid_json;
static bool s_make_stale;
static bool s_make_cancelled;
static bool s_bad_markers;
static int s_release_count;
static int s_http_call_count;

esp_err_t rehab_person_follow_camera_acquire(
    rehab_follow_preview_frame_t *frame, uint32_t timeout_ms)
{
    (void)timeout_ms;
    static const uint8_t jpeg[] = {0xff, 0xd8, 0xff, 0xd9};
    static const uint8_t bad_jpeg[] = {0x00, 0xd8, 0xff, 0x00};
    if (s_camera_result != ESP_OK) {
        return s_camera_result;
    }
    *frame = (rehab_follow_preview_frame_t) {
        .data = s_bad_markers ? bad_jpeg : jpeg,
        .length = sizeof(jpeg),
        .capacity = 64,
        .generation = 99,
        .timestamp_us = 123456,
        .lease_token = 1,
    };
    return ESP_OK;
}

void rehab_person_follow_camera_release(rehab_follow_preview_frame_t *frame)
{
    ++s_release_count;
    memset(frame, 0, sizeof(*frame));
}

esp_err_t rehab_person_follow_http_post(
    const char *url, uint32_t timeout_ms, const uint8_t *jpeg,
    size_t jpeg_length, const char *metadata_json, char *response,
    size_t response_capacity, size_t *out_response_length,
    int *out_http_status, uint32_t *out_elapsed_ms)
{
    (void)url;
    (void)timeout_ms;
    ++s_http_call_count;
    TEST_ASSERT_NOT_NULL(jpeg);
    TEST_ASSERT_EQUAL_UINT32(4, jpeg_length);
    TEST_ASSERT_NOT_NULL(strstr(metadata_json, "\"frame_id\":\"PF-7-1\""));
    *out_http_status = 200;
    *out_elapsed_ms = 12;
    if (s_http_result != ESP_OK) {
        *out_response_length = 0;
        return s_http_result;
    }
    const char *body = s_invalid_json ? "{}" :
        "{\"schema_version\":\"person_follow.v1\","
        "\"frame_id\":\"PF-7-1\",\"frame_generation\":7,"
        "\"capture_timestamp_us\":123456,"
        "\"capture_clock_domain\":\"esp_timer_monotonic\","
        "\"image_width\":640,\"image_height\":360,"
        "\"target_class\":\"person\",\"target_found\":false,"
        "\"detections\":[]}";
    TEST_ASSERT_LESS_THAN(response_capacity, strlen(body));
    strlcpy(response, body, response_capacity);
    *out_response_length = strlen(body);
    if (s_make_stale) {
        ++s_active_handle->generation;
    }
    if (s_make_cancelled) {
        s_active_handle->cancel_requested = true;
    }
    return ESP_OK;
}

static rehab_person_follow_handle_t perception_handle(void)
{
    rehab_person_follow_config_t config =
        REHAB_PERSON_FOLLOW_CONFIG_DEFAULT((rehab_car_handle_t)1);
    config.perception_provider = REHAB_PERSON_FOLLOW_PERCEPTION_REAL;
    rehab_person_follow_handle_t handle = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_create(&config, &handle));
    handle->generation = 7;
    s_active_handle = handle;
    s_camera_result = ESP_OK;
    s_http_result = ESP_OK;
    s_invalid_json = false;
    s_make_stale = false;
    s_make_cancelled = false;
    s_bad_markers = false;
    s_release_count = 0;
    s_http_call_count = 0;
    return handle;
}

static void delete_handle(rehab_person_follow_handle_t handle);

TEST_CASE("person follow large buffers are handle owned and multipart length uses actual JPEG",
          "[person_follow][perception][memory]")
{
    rehab_person_follow_handle_t handle = perception_handle();
    TEST_ASSERT_NOT_NULL(handle->response_buffer);
    TEST_ASSERT_NOT_NULL(handle->workspace);
    size_t first = 0;
    size_t second = 0;
    TEST_ASSERT_EQUAL(ESP_OK,
        rehab_person_follow_multipart_content_length(4, 100, &first));
    TEST_ASSERT_EQUAL(ESP_OK,
        rehab_person_follow_multipart_content_length(9, 100, &second));
    TEST_ASSERT_EQUAL_UINT32(5, second - first);
    delete_handle(handle);
}

TEST_CASE("invalid JPEG markers fail before HTTP and release once",
          "[person_follow][perception][jpeg][cleanup]")
{
    rehab_person_follow_handle_t handle = perception_handle();
    rehab_person_follow_perception_result_t result = {0};
    s_bad_markers = true;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_person_follow_perceive(handle, 7, 1, &result));
    TEST_ASSERT_EQUAL_STRING("jpeg_marker_invalid", result.error_code);
    TEST_ASSERT_EQUAL_INT(1, s_release_count);
    TEST_ASSERT_EQUAL_INT(0, s_http_call_count);
    delete_handle(handle);
}

static void delete_handle(rehab_person_follow_handle_t handle)
{
    s_active_handle = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_delete(handle));
}

TEST_CASE("real perception releases JPEG lease exactly once on success",
          "[person_follow][perception][cleanup]")
{
    rehab_person_follow_handle_t handle = perception_handle();
    rehab_person_follow_perception_result_t result = {0};
    TEST_ASSERT_EQUAL(ESP_OK,
        rehab_person_follow_perceive(handle, 7, 1, &result));
    TEST_ASSERT_EQUAL_INT(1, s_release_count);
    TEST_ASSERT_TRUE(result.observation.valid);
    TEST_ASSERT_FALSE(result.observation.target_found);
    TEST_ASSERT_EQUAL_UINT32(12, result.http_ms);
    delete_handle(handle);
}

TEST_CASE("real perception drops cancelled generation after releasing lease",
          "[person_follow][perception][generation]")
{
    rehab_person_follow_handle_t handle = perception_handle();
    rehab_person_follow_perception_result_t result = {0};
    s_make_cancelled = true;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
        rehab_person_follow_perceive(handle, 7, 1, &result));
    TEST_ASSERT_EQUAL_INT(1, s_release_count);
    TEST_ASSERT_EQUAL_STRING("generation_stale", result.error_code);
    handle->cancel_requested = false;
    delete_handle(handle);
}

TEST_CASE("real perception releases JPEG lease on HTTP and parse errors",
          "[person_follow][perception][cleanup]")
{
    rehab_person_follow_handle_t handle = perception_handle();
    rehab_person_follow_perception_result_t result = {0};
    s_http_result = ESP_ERR_TIMEOUT;
    TEST_ASSERT_EQUAL(ESP_ERR_TIMEOUT,
        rehab_person_follow_perceive(handle, 7, 1, &result));
    TEST_ASSERT_EQUAL_INT(1, s_release_count);
    s_http_result = ESP_OK;
    s_invalid_json = true;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_person_follow_perceive(handle, 7, 1, &result));
    TEST_ASSERT_EQUAL_INT(2, s_release_count);
    delete_handle(handle);
}

TEST_CASE("real perception drops stale generation after releasing lease",
          "[person_follow][perception][generation]")
{
    rehab_person_follow_handle_t handle = perception_handle();
    rehab_person_follow_perception_result_t result = {0};
    s_make_stale = true;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
        rehab_person_follow_perceive(handle, 7, 1, &result));
    TEST_ASSERT_EQUAL_INT(1, s_release_count);
    TEST_ASSERT_EQUAL_STRING("generation_stale", result.error_code);
    delete_handle(handle);
}

TEST_CASE("camera acquire failure owns no JPEG lease",
          "[person_follow][perception][cleanup]")
{
    rehab_person_follow_handle_t handle = perception_handle();
    rehab_person_follow_perception_result_t result = {0};
    s_camera_result = ESP_ERR_TIMEOUT;
    TEST_ASSERT_EQUAL(ESP_ERR_TIMEOUT,
        rehab_person_follow_perceive(handle, 7, 1, &result));
    TEST_ASSERT_EQUAL_INT(0, s_release_count);
    delete_handle(handle);
}
