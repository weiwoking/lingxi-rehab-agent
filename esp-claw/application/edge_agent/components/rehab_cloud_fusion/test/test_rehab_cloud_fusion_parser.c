// SPDX-License-Identifier: Apache-2.0
#include <stdlib.h>
#include <string.h>

#include "unity.h"
#include "../src/rehab_cloud_fusion_priv.h"

static const char SUCCESS_THREE_JSON[] =
    "{\"schema_version\":\"vision_fusion.v1\","
    "\"capture_id\":\"CAP00000152\",\"status\":\"ok\",\"valid\":true,"
    "\"pairing\":{\"image_timestamp_ms\":1720665012345,"
    "\"tof_timestamp_ms\":1720665012318,\"offset_ms\":27,"
    "\"max_offset_ms\":100},\"detections\":["
    "{\"object_id\":1,\"class_name\":\"cup\",\"confidence\":0.93,"
    "\"bbox_px\":{\"x1\":420,\"y1\":180,\"x2\":510,\"y2\":360},"
    "\"center_normalized\":{\"x\":0.48,\"y\":0.50},"
    "\"horizontal_zone\":\"center\",\"geometry\":{"
    "\"distance_cm\":82.5,\"object_height_cm\":11.4,"
    "\"bottom_height_from_ground_cm\":74.0,\"distance_valid\":true,"
    "\"object_height_valid\":true,\"bottom_height_valid\":true,"
    "\"quality\":0.88,\"tof_zone_ids\":[27,28,35,36]}},"
    "{\"object_id\":2,\"class_name\":\"zxl\",\"confidence\":0.96,"
    "\"bbox_px\":{\"x1\":100,\"y1\":60,\"x2\":360,\"y2\":580},"
    "\"center_normalized\":{\"x\":0.22,\"y\":0.51},"
    "\"horizontal_zone\":\"left\",\"geometry\":{"
    "\"distance_cm\":136.0,\"object_height_cm\":171.0,"
    "\"bottom_height_from_ground_cm\":0.0,\"distance_valid\":true,"
    "\"object_height_valid\":true,\"bottom_height_valid\":true,"
    "\"quality\":0.91,\"tof_zone_ids\":[16,17,24,25,32,33]}},"
    "{\"object_id\":3,\"class_name\":\"person\",\"confidence\":0.88,"
    "\"bbox_px\":{\"x1\":600,\"y1\":80,\"x2\":850,\"y2\":570},"
    "\"center_normalized\":{\"x\":0.76,\"y\":0.50},"
    "\"horizontal_zone\":\"right\",\"geometry\":{"
    "\"distance_cm\":210.0,\"object_height_cm\":168.0,"
    "\"bottom_height_from_ground_cm\":0.0,\"distance_valid\":true,"
    "\"object_height_valid\":true,\"bottom_height_valid\":true,"
    "\"quality\":0.84,\"tof_zone_ids\":[38,39,46,47]}}],"
    "\"summary\":{\"object_count\":3,\"zxl_found\":true,"
    "\"person_count\":1},\"processing_time_ms\":{\"decode\":18,"
    "\"yolo\":96,\"fusion\":12,\"total\":126}}";

static const char INVALID_GEOMETRY_JSON[] =
    "{\"schema_version\":\"vision_fusion.v1\",\"capture_id\":\"c1\","
    "\"status\":\"ok\",\"valid\":true,\"pairing\":{"
    "\"image_timestamp_ms\":1,\"tof_timestamp_ms\":1,\"offset_ms\":0,"
    "\"max_offset_ms\":100},\"detections\":[{\"object_id\":4,"
    "\"class_name\":\"cup\",\"confidence\":0.89,\"bbox_px\":{"
    "\"x1\":10,\"y1\":20,\"x2\":30,\"y2\":40},"
    "\"center_normalized\":{\"x\":0.1,\"y\":0.2},"
    "\"horizontal_zone\":\"left\",\"geometry\":{\"distance_cm\":null,"
    "\"object_height_cm\":null,\"bottom_height_from_ground_cm\":null,"
    "\"distance_valid\":false,\"object_height_valid\":false,"
    "\"bottom_height_valid\":false,\"quality\":0.0,\"tof_zone_ids\":[],"
    "\"invalid_reason\":\"no_valid_tof_zone\"}}],\"summary\":{"
    "\"object_count\":1,\"zxl_found\":false,\"person_count\":0},"
    "\"processing_time_ms\":{\"decode\":1,\"yolo\":2,\"fusion\":3,"
    "\"total\":6}}";

