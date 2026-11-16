// SPDX-License-Identifier: Apache-2.0
#include <stdio.h>
#include <string.h>

#include "unity.h"
#include "../src/rehab_person_follow_priv.h"

static const rehab_person_follow_request_meta_t META = {
    .frame_id = "PF-7-1",
    .generation = 7,
    .capture_timestamp_us = 123456,
};

static void response_json(char *output, size_t size, const char *overrides)
{
    snprintf(output, size,
        "{\"schema_version\":\"person_follow.v1\","
        "\"frame_id\":\"PF-7-1\",\"frame_generation\":7,"
        "\"capture_timestamp_us\":123456,"
        "\"capture_clock_domain\":\"esp_timer_monotonic\","
        "\"image_width\":640,\"image_height\":360,"
        "\"target_class\":\"person\",%s}", overrides);
}

TEST_CASE("person follow parser accepts exact echo and person detection",
          "[person_follow][parser]")
{
    char json[768];
    response_json(json, sizeof(json),
        "\"processing_time_ms\":{\"inference\":23.5,\"total\":31.0},"
        "\"target_found\":true,\"detections\":[{"
        "\"class_name\":\"person\",\"confidence\":0.91,"
        "\"bbox_px\":{\"x1\":100,\"y1\":20,\"x2\":300,\"y2\":340},"
        "\"center_normalized\":{\"x\":0.3125,\"y\":0.5},"
        "\"area_normalized\":0.2778}]");
    rehab_person_follow_response_t parsed = {0};
    char error[48];
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_parse_response(
        json, &META, 16, &parsed, error, sizeof(error)));
    TEST_ASSERT_TRUE(parsed.target_found);
    TEST_ASSERT_EQUAL_UINT32(1, parsed.detection_count);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 23.5f, parsed.pc_inference_ms);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 31.0f, parsed.pc_total_ms);
    TEST_ASSERT_EQUAL_STRING("none", error);
}

TEST_CASE("person follow parser accepts no target and multiple people",
          "[person_follow][parser]")
{
    char json[1200];
    rehab_person_follow_response_t parsed = {0};
    char error[48];
    response_json(json, sizeof(json),
        "\"target_found\":false,\"detections\":[]");
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_parse_response(
        json, &META, 16, &parsed, error, sizeof(error)));
    TEST_ASSERT_FALSE(parsed.target_found);
    TEST_ASSERT_EQUAL_UINT32(0, parsed.detection_count);

    const char *two_people =
        "\"target_found\":true,\"detections\":["
        "{\"class_name\":\"person\",\"confidence\":0.8,"
        "\"bbox_px\":{\"x1\":10,\"y1\":10,\"x2\":110,\"y2\":210},"
        "\"center_normalized\":{\"x\":0.1,\"y\":0.3},"
        "\"area_normalized\":0.09},"
        "{\"class_name\":\"person\",\"confidence\":0.9,"
        "\"bbox_px\":{\"x1\":300,\"y1\":20,\"x2\":500,\"y2\":340},"
        "\"center_normalized\":{\"x\":0.625,\"y\":0.5},"
        "\"area_normalized\":0.28}]";
    response_json(json, sizeof(json), two_people);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_person_follow_parse_response(
        json, &META, 16, &parsed, error, sizeof(error)));
    TEST_ASSERT_EQUAL_UINT32(2, parsed.detection_count);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE,
        rehab_person_follow_parse_response(json, &META, 1, &parsed,
                                           error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("too_many_detections", error);
}

