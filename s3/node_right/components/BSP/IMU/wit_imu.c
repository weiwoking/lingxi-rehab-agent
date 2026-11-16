/**
 * @file wit_imu.c
 * @brief 双 WT901C-TTL IMU 的 ESP32-S3 封装层实现
 */

#include "wit_imu.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "WIT_IMU";

/* ============================================================
 * 1. WT901 标准串口帧定义
 * ============================================================
 *
 * 标准帧格式：
 * Byte0  = 0x55
 * Byte1  = 数据类型
 * Byte2~9 = 8 字节数据，小端序
 * Byte10 = 前 10 字节累加和低 8 位
 */

#define WIT_FRAME_HEAD       0x55u
#define WIT_FRAME_LEN        11u

#define WIT_FRAME_ACC        0x51u
#define WIT_FRAME_GYRO       0x52u
#define WIT_FRAME_ANGLE      0x53u
#define WIT_FRAME_MAG        0x54u

#define WIT_AUTOSCAN_WAIT_MS 500

static const uint32_t s_baud_table[] = {
    9600,
    115200,
    4800,
    19200,
    38400,
    57600,
    230400,
    460800,
    921600,
};

/* ============================================================
 * 2. 内部对象
 * ============================================================ */

typedef struct
{
    uint8_t buf[WIT_FRAME_LEN];
    uint8_t index;
} wit_parser_t;

typedef struct
{
    const char *name;
    wit_imu_id_t id;
    uart_port_t uart_port;
    gpio_num_t tx_pin;
    gpio_num_t rx_pin;

    uint32_t current_baud;
    SemaphoreHandle_t lock;
    wit_imu_data_t latest;
    wit_parser_t parser;
} wit_imu_dev_t;

static wit_imu_dev_t s_imu[WIT_IMU_COUNT] = {
    {
        .name = "IMU1",
        .id = WIT_IMU_ID_1,
        .uart_port = WIT_IMU1_UART_PORT,
        .tx_pin = WIT_IMU1_PIN_TX,
        .rx_pin = WIT_IMU1_PIN_RX,
        .current_baud = WIT_IMU_DEFAULT_BAUDRATE,
    },
    {
        .name = "IMU2",
        .id = WIT_IMU_ID_2,
        .uart_port = WIT_IMU2_UART_PORT,
        .tx_pin = WIT_IMU2_PIN_TX,
        .rx_pin = WIT_IMU2_PIN_RX,
        .current_baud = WIT_IMU_DEFAULT_BAUDRATE,
    },
};

/* ============================================================
 * 3. 基础工具函数
 * ============================================================ */

static int16_t wit_i16_le(const uint8_t *p)
{
    return (int16_t)(((uint16_t)p[1] << 8) | (uint16_t)p[0]);
}

static bool wit_frame_checksum_ok(const uint8_t frame[WIT_FRAME_LEN])
{
    uint8_t sum = 0;

    for (int i = 0; i < 10; i++) {
        sum = (uint8_t)(sum + frame[i]);
    }

    return sum == frame[10];
}

static bool wit_id_valid(wit_imu_id_t id)
{
    return id >= WIT_IMU_ID_1 && id < WIT_IMU_COUNT;
}

/* ============================================================
 * 4. 帧解析
 * ============================================================ */

static void wit_handle_frame(wit_imu_dev_t *dev, const uint8_t frame[WIT_FRAME_LEN])
{
    if (dev == NULL || frame == NULL) {
        return;
    }

    wit_imu_data_t temp;

    xSemaphoreTake(dev->lock, portMAX_DELAY);
    temp = dev->latest;
    xSemaphoreGive(dev->lock);

    const uint8_t type = frame[1];
    const int16_t d0 = wit_i16_le(&frame[2]);
    const int16_t d1 = wit_i16_le(&frame[4]);
    const int16_t d2 = wit_i16_le(&frame[6]);
    const int16_t d3 = wit_i16_le(&frame[8]);

    temp.ts_us = esp_timer_get_time();

    switch (type) {
    case WIT_FRAME_ACC:
        temp.acc_x_g = d0 / 32768.0f * 16.0f;
        temp.acc_y_g = d1 / 32768.0f * 16.0f;
        temp.acc_z_g = d2 / 32768.0f * 16.0f;
        temp.temp_c = d3 / 100.0f;
        break;

    case WIT_FRAME_GYRO:
        temp.gyro_x_dps = d0 / 32768.0f * 2000.0f;
        temp.gyro_y_dps = d1 / 32768.0f * 2000.0f;
        temp.gyro_z_dps = d2 / 32768.0f * 2000.0f;
        break;

    case WIT_FRAME_ANGLE:
        temp.angle_x_deg = d0 / 32768.0f * 180.0f;
        temp.angle_y_deg = d1 / 32768.0f * 180.0f;
        temp.angle_z_deg = d2 / 32768.0f * 180.0f;
        temp.valid = 1;
        temp.update_count++;
        break;

    case WIT_FRAME_MAG:
        temp.mag_x = d0;
        temp.mag_y = d1;
        temp.mag_z = d2;
        break;

    default:
        return;
    }

    xSemaphoreTake(dev->lock, portMAX_DELAY);
    dev->latest = temp;
    xSemaphoreGive(dev->lock);
}

