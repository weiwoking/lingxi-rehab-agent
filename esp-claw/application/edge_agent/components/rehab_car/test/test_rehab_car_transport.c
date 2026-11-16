// SPDX-License-Identifier: Apache-2.0
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "unity.h"

#include "rehab_car.h"

/* Internal parser seam from rehab_car_capabilities.c. */
esp_err_t rehab_car_cap_parse_handheld_mode(
    const char *input_json,
    rehab_car_s3_input_mode_t *mode);

#define FAKE_UART_WRITE_CAPACITY 24
#define FAKE_UART_LINE_CAPACITY  97

static char s_uart_writes[FAKE_UART_WRITE_CAPACITY][FAKE_UART_LINE_CAPACITY];
static size_t s_uart_write_lengths[FAKE_UART_WRITE_CAPACITY];
static size_t s_uart_write_count;
static size_t s_uart_drain_count;
static int s_uart_fail_write_call;
static int s_uart_partial_write_call;
static int s_uart_fail_wait_call;
static int s_uart_wait_count;
static bool s_uart_installed;
static esp_err_t s_uart_install_result;

/* Strong test doubles replace the weak ESP-IDF UART seams in rehab_car.c. */
esp_err_t rehab_car_uart_backend_install(void)
{
    if (s_uart_install_result != ESP_OK) {
        return s_uart_install_result;
    }
    if (s_uart_installed) {
        return ESP_ERR_INVALID_STATE;
    }
    s_uart_installed = true;
    return ESP_OK;
}

esp_err_t rehab_car_uart_backend_uninstall(void)
{
    s_uart_installed = false;
    return ESP_OK;
}

int rehab_car_uart_backend_write(const void *data, size_t length)
{
    size_t call = ++s_uart_write_count;
    if (call <= FAKE_UART_WRITE_CAPACITY) {
        size_t copied = length < FAKE_UART_LINE_CAPACITY
                            ? length : FAKE_UART_LINE_CAPACITY;
        memcpy(s_uart_writes[call - 1U], data, copied);
        s_uart_write_lengths[call - 1U] = copied;
    }
    if (s_uart_fail_write_call == (int)call) {
        return -1;
    }
    if (s_uart_partial_write_call == (int)call && length > 0) {
        return (int)length - 1;
    }
    return (int)length;
}

esp_err_t rehab_car_uart_backend_wait_tx_done(TickType_t ticks_to_wait)
{
    (void)ticks_to_wait;
    int call = ++s_uart_wait_count;
    return s_uart_fail_wait_call == call ? ESP_ERR_TIMEOUT : ESP_OK;
}

void rehab_car_uart_backend_drain_rx(void)
{
    ++s_uart_drain_count;
}

static void fake_uart_reset(void)
{
    memset(s_uart_writes, 0, sizeof(s_uart_writes));
    memset(s_uart_write_lengths, 0, sizeof(s_uart_write_lengths));
    s_uart_write_count = 0;
    s_uart_drain_count = 0;
    s_uart_fail_write_call = 0;
    s_uart_partial_write_call = 0;
    s_uart_fail_wait_call = 0;
    s_uart_wait_count = 0;
    s_uart_installed = false;
    s_uart_install_result = ESP_OK;
}

static void fake_uart_clear_captures(void)
{
    memset(s_uart_writes, 0, sizeof(s_uart_writes));
    memset(s_uart_write_lengths, 0, sizeof(s_uart_write_lengths));
    s_uart_write_count = 0;
    s_uart_drain_count = 0;
    s_uart_fail_write_call = 0;
    s_uart_partial_write_call = 0;
    s_uart_fail_wait_call = 0;
    s_uart_wait_count = 0;
}

static rehab_car_handle_t create_loopback_car(void)
{
    const rehab_car_config_t config = {
        .target_ipv4 = "127.0.0.1",
        .target_port = REHAB_CAR_UDP_PORT,
        .default_transport = REHAB_CAR_TRANSPORT_UDP,
    };
    rehab_car_handle_t car = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_create(&config, &car));
    return car;
}

static rehab_car_config_t uart_loopback_config(void)
{
    rehab_car_config_t config = REHAB_CAR_CONFIG_DEFAULT();
    config.target_ipv4 = "127.0.0.1";
    config.default_transport = REHAB_CAR_TRANSPORT_UART;
    return config;
}

