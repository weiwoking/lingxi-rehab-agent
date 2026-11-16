/**
 * @file main.c
 * @brief ESP32-S3-R 右侧传感器节点。
 *
 * 本节点固定为 node_id=2：
 * - 读取两路 WT901C-TTL IMU；
 * - 不初始化 ADS1292，不发送 EMG 有效位；
 * - 每 10ms 发送一个 sensor_packet_t v5；
 * - 通过 Wi-Fi UDP broadcast 发到 P4 的 5005 端口；
 * - P4 融合层会把本包内的 imu1/imu2 映射成全局 imu3/imu4。
 *
 * 封存版本只整理注释和说明，不改变采样、发包和协议逻辑。
 */

#include <inttypes.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "wit_imu.h"
#include "sensor_packet.h"
#include "rehab_udp_client.h"

static const char *TAG = "S3_NODE2";

/* ============================================================
 * 1. 运行参数
 * ============================================================ */

#define STATUS_PRINT_PERIOD_MS              1000

#define SENSOR_NODE_ID                      2
#define SENSOR_NODE_TYPE                    SENSOR_NODE_TYPE_DUAL_IMU

#define SENSOR_PACKET_RATE_HZ               100
#define SENSOR_STATE_PACKET_PERIOD_MS       (1000 / SENSOR_PACKET_RATE_HZ)

/* ============================================================
 * 2. 工具函数
 * ============================================================ */

static uint16_t sensor_packet_checksum(const sensor_packet_t *pkt)
{
    if (pkt == NULL) {
        return 0;
    }

    const uint8_t *p = (const uint8_t *)pkt;
    size_t len = offsetof(sensor_packet_t, checksum);

    uint32_t sum = 0;

    for (size_t i = 0; i < len; i++) {
        sum += p[i];
    }

    return (uint16_t)(sum & 0xFFFFu);
}

static void sensor_output_packet(const sensor_packet_t *pkt)
{
    if (pkt == NULL) {
        return;
    }

    (void)rehab_udp_client_send_packet(pkt);
}

static void sensor_calc_linear_accel_g(float ax_g, float ay_g, float az_g,
                                       float roll_deg, float pitch_deg,
                                       float *lax_g, float *lay_g, float *laz_g)
{
    const float DEG2RAD = 0.017453292519943295f;

    float roll = roll_deg * DEG2RAD;
    float pitch = pitch_deg * DEG2RAD;

    /*
     * Gravity projection in IMU body frame, unit: g.
     * Current convention:
     * - acc_x/y/z keeps the raw WT901 acceleration including gravity.
     * - lin_acc_x/y/z = acc_x/y/z - estimated gravity component.
     *
     * After flashing, do one static test:
     * if the board is still but lin_acc_z is around +/-2g, reverse the gz sign here.
     */
    float gx = -sinf(pitch);
    float gy =  sinf(roll) * cosf(pitch);
    float gz =  cosf(roll) * cosf(pitch);

    if (lax_g != NULL) {
        *lax_g = ax_g - gx;
    }
    if (lay_g != NULL) {
        *lay_g = ay_g - gy;
    }
    if (laz_g != NULL) {
        *laz_g = az_g - gz;
    }
}

static void sensor_fill_imu_state(sensor_imu_state_t *dst,
                                  const wit_imu_data_t *src,
                                  int64_t packet_ts_us)
{
    if (dst == NULL) {
        return;
    }

    memset(dst, 0, sizeof(sensor_imu_state_t));

    if (src == NULL || src->valid == 0) {
        return;
    }

    dst->ts_us = src->ts_us;
    dst->update_count = src->update_count;

    int64_t age_us = packet_ts_us - src->ts_us;
    if (age_us < 0) {
        age_us = 0;
    }

    int64_t age_ms = age_us / 1000;
    if (age_ms > UINT16_MAX) {
        age_ms = UINT16_MAX;
    }

    dst->age_ms = (uint16_t)age_ms;

    dst->acc_x_g = src->acc_x_g;
    dst->acc_y_g = src->acc_y_g;
    dst->acc_z_g = src->acc_z_g;

    sensor_calc_linear_accel_g(dst->acc_x_g,
                               dst->acc_y_g,
                               dst->acc_z_g,
                               src->angle_x_deg,
                               src->angle_y_deg,
                               &dst->lin_acc_x_g,
                               &dst->lin_acc_y_g,
                               &dst->lin_acc_z_g);

    dst->gyro_x_dps = src->gyro_x_dps;
    dst->gyro_y_dps = src->gyro_y_dps;
    dst->gyro_z_dps = src->gyro_z_dps;

    dst->angle_x_deg = src->angle_x_deg;
    dst->angle_y_deg = src->angle_y_deg;
    dst->angle_z_deg = src->angle_z_deg;
}

/**
 * @brief 构建 S3-R IMU 状态快照包。
 *
 * 注意：
 * - 本节点不填 EMG；
 * - flags 里不会置 SENSOR_FLAG_EMG_VALID；
 * - 包里的 imu1 / imu2 到 P4 后会映射为全局 imu3 / imu4。
 */