static void wit_parser_input_byte(wit_imu_dev_t *dev, uint8_t byte)
{
    wit_parser_t *parser = &dev->parser;

    if (parser->index == 0 && byte != WIT_FRAME_HEAD) {
        return;
    }

    parser->buf[parser->index++] = byte;

    if (parser->index < WIT_FRAME_LEN) {
        return;
    }

    if (wit_frame_checksum_ok(parser->buf)) {
        wit_handle_frame(dev, parser->buf);
    }

    parser->index = 0;
}

/* ============================================================
 * 5. UART 初始化、接收任务、波特率扫描
 * ============================================================ */

static esp_err_t wit_uart_init_one(wit_imu_dev_t *dev)
{
    uart_config_t uart_cfg = {
        .baud_rate = WIT_IMU_DEFAULT_BAUDRATE,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    ESP_RETURN_ON_ERROR(uart_driver_install(dev->uart_port,
                                            WIT_IMU_RX_BUF_SIZE,
                                            0,
                                            0,
                                            NULL,
                                            0),
                        TAG,
                        "%s uart driver install failed",
                        dev->name);

    ESP_RETURN_ON_ERROR(uart_param_config(dev->uart_port, &uart_cfg),
                        TAG,
                        "%s uart param config failed",
                        dev->name);

    ESP_RETURN_ON_ERROR(uart_set_pin(dev->uart_port,
                                     dev->tx_pin,
                                     dev->rx_pin,
                                     UART_PIN_NO_CHANGE,
                                     UART_PIN_NO_CHANGE),
                        TAG,
                        "%s uart set pin failed",
                        dev->name);

    ESP_LOGI(TAG,
             "%s UART init: port=%d, default_baud=%d, tx=%d, rx=%d",
             dev->name,
             dev->uart_port,
             WIT_IMU_DEFAULT_BAUDRATE,
             dev->tx_pin,
             dev->rx_pin);

    return ESP_OK;
}

static void wit_uart_rx_task(void *arg)
{
    wit_imu_dev_t *dev = (wit_imu_dev_t *)arg;
    uint8_t byte = 0;

    while (1) {
        int len = uart_read_bytes(dev->uart_port,
                                  &byte,
                                  1,
                                  portMAX_DELAY);

        if (len == 1) {
            wit_parser_input_byte(dev, byte);
        }
    }
}

static uint32_t wit_get_update_count(wit_imu_dev_t *dev)
{
    uint32_t count = 0;

    xSemaphoreTake(dev->lock, portMAX_DELAY);
    count = dev->latest.update_count;
    xSemaphoreGive(dev->lock);

    return count;
}

static esp_err_t wit_auto_scan_baudrate_one(wit_imu_dev_t *dev)
{
    for (size_t i = 0; i < sizeof(s_baud_table) / sizeof(s_baud_table[0]); i++) {
        uint32_t baud = s_baud_table[i];
        uint32_t before = 0;
        uint32_t after = 0;

        ESP_RETURN_ON_ERROR(uart_set_baudrate(dev->uart_port, baud),
                            TAG,
                            "%s set baudrate failed",
                            dev->name);

        dev->parser.index = 0;
        uart_flush_input(dev->uart_port);

        before = wit_get_update_count(dev);
        vTaskDelay(pdMS_TO_TICKS(WIT_AUTOSCAN_WAIT_MS));
        after = wit_get_update_count(dev);

        if (after > before) {
            dev->current_baud = baud;
            ESP_LOGI(TAG, "%s found at %lu baud", dev->name, (unsigned long)baud);
            return ESP_OK;
        }
    }

    ESP_LOGE(TAG, "%s not found. Check TX/RX/GND and module power.", dev->name);
    return ESP_ERR_NOT_FOUND;
}

/* ============================================================
 * 6. 对外接口
 * ============================================================ */

esp_err_t wit_imu_init(void)
{
    ESP_LOGI(TAG, "Dual WT901 IMU init start");
    ESP_LOGI(TAG, "UART0 is not used for IMU. IMU1 uses UART1, IMU2 uses UART2.");

    /*
     * 1. 创建两个 IMU 的互斥锁
     */
    for (int i = 0; i < WIT_IMU_COUNT; i++) {
        s_imu[i].lock = xSemaphoreCreateMutex();
        if (s_imu[i].lock == NULL) {
            ESP_LOGE(TAG, "%s mutex create failed", s_imu[i].name);
            return ESP_ERR_NO_MEM;
        }
    }

    /*
     * 2. 初始化 UART1 / UART2
     *
     * 注意：
     * - UART0 是烧录和日志串口，不使用；
     * - IMU1 使用 UART1：GPIO4 TX，GPIO5 RX；
     * - IMU2 使用 UART2：GPIO6 TX，GPIO7 RX。
     */
    for (int i = 0; i < WIT_IMU_COUNT; i++) {
        ESP_RETURN_ON_ERROR(wit_uart_init_one(&s_imu[i]),
                            TAG,
                            "%s uart init failed",
                            s_imu[i].name);
    }

    /*
     * 3. 不再自动扫描波特率，直接固定为 9600
     *
     * 原因：
     * - 你之前单 IMU 日志已经证明 WT901 在 9600 baud 被识别；
     * - 自动扫描依赖收到 0x53 角度帧，有时会因为模块输出内容没开全而误判；
     * - 当前目标是封存稳定，不是做花哨的自动识别。
     */
    for (int i = 0; i < WIT_IMU_COUNT; i++) {
        ESP_RETURN_ON_ERROR(uart_set_baudrate(s_imu[i].uart_port, WIT_IMU_DEFAULT_BAUDRATE),
                            TAG,
                            "%s set baudrate failed",
                            s_imu[i].name);

        s_imu[i].current_baud = WIT_IMU_DEFAULT_BAUDRATE;
        s_imu[i].parser.index = 0;
        uart_flush_input(s_imu[i].uart_port);

        ESP_LOGI(TAG,
                 "%s fixed baudrate = %d",
                 s_imu[i].name,
                 WIT_IMU_DEFAULT_BAUDRATE);
    }

    /*
     * 4. 启动两个后台接收任务
     *
     * 注意：
     * - 接收任务启动后会持续解析 0x55 帧；
     * - 是否有效通过 main.c 里面的 IMU1 valid / IMU2 valid 看。
     */
    for (int i = 0; i < WIT_IMU_COUNT; i++) {
        char task_name[16];
        snprintf(task_name, sizeof(task_name), "wit_rx_%d", i + 1);

        BaseType_t task_ret = xTaskCreate(wit_uart_rx_task,
                                          task_name,
                                          WIT_IMU_TASK_STACK_SIZE,
                                          &s_imu[i],
                                          WIT_IMU_TASK_PRIORITY,
                                          NULL);
        if (task_ret != pdPASS) {
            ESP_LOGE(TAG, "%s rx task create failed", s_imu[i].name);
            return ESP_FAIL;
        }

        ESP_LOGI(TAG, "%s rx task started", s_imu[i].name);
    }

    ESP_LOGI(TAG, "Dual WT901 IMU init done");
    return ESP_OK;
}

esp_err_t wit_imu_get_latest_by_id(wit_imu_id_t id, wit_imu_data_t *data)
{
    if (data == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    if (!wit_id_valid(id)) {
        return ESP_ERR_INVALID_ARG;
    }

    wit_imu_dev_t *dev = &s_imu[id];

    xSemaphoreTake(dev->lock, portMAX_DELAY);
    memcpy(data, &dev->latest, sizeof(wit_imu_data_t));
    xSemaphoreGive(dev->lock);

    return ESP_OK;
}

esp_err_t wit_imu_get_latest_pair(wit_imu_pair_data_t *pair)
{
    if (pair == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    ESP_RETURN_ON_ERROR(wit_imu_get_latest_by_id(WIT_IMU_ID_1, &pair->imu[WIT_IMU_ID_1]),
                        TAG,
                        "get IMU1 failed");

    ESP_RETURN_ON_ERROR(wit_imu_get_latest_by_id(WIT_IMU_ID_2, &pair->imu[WIT_IMU_ID_2]),
                        TAG,
                        "get IMU2 failed");

    return ESP_OK;
}

esp_err_t wit_imu_get_latest(wit_imu_data_t *data)
{
    return wit_imu_get_latest_by_id(WIT_IMU_ID_1, data);
}