static void assert_uart_line(size_t index, const char *expected)
{
    size_t expected_length = strlen(expected);
    TEST_ASSERT_LESS_THAN(s_uart_write_count, index);
    TEST_ASSERT_EQUAL_UINT(expected_length, s_uart_write_lengths[index]);
    TEST_ASSERT_EQUAL_MEMORY(expected, s_uart_writes[index], expected_length);
}

TEST_CASE("handheld capability mode parser enforces the exact string contract",
          "[rehab_car][capability][s3_mode]")
{
    rehab_car_s3_input_mode_t mode = REHAB_CAR_S3_INPUT_MODE_SERIAL;

    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_cap_parse_handheld_mode(
        "{\"mode\":\"BLE\"}", &mode));
    TEST_ASSERT_EQUAL(REHAB_CAR_S3_INPUT_MODE_BLE, mode);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_cap_parse_handheld_mode(
        "{\"mode\":\"SERIAL\"}", &mode));
    TEST_ASSERT_EQUAL(REHAB_CAR_S3_INPUT_MODE_SERIAL, mode);

    static const char *invalid_inputs[] = {
        "{}",
        "{\"mode\":\"UDP\"}",
        "{\"mode\":true}",
        "{\"mode\":\"ble\"}",
        "{\"mode\":\"BLE\",\"extra\":1}",
    };
    for (size_t i = 0; i < sizeof(invalid_inputs) / sizeof(invalid_inputs[0]); ++i) {
        TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                          rehab_car_cap_parse_handheld_mode(invalid_inputs[i], &mode));
    }
}

TEST_CASE("car transport defaults to UDP with fixed UART configuration",
           "[rehab_car][transport]")
{
    fake_uart_reset();
    const rehab_car_config_t default_config = REHAB_CAR_CONFIG_DEFAULT();
    TEST_ASSERT_EQUAL(REHAB_CAR_TRANSPORT_UDP,
                      default_config.default_transport);
    rehab_car_handle_t car = create_loopback_car();
    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_TRANSPORT_UDP, status.transport);
    TEST_ASSERT_FALSE(status.uart_initialized);
    TEST_ASSERT_EQUAL(1, status.uart_port);
    TEST_ASSERT_EQUAL(46, status.uart_tx_gpio);
    TEST_ASSERT_EQUAL(47, status.uart_rx_gpio);
    TEST_ASSERT_EQUAL(115200, status.uart_baud_rate);
    TEST_ASSERT_EQUAL_STRING("udp", rehab_car_transport_name(status.transport));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("explicit UART create initializes without sending",
          "[rehab_car][transport][uart][create]")
{
    fake_uart_reset();
    const rehab_car_config_t config = uart_loopback_config();
    rehab_car_handle_t car = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_create(&config, &car));
    TEST_ASSERT_NOT_NULL(car);
    TEST_ASSERT_TRUE(s_uart_installed);
    TEST_ASSERT_EQUAL_UINT(0, s_uart_write_count);

    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_TRANSPORT_UART, status.transport);
    TEST_ASSERT_TRUE(status.uart_initialized);
    TEST_ASSERT_FALSE(status.socket_open);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
    TEST_ASSERT_FALSE(s_uart_installed);
}

TEST_CASE("explicit UART create failure returns no handle and no fallback",
          "[rehab_car][transport][uart][create][failure]")
{
    fake_uart_reset();
    s_uart_install_result = ESP_ERR_NO_MEM;
    const rehab_car_config_t config = uart_loopback_config();
    rehab_car_handle_t car = (rehab_car_handle_t)1;
    TEST_ASSERT_EQUAL(ESP_ERR_NO_MEM, rehab_car_create(&config, &car));
    TEST_ASSERT_NULL(car);
    TEST_ASSERT_FALSE(s_uart_installed);
    TEST_ASSERT_EQUAL_UINT(0, s_uart_write_count);
}

TEST_CASE("create rejects invalid default transport",
          "[rehab_car][transport][create][invalid]")
{
    fake_uart_reset();
    rehab_car_config_t config = REHAB_CAR_CONFIG_DEFAULT();
    config.default_transport = (rehab_car_transport_t)99;
    rehab_car_handle_t car = (rehab_car_handle_t)1;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, rehab_car_create(&config, &car));
    TEST_ASSERT_NULL(car);
    TEST_ASSERT_FALSE(s_uart_installed);
    TEST_ASSERT_EQUAL_UINT(0, s_uart_write_count);
}