TEST_CASE("person follow parser rejects wrong schema and echo fields",
          "[person_follow][parser]")
{
    char json[512];
    rehab_person_follow_response_t parsed = {0};
    char error[48];
    response_json(json, sizeof(json),
        "\"target_found\":false,\"detections\":[]");
    char *schema = strstr(json, "person_follow.v1");
    TEST_ASSERT_NOT_NULL(schema);
    schema[0] = 'x';
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_person_follow_parse_response(json, &META, 16, &parsed,
                                           error, sizeof(error)));

    response_json(json, sizeof(json),
        "\"target_found\":false,\"detections\":[]");
    char *frame = strstr(json, "PF-7-1");
    TEST_ASSERT_NOT_NULL(frame);
    frame[5] = '2';
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_person_follow_parse_response(json, &META, 16, &parsed,
                                           error, sizeof(error)));

    response_json(json, sizeof(json),
        "\"target_found\":false,\"detections\":[]");
    char *generation = strstr(json, "\"frame_generation\":7");
    TEST_ASSERT_NOT_NULL(generation);
    generation[strlen("\"frame_generation\":")] = '8';
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_person_follow_parse_response(json, &META, 16, &parsed,
                                           error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("response_echo_or_schema_invalid", error);
}

TEST_CASE("person follow parser rejects stale echo and target mismatch",
          "[person_follow][parser]")
{
    char json[512];
    response_json(json, sizeof(json),
        "\"target_found\":true,\"detections\":[]");
    rehab_person_follow_response_t parsed = {0};
    char error[48];
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_person_follow_parse_response(json, &META, 16, &parsed,
                                           error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("target_found_mismatch", error);
    char *generation = strstr(json, "\"frame_generation\":7");
    TEST_ASSERT_NOT_NULL(generation);
    generation[strlen("\"frame_generation\":")] = '8';
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_person_follow_parse_response(json, &META, 16, &parsed,
                                           error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("response_echo_or_schema_invalid", error);
}

TEST_CASE("person follow parser rejects invalid confidence and bbox bounds",
          "[person_follow][parser]")
{
    char json[768];
    response_json(json, sizeof(json),
        "\"target_found\":true,\"detections\":[{"
        "\"class_name\":\"person\",\"confidence\":1.2,"
        "\"bbox_px\":{\"x1\":0,\"y1\":0,\"x2\":640,\"y2\":360},"
        "\"center_normalized\":{\"x\":0.5,\"y\":0.5},"
        "\"area_normalized\":1.0}]");
    rehab_person_follow_response_t parsed = {0};
    char error[48];
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_person_follow_parse_response(json, &META, 16, &parsed,
                                           error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("detection_invalid", error);

    response_json(json, sizeof(json),
        "\"target_found\":true,\"detections\":[{"
        "\"class_name\":\"person\",\"confidence\":0.9,"
        "\"bbox_px\":{\"x1\":0,\"y1\":0,\"x2\":641,\"y2\":360},"
        "\"center_normalized\":{\"x\":0.5,\"y\":0.5},"
        "\"area_normalized\":1.0}]");
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_person_follow_parse_response(json, &META, 16, &parsed,
                                           error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("detection_invalid", error);
}

TEST_CASE("person follow parser rejects non-person detection",
          "[person_follow][parser]")
{
    char json[768];
    response_json(json, sizeof(json),
        "\"target_found\":true,\"detections\":[{"
        "\"class_name\":\"cat\",\"confidence\":0.9,"
        "\"bbox_px\":{\"x1\":10,\"y1\":10,\"x2\":100,\"y2\":100},"
        "\"center_normalized\":{\"x\":0.1,\"y\":0.1},"
        "\"area_normalized\":0.04}]");
    rehab_person_follow_response_t parsed = {0};
    char error[48];
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_RESPONSE,
        rehab_person_follow_parse_response(json, &META, 16, &parsed,
                                           error, sizeof(error)));
    TEST_ASSERT_EQUAL_STRING("detection_invalid", error);
}

TEST_CASE("person follow error parser keeps structured PC error code",
          "[person_follow][parser]")
{
    char error[48];
    rehab_person_follow_parse_error_code(
        "{\"schema_version\":\"person_follow_error.v1\","
        "\"error\":{\"code\":\"image_decode_failed\"}}",
        error, sizeof(error));
    TEST_ASSERT_EQUAL_STRING("image_decode_failed", error);
}