static void sensor_build_packet(sensor_packet_t *pkt,
                                uint32_t tx_seq,
                                const wit_imu_pair_data_t *imu_pair)
{
    if (pkt == NULL) {
        return;
    }

    memset(pkt, 0, sizeof(sensor_packet_t));

    int64_t packet_ts_us = esp_timer_get_time();

    pkt->magic = SENSOR_PACKET_MAGIC;
    pkt->version = SENSOR_PACKET_VERSION;
    pkt->packet_size = sizeof(sensor_packet_t);

    pkt->node_id = SENSOR_NODE_ID;
    pkt->node_type = SENSOR_NODE_TYPE;
    pkt->packet_rate_hz = SENSOR_PACKET_RATE_HZ;

    pkt->tx_seq = tx_seq;
    pkt->tx_ts_us = packet_ts_us;

    pkt->flags = 0;

    if (imu_pair != NULL) {
        const wit_imu_data_t *imu1 = &imu_pair->imu[WIT_IMU_ID_1];
        const wit_imu_data_t *imu2 = &imu_pair->imu[WIT_IMU_ID_2];

        if (imu1->valid != 0) {
            pkt->flags |= SENSOR_FLAG_IMU1_VALID;
            sensor_fill_imu_state(&pkt->imu1, imu1, packet_ts_us);
        }

        if (imu2->valid != 0) {
            pkt->flags |= SENSOR_FLAG_IMU2_VALID;
            sensor_fill_imu_state(&pkt->imu2, imu2, packet_ts_us);
        }
    }

    pkt->checksum = sensor_packet_checksum(pkt);
}

static int sensor_imu_age_ms(int64_t now_us, const wit_imu_data_t *imu)
{
    if (imu == NULL || imu->valid == 0) {
        return -1;
    }

    return (int)((now_us - imu->ts_us) / 1000);
}

/* ============================================================
 * 3. 主程序
 * ============================================================ */

void app_main(void)
{
    ESP_LOGI(TAG, "S3 sensor node start, node_id=%u, dual IMU only", SENSOR_NODE_ID);

    /*
     * S3-R 不初始化 ADS1292。
     * 即使没有 ADS 模块，也不会卡在 ads1292_init()。
     */
    ESP_ERROR_CHECK(wit_imu_init());

    ESP_LOGI(TAG, "IMU init done, ADS1292 disabled for node2");

    /*
     * 启动 Wi-Fi STA + UDP broadcast client。
     */
    ESP_ERROR_CHECK(rehab_udp_client_start());

    ESP_LOGI(TAG, "Status print period: %d ms", STATUS_PRINT_PERIOD_MS);
    ESP_LOGI(TAG,
             "Node config: node_id=%u, node_type=%u, packet_rate=%u Hz, period=%d ms",
             SENSOR_NODE_ID,
             SENSOR_NODE_TYPE,
             SENSOR_PACKET_RATE_HZ,
             SENSOR_STATE_PACKET_PERIOD_MS);

    uint32_t tx_seq = 0;
    uint32_t packet_count = 0;

    int64_t last_print_us = esp_timer_get_time();

    wit_imu_pair_data_t last_imu_pair = {0};

    /*
     * 用 FreeRTOS 的周期延时做 100Hz 节拍。
     *
     * 不要用 while + esp_timer_get_time() 空转等时间，否则会占满 CPU0，
     * 导致 IDLE0 没机会运行，从而触发 task watchdog。
     */
    TickType_t last_wake_tick = xTaskGetTickCount();
    TickType_t period_ticks = pdMS_TO_TICKS(SENSOR_STATE_PACKET_PERIOD_MS);

    if (period_ticks < 1) {
        period_ticks = 1;
    }

    while (1) {
        sensor_packet_t pkt = {0};

        ESP_ERROR_CHECK(wit_imu_get_latest_pair(&last_imu_pair));

        sensor_build_packet(&pkt, tx_seq++, &last_imu_pair);
        sensor_output_packet(&pkt);

        packet_count++;

        int64_t now_us = esp_timer_get_time();

        if ((now_us - last_print_us) >= STATUS_PRINT_PERIOD_MS * 1000LL) {
            float dt_s = (now_us - last_print_us) / 1000000.0f;
            float send_rate = packet_count / dt_s;

            const wit_imu_data_t *imu1 = &last_imu_pair.imu[WIT_IMU_ID_1];
            const wit_imu_data_t *imu2 = &last_imu_pair.imu[WIT_IMU_ID_2];

            ESP_LOGI(TAG,
                     "node=%u,type=%u,send_rate=%.1fHz,target=%uHz"
                     ", tx_seq=%" PRIu32
                     ", IMU1 valid=%u, age=%d ms, updates=%" PRIu32
                     ", A1=[%.2f, %.2f, %.2f], G1=[%.2f, %.2f, %.2f]"
                     ", IMU2 valid=%u, age=%d ms, updates=%" PRIu32
                     ", A2=[%.2f, %.2f, %.2f], G2=[%.2f, %.2f, %.2f]",
                     SENSOR_NODE_ID,
                     SENSOR_NODE_TYPE,
                     send_rate,
                     SENSOR_PACKET_RATE_HZ,
                     tx_seq,
                     imu1->valid,
                     sensor_imu_age_ms(now_us, imu1),
                     imu1->update_count,
                     imu1->angle_x_deg,
                     imu1->angle_y_deg,
                     imu1->angle_z_deg,
                     imu1->gyro_x_dps,
                     imu1->gyro_y_dps,
                     imu1->gyro_z_dps,
                     imu2->valid,
                     sensor_imu_age_ms(now_us, imu2),
                     imu2->update_count,
                     imu2->angle_x_deg,
                     imu2->angle_y_deg,
                     imu2->angle_z_deg,
                     imu2->gyro_x_dps,
                     imu2->gyro_y_dps,
                     imu2->gyro_z_dps);

            packet_count = 0;
            last_print_us = now_us;
        }

        vTaskDelayUntil(&last_wake_tick, period_ticks);
    }
}