TEST_CASE("UART create followed by startup STOP writes exactly one line",
          "[rehab_car][transport][uart][startup]")
{
    fake_uart_reset();
    const rehab_car_config_t config = uart_loopback_config();
    rehab_car_handle_t car = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_create(&config, &car));
    TEST_ASSERT_EQUAL_UINT(0, s_uart_write_count);

    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_stop(car));
    TEST_ASSERT_EQUAL_UINT(1, s_uart_write_count);
    assert_uart_line(0, "STOP\n");
    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_TRANSPORT_UART, status.transport);
    TEST_ASSERT_TRUE(status.uart_initialized);
    TEST_ASSERT_EQUAL(ESP_OK, status.last_final_stop_result);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("default UDP keeps legacy payload lengths without LF",
          "[rehab_car][transport][udp][regression]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_send_pad(
        car, 128, 21, 128, 128, "NONE", 0));
    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_TRANSPORT_UDP, status.transport);
    TEST_ASSERT_EQUAL_UINT(strlen("PAD,128,21,128,128,NONE"),
                           status.last_payload_length);
    TEST_ASSERT_EQUAL_INT(strlen("PAD,128,21,128,128,NONE"),
                          status.last_send_result);
    TEST_ASSERT_EQUAL_UINT(0, s_uart_write_count);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_stop(car));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL_UINT(4, status.last_payload_length);
    TEST_ASSERT_EQUAL_INT(4, status.last_send_result);
    TEST_ASSERT_EQUAL_UINT(0, s_uart_write_count);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("UART PAD and STOP append exactly one LF",
          "[rehab_car][transport][uart][payload]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    fake_uart_clear_captures();

    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_send_pad(
        car, 128, 21, 128, 128, "NONE", 0));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_stop(car));
    TEST_ASSERT_EQUAL_UINT(2, s_uart_write_count);
    TEST_ASSERT_EQUAL_UINT(2, s_uart_drain_count);
    assert_uart_line(0, "PAD,128,21,128,128,NONE\n");
    assert_uart_line(1, "STOP\n");
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("UART handheld mode sequence is STOP then MODE and updates requested state",
          "[rehab_car][transport][uart][s3_mode]")
{
    fake_uart_reset();
    const rehab_car_config_t config = uart_loopback_config();
    rehab_car_handle_t car = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_create(&config, &car));

    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_s3_input_mode(
        car, REHAB_CAR_S3_INPUT_MODE_BLE));
    TEST_ASSERT_EQUAL_UINT(2, s_uart_write_count);
    TEST_ASSERT_EQUAL_UINT(2, s_uart_drain_count);
    assert_uart_line(0, "STOP\n");
    assert_uart_line(1, "MODE BLE\n");
    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_S3_INPUT_MODE_BLE,
                      status.requested_s3_input_mode);
    TEST_ASSERT_EQUAL(ESP_OK, status.last_s3_mode_switch_result);
    TEST_ASSERT_EQUAL_STRING("MODE BLE", status.last_command);
    TEST_ASSERT_EQUAL_UINT(strlen("MODE BLE\n"), status.last_payload_length);
    TEST_ASSERT_EQUAL_INT(strlen("MODE BLE\n"), status.last_send_result);

    fake_uart_clear_captures();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_s3_input_mode(
        car, REHAB_CAR_S3_INPUT_MODE_SERIAL));
    TEST_ASSERT_EQUAL_UINT(2, s_uart_write_count);
    assert_uart_line(0, "STOP\n");
    assert_uart_line(1, "MODE SERIAL\n");
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_S3_INPUT_MODE_SERIAL,
                      status.requested_s3_input_mode);
    TEST_ASSERT_EQUAL_STRING("MODE SERIAL", status.last_command);
    TEST_ASSERT_EQUAL_UINT(strlen("MODE SERIAL\n"), status.last_payload_length);
    TEST_ASSERT_EQUAL_INT(strlen("MODE SERIAL\n"), status.last_send_result);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("failed handheld mode writes do not change requested state",
          "[rehab_car][transport][uart][s3_mode][failure]")
{
    fake_uart_reset();
    const rehab_car_config_t config = uart_loopback_config();
    rehab_car_handle_t car = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_create(&config, &car));
    rehab_car_status_t status = {0};

    s_uart_fail_write_call = 1;
    TEST_ASSERT_EQUAL(ESP_FAIL, rehab_car_set_s3_input_mode(
        car, REHAB_CAR_S3_INPUT_MODE_BLE));
    TEST_ASSERT_EQUAL_UINT(1, s_uart_write_count);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_S3_INPUT_MODE_SERIAL,
                      status.requested_s3_input_mode);

    fake_uart_clear_captures();
    s_uart_fail_write_call = 2;
    TEST_ASSERT_EQUAL(ESP_FAIL, rehab_car_set_s3_input_mode(
        car, REHAB_CAR_S3_INPUT_MODE_BLE));
    TEST_ASSERT_EQUAL_UINT(2, s_uart_write_count);
    assert_uart_line(0, "STOP\n");
    assert_uart_line(1, "MODE BLE\n");
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_S3_INPUT_MODE_SERIAL,
                      status.requested_s3_input_mode);
    TEST_ASSERT_EQUAL(ESP_FAIL, status.last_s3_mode_switch_result);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("handheld mode requires idle UART service",
          "[rehab_car][transport][s3_mode][busy]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_set_s3_input_mode(
        car, REHAB_CAR_S3_INPUT_MODE_BLE));
    TEST_ASSERT_EQUAL_UINT(0, s_uart_write_count);

    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    fake_uart_clear_captures();
    rehab_car_control_lease_t lease = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_acquire(
        car, REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW, NULL, NULL, &lease));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_set_s3_input_mode(
        car, REHAB_CAR_S3_INPUT_MODE_BLE));
    TEST_ASSERT_EQUAL_UINT(0, s_uart_write_count);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_release(car, &lease));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("transport switch is STOP first and preserves UDP state",
          "[rehab_car][transport][switch]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_send_pad(
        car, 128, 21, 128, 128, "NONE", 0));
    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_TRUE(status.socket_open);
    TEST_ASSERT_EQUAL_STRING("PAD", status.last_command);

    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_TRANSPORT_UART, status.transport);
    TEST_ASSERT_TRUE(status.uart_initialized);
    TEST_ASSERT_EQUAL_STRING("STOP", status.last_command);
    fake_uart_clear_captures();

    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UDP));
    TEST_ASSERT_EQUAL_UINT(1, s_uart_write_count);
    assert_uart_line(0, "STOP\n");
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_TRANSPORT_UDP, status.transport);
    TEST_ASSERT_TRUE(status.socket_open);
    TEST_ASSERT_TRUE(status.uart_initialized);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("failed UART initialization keeps UDP active after STOP",
          "[rehab_car][transport][switch][failure]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    s_uart_install_result = ESP_ERR_NO_MEM;
    TEST_ASSERT_EQUAL(ESP_ERR_NO_MEM, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_TRANSPORT_UDP, status.transport);
    TEST_ASSERT_FALSE(status.uart_initialized);
    TEST_ASSERT_TRUE(status.socket_open);
    TEST_ASSERT_EQUAL_STRING("STOP", status.last_command);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("owner lease rejects transport switch",
          "[rehab_car][transport][owner]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    rehab_car_control_lease_t lease = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_acquire(
        car, REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW,
        NULL, NULL, &lease));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    TEST_ASSERT_EQUAL_UINT(0, s_uart_write_count);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_release(car, &lease));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