static const char EMPTY_SUCCESS_JSON[] =
    "{\"schema_version\":\"vision_fusion.v1\",\"capture_id\":\"empty\","
    "\"status\":\"ok\",\"valid\":true,\"pairing\":{"
    "\"image_timestamp_ms\":1,\"tof_timestamp_ms\":1,\"offset_ms\":0,"
    "\"max_offset_ms\":100},\"detections\":[],\"summary\":{"
    "\"object_count\":0,\"zxl_found\":false,\"person_count\":0},"
    "\"processing_time_ms\":{\"decode\":1,\"yolo\":2,\"fusion\":3,"
    "\"total\":6}}";

static const char ERROR_JSON[] =
    "{\"schema_version\":\"vision_fusion.v1\","
    "\"capture_id\":\"CAP00000152\",\"status\":\"error\","
    "\"valid\":false,\"error\":{\"code\":\"PAIR_TIME_EXCEEDED\","
    "\"message\":\"Image and ToF timestamps exceed the allowed offset.\","
    "\"retryable\":true}}";

TEST_CASE("vision_fusion v1 parses three formal detections", "[fusion_parser]")
{
    rehab_cloud_fusion_result_t result = {.local_pair_offset_ms = 27};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_cloud_fusion_parse_response(
        SUCCESS_THREE_JSON, "CAP00000152", &result));
    TEST_ASSERT_EQUAL_UINT32(3, result.detection_count);
    TEST_ASSERT_EQUAL_UINT32(3, result.summary_object_count);
    TEST_ASSERT_TRUE(result.detections[0].has_object_id);
    TEST_ASSERT_EQUAL_INT(1, result.detections[0].object_id);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 420.0f,
                             result.detections[0].bbox_px.x1);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.48f,
                             result.detections[0].center_normalized.x);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.88f,
                             result.detections[0].geometry_quality);
    TEST_ASSERT_EQUAL_UINT32(4, result.detections[0].tof_zone_count);
    TEST_ASSERT_EQUAL_UINT8(27, result.detections[0].tof_zone_ids[0]);
}

TEST_CASE("vision_fusion v1 preserves nullable detection geometry",
          "[fusion_parser]")
{
    rehab_cloud_fusion_result_t result = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_cloud_fusion_parse_response(
        INVALID_GEOMETRY_JSON, "c1", &result));
    TEST_ASSERT_FALSE(result.detections[0].has_distance_cm);
    TEST_ASSERT_FALSE(result.detections[0].distance_valid);
    TEST_ASSERT_FALSE(result.detections[0].has_object_height_cm);
    TEST_ASSERT_FALSE(result.detections[0].object_height_valid);
    TEST_ASSERT_EQUAL_UINT32(0, result.detections[0].tof_zone_count);
    TEST_ASSERT_EQUAL_STRING("no_valid_tof_zone",
                             result.detections[0].geometry_invalid_reason);
}

TEST_CASE("vision_fusion v1 accepts empty detections", "[fusion_parser]")
{
    rehab_cloud_fusion_result_t result = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_cloud_fusion_parse_response(
        EMPTY_SUCCESS_JSON, "empty", &result));
    TEST_ASSERT_TRUE(result.cloud_valid);
    TEST_ASSERT_EQUAL_UINT32(0, result.detection_count);
    TEST_ASSERT_EQUAL_UINT32(0, result.summary_object_count);
}

TEST_CASE("vision_fusion v1 exposes formal error object", "[fusion_parser]")
{
    rehab_cloud_fusion_result_t result = {0};
    TEST_ASSERT_EQUAL(ESP_FAIL, rehab_cloud_fusion_parse_response(
        ERROR_JSON, "CAP00000152", &result));
    TEST_ASSERT_TRUE(result.has_error);
    TEST_ASSERT_EQUAL_STRING("PAIR_TIME_EXCEEDED", result.error_code);
    TEST_ASSERT_TRUE(result.error_message[0] != '\0');
    TEST_ASSERT_TRUE(result.error_retryable);
}