typedef struct {
    rehab_car_handle_t car;
    SemaphoreHandle_t done;
    esp_err_t result;
} motion_task_context_t;

static void run_timed_motion(void *argument)
{
    motion_task_context_t *context = argument;
    context->result = rehab_car_move(
        context->car, REHAB_CAR_DIRECTION_FORWARD, 200);
    xSemaphoreGive(context->done);
    vTaskDelete(NULL);
}

TEST_CASE("active motion rejects transport switch",
          "[rehab_car][transport][motion]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    fake_uart_clear_captures();
    motion_task_context_t context = {
        .car = car,
        .done = xSemaphoreCreateBinary(),
        .result = ESP_FAIL,
    };
    TEST_ASSERT_NOT_NULL(context.done);
    TEST_ASSERT_EQUAL(pdPASS, xTaskCreate(
        run_timed_motion, "car_uart_test", 3072, &context, 4, NULL));

    rehab_car_status_t status = {0};
    for (int attempt = 0; attempt < 20; ++attempt) {
        TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
        if (status.motion_active) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
    TEST_ASSERT_TRUE(status.motion_active);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_set_s3_input_mode(
        car, REHAB_CAR_S3_INPUT_MODE_BLE));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UDP));
    TEST_ASSERT_EQUAL(pdTRUE, xSemaphoreTake(
        context.done, pdMS_TO_TICKS(1000)));
    TEST_ASSERT_EQUAL(ESP_OK, context.result);
    vSemaphoreDelete(context.done);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("active sequence rejects transport switch",
          "[rehab_car][transport][sequence]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    const rehab_car_sequence_step_t step = {
        .direction = REHAB_CAR_DIRECTION_FORWARD,
        .hold_ms = 200,
        .pause_after_ms = 0,
    };
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_sequence_start(car, &step, 1));
    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_TRUE(status.sequence_active);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_set_s3_input_mode(
        car, REHAB_CAR_S3_INPUT_MODE_BLE));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UDP));
    for (int attempt = 0; attempt < 100; ++attempt) {
        TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
        if (!status.sequence_active) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
    TEST_ASSERT_FALSE(status.sequence_active);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("UART write failure attempts exact STOP and reports failure",
          "[rehab_car][transport][uart][failure]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    fake_uart_clear_captures();
    s_uart_fail_write_call = 1;

    TEST_ASSERT_EQUAL(ESP_FAIL, rehab_car_send_pad(
        car, 128, 21, 128, 128, "NONE", 0));
    TEST_ASSERT_EQUAL_UINT(2, s_uart_write_count);
    assert_uart_line(0, "PAD,128,21,128,128,NONE\n");
    assert_uart_line(1, "STOP\n");
    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(ESP_FAIL, status.last_operation_result);
    TEST_ASSERT_EQUAL(ESP_OK, status.last_final_stop_result);
    TEST_ASSERT_NOT_EQUAL(0, status.last_error);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("UART partial write attempts exact STOP and reports failure",
          "[rehab_car][transport][uart][partial]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    fake_uart_clear_captures();
    s_uart_partial_write_call = 1;

    TEST_ASSERT_EQUAL(ESP_FAIL, rehab_car_send_pad(
        car, 128, 21, 128, 128, "NONE", 0));
    TEST_ASSERT_EQUAL_UINT(2, s_uart_write_count);
    assert_uart_line(0, "PAD,128,21,128,128,NONE\n");
    assert_uart_line(1, "STOP\n");
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("UART TX wait failure attempts exact STOP",
          "[rehab_car][transport][uart][timeout]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    fake_uart_clear_captures();
    s_uart_fail_wait_call = 1;

    TEST_ASSERT_EQUAL(ESP_FAIL, rehab_car_send_pad(
        car, 128, 21, 128, 128, "NONE", 0));
    TEST_ASSERT_EQUAL_UINT(2, s_uart_write_count);
    assert_uart_line(0, "PAD,128,21,128,128,NONE\n");
    assert_uart_line(1, "STOP\n");
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("timed UART move repeats PAD and finishes with STOP",
          "[rehab_car][transport][uart][timing]")
{
    fake_uart_reset();
    rehab_car_handle_t car = create_loopback_car();
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_set_transport(
        car, REHAB_CAR_TRANSPORT_UART));
    fake_uart_clear_captures();

    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_move(
        car, REHAB_CAR_DIRECTION_FORWARD, 100));
    TEST_ASSERT_GREATER_OR_EQUAL_UINT(3, s_uart_write_count);
    for (size_t index = 0; index + 1U < s_uart_write_count; ++index) {
        assert_uart_line(index, "PAD,128,21,128,128,NONE\n");
    }
    assert_uart_line(s_uart_write_count - 1U, "STOP\n");
    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_GREATER_OR_EQUAL_UINT(2, status.last_send_count);
    TEST_ASSERT_EQUAL(ESP_OK, status.last_final_stop_result);
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}