static cJSON *make_detection(int object_id)
{
    cJSON *item = cJSON_CreateObject();
    cJSON_AddNumberToObject(item, "object_id", object_id);
    cJSON_AddStringToObject(item, "class_name", "cup");
    cJSON_AddNumberToObject(item, "confidence", 0.9);
    cJSON *bbox = cJSON_AddObjectToObject(item, "bbox_px");
    cJSON_AddNumberToObject(bbox, "x1", 1);
    cJSON_AddNumberToObject(bbox, "y1", 2);
    cJSON_AddNumberToObject(bbox, "x2", 3);
    cJSON_AddNumberToObject(bbox, "y2", 4);
    cJSON *center = cJSON_AddObjectToObject(item, "center_normalized");
    cJSON_AddNumberToObject(center, "x", 0.1);
    cJSON_AddNumberToObject(center, "y", 0.2);
    cJSON_AddStringToObject(item, "horizontal_zone", "left");
    cJSON *geometry = cJSON_AddObjectToObject(item, "geometry");
    cJSON_AddNumberToObject(geometry, "distance_cm", 100.0);
    cJSON_AddNumberToObject(geometry, "object_height_cm", 10.0);
    cJSON_AddNumberToObject(geometry, "bottom_height_from_ground_cm", 70.0);
    cJSON_AddBoolToObject(geometry, "distance_valid", true);
    cJSON_AddBoolToObject(geometry, "object_height_valid", true);
    cJSON_AddBoolToObject(geometry, "bottom_height_valid", true);
    cJSON_AddNumberToObject(geometry, "quality", 0.8);
    cJSON_AddArrayToObject(geometry, "tof_zone_ids");
    return item;
}

TEST_CASE("vision_fusion v1 truncates safely after 32 detections",
          "[fusion_parser]")
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "schema_version", "vision_fusion.v1");
    cJSON_AddStringToObject(root, "capture_id", "many");
    cJSON_AddStringToObject(root, "status", "ok");
    cJSON_AddBoolToObject(root, "valid", true);
    cJSON *pairing = cJSON_AddObjectToObject(root, "pairing");
    cJSON_AddNumberToObject(pairing, "image_timestamp_ms", 1);
    cJSON_AddNumberToObject(pairing, "tof_timestamp_ms", 1);
    cJSON_AddNumberToObject(pairing, "offset_ms", 0);
    cJSON_AddNumberToObject(pairing, "max_offset_ms", 100);
    cJSON *detections = cJSON_AddArrayToObject(root, "detections");
    for (int i = 1; i <= 33; ++i) {
        cJSON_AddItemToArray(detections, make_detection(i));
    }
    cJSON *summary = cJSON_AddObjectToObject(root, "summary");
    cJSON_AddNumberToObject(summary, "object_count", 33);
    cJSON_AddBoolToObject(summary, "zxl_found", false);
    cJSON_AddNumberToObject(summary, "person_count", 0);
    cJSON *timing = cJSON_AddObjectToObject(root, "processing_time_ms");
    cJSON_AddNumberToObject(timing, "decode", 1);
    cJSON_AddNumberToObject(timing, "yolo", 2);
    cJSON_AddNumberToObject(timing, "fusion", 3);
    cJSON_AddNumberToObject(timing, "total", 6);
    char *json = cJSON_PrintUnformatted(root);

    rehab_cloud_fusion_result_t result = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_cloud_fusion_parse_response(
        json, "many", &result));
    TEST_ASSERT_EQUAL_UINT32(33, result.detection_count);
    TEST_ASSERT_TRUE(result.detections_truncated);
    TEST_ASSERT_EQUAL_UINT32(33, result.summary_object_count);
    TEST_ASSERT_EQUAL_INT(32, result.detections[31].object_id);

    free(json);
    cJSON_Delete(root);
}
