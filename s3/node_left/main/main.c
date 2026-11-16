/**
 * @file main.c
 * @brief ESP32-S3-L 左侧传感器节点。
 *
 * 本节点固定为 node_id=1：
 * - 读取两路 WT901C-TTL IMU；
 * - 读取 ADS1292，整理 4 通道 EMG 特征；
 * - ADS1292 作为本地采样主节拍；
 * - 每 10ms 汇总一次 EMG 窗口；
 * - 每 10ms 发送一个 sensor_packet_t v5；
 * - 通过 Wi-Fi UDP broadcast 发到 P4 的 5005 端口。
 *
 * 封存版本只整理注释和说明，不改变采样、发包和协议逻辑。
 */
#include <inttypes.h>
#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "ads1292.h"
#include "wit_imu.h"
#include "sensor_packet.h"
#include "rehab_udp_client.h"

static const char *TAG = "S3_NODE";

/* ============================================================
 * 1. 运行参数
 * ============================================================ */

/** @brief 状态打印周期。不要每帧打印，否则串口会拖慢采样。 */
#define STATUS_PRINT_PERIOD_MS              1000

/**
 * @brief 当前节点配置。
 *
 * S3-L：
 * - node_id = 1
 * - node_type = EMG + 双 IMU
 *
 * S3-R 使用 node_id = 2 和 SENSOR_NODE_TYPE_DUAL_IMU。
 */
#define SENSOR_NODE_ID                      1
#define SENSOR_NODE_TYPE                    SENSOR_NODE_TYPE_EMG_DUAL_IMU

/**
 * @brief S3 状态包发送频率。
 *
 * 当前默认 100Hz，即每 10ms 发送一个状态快照包。
 * ADS1292 本地仍然高速采样，但无线只发送窗口特征。
 */
#define SENSOR_PACKET_RATE_HZ               100
#define SENSOR_STATE_PACKET_PERIOD_US       (1000000LL / SENSOR_PACKET_RATE_HZ)

/* ============================================================
 * 2. 工具函数
 * ============================================================ */

/**
 * @brief 计算 16 位累加和。
 *
 * 当前不是加密校验，只用于 P4 快速发现明显错包。
 */
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

/**
 * @brief 发送一个康复状态包。
 *
 * 注意：
 * - 这里不做限速；
 * - 限速由主循环的 SENSOR_STATE_PACKET_PERIOD_US 控制；
 * - COMM 层内部只保留最新 packet。
 */
static void sensor_output_packet(const sensor_packet_t *pkt)
{
    if (pkt == NULL) {
        return;
    }

    (void)rehab_udp_client_send_packet(pkt);
}

/* ============================================================
 * 3. EMG 窗口统计器
 * ============================================================ */

typedef struct
{
    int64_t window_start_us;
    int64_t window_end_us;

    uint32_t sample_count;

    int32_t latest_ch[4];

    double sum_abs[4];
    double sum_sq[4];

    int32_t peak_abs[4];
} emg_window_acc_t;

static int32_t i32_abs_safe(int32_t x)
{
    if (x == INT32_MIN) {
        return INT32_MAX;
    }

    return (x < 0) ? -x : x;
}

static void emg_window_reset(emg_window_acc_t *acc, int64_t start_us)
{
    if (acc == NULL) {
        return;
    }

    memset(acc, 0, sizeof(emg_window_acc_t));
    acc->window_start_us = start_us;
    acc->window_end_us = start_us;
}

static void emg_window_add_sample(emg_window_acc_t *acc, const ads1292_sample_t *ads)
{
    if (acc == NULL || ads == NULL) {
        return;
    }

    int32_t ch[4] = {
        ads->m1_ch1,
        ads->m1_ch2,
        ads->m2_ch1,
        ads->m2_ch2,
    };

    if (acc->sample_count == 0) {
        acc->window_start_us = ads->ts_us;
    }

    acc->window_end_us = ads->ts_us;
    acc->sample_count++;

    for (int i = 0; i < 4; i++) {
        int32_t abs_v = i32_abs_safe(ch[i]);

        acc->latest_ch[i] = ch[i];
        acc->sum_abs[i] += (double)abs_v;
        acc->sum_sq[i] += (double)ch[i] * (double)ch[i];

        if (abs_v > acc->peak_abs[i]) {
            acc->peak_abs[i] = abs_v;
        }
    }
}

static void emg_window_fill_packet(sensor_emg_state_t *dst, const emg_window_acc_t *acc)
{
    if (dst == NULL || acc == NULL) {
        return;
    }

    memset(dst, 0, sizeof(sensor_emg_state_t));

    dst->window_start_us = acc->window_start_us;
    dst->window_end_us = acc->window_end_us;

    if (acc->sample_count > UINT16_MAX) {
        dst->sample_count = UINT16_MAX;
    } else {
        dst->sample_count = (uint16_t)acc->sample_count;
    }

    dst->latest_ch1 = acc->latest_ch[0];
    dst->latest_ch2 = acc->latest_ch[1];
    dst->latest_ch3 = acc->latest_ch[2];
    dst->latest_ch4 = acc->latest_ch[3];

    dst->peak_abs_ch1 = acc->peak_abs[0];
    dst->peak_abs_ch2 = acc->peak_abs[1];
    dst->peak_abs_ch3 = acc->peak_abs[2];
    dst->peak_abs_ch4 = acc->peak_abs[3];

    if (acc->sample_count == 0) {
        return;
    }

    double n = (double)acc->sample_count;

    dst->mav_ch1 = (float)(acc->sum_abs[0] / n);
    dst->mav_ch2 = (float)(acc->sum_abs[1] / n);
    dst->mav_ch3 = (float)(acc->sum_abs[2] / n);
    dst->mav_ch4 = (float)(acc->sum_abs[3] / n);

    dst->rms_ch1 = (float)sqrt(acc->sum_sq[0] / n);
    dst->rms_ch2 = (float)sqrt(acc->sum_sq[1] / n);
    dst->rms_ch3 = (float)sqrt(acc->sum_sq[2] / n);
    dst->rms_ch4 = (float)sqrt(acc->sum_sq[3] / n);
}

/* ============================================================
 * 4. IMU 状态填充
 * ============================================================ */

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
 * @brief 构建 S3-L 状态快照包。
 *
 * 语义：S3 在 tx_ts_us 时刻对本节点状态拍一张快照。
 * - EMG 是最近一个发送周期的窗口统计；
 * - IMU 是最近一次姿态；
 * - tx_seq 是本节点无线发送序号，不是 ADS 采样序号。
 */
static void sensor_build_packet(sensor_packet_t *pkt,
                                uint32_t tx_seq,
                                const emg_window_acc_t *emg_acc,
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

    if (emg_acc != NULL && emg_acc->sample_count > 0) {
        pkt->flags |= SENSOR_FLAG_EMG_VALID;
        emg_window_fill_packet(&pkt->emg, emg_acc);
    }

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
 * 5. 主程序
 * ============================================================ */

void app_main(void)
{
    ESP_LOGI(TAG, "S3 sensor node start, node_id=%u, EMG + dual IMU", SENSOR_NODE_ID);

    ESP_ERROR_CHECK(wit_imu_init());
    ESP_ERROR_CHECK(ads1292_init());

    ESP_LOGI(TAG, "All sensors init done");

    /*
     * 启动 Wi-Fi STA + UDP broadcast client。
     * 当前连接电脑热点 / 手机热点。
     */
    ESP_ERROR_CHECK(rehab_udp_client_start());

    ESP_LOGI(TAG, "Status print period: %d ms", STATUS_PRINT_PERIOD_MS);
    ESP_LOGI(TAG,
             "Node config: node_id=%u, node_type=%u, packet_rate=%u Hz, period=%lld us",
             SENSOR_NODE_ID,
             SENSOR_NODE_TYPE,
             SENSOR_PACKET_RATE_HZ,
             SENSOR_STATE_PACKET_PERIOD_US);

    uint32_t tx_seq = 0;

    uint32_t ads_ok_count = 0;
    uint32_t ads_timeout_count = 0;
    uint32_t ads_error_count = 0;

    int64_t last_print_us = esp_timer_get_time();
    int64_t last_packet_us = esp_timer_get_time();

    ads1292_sample_t last_ads = {0};
    wit_imu_pair_data_t last_imu_pair = {0};

    emg_window_acc_t emg_acc = {0};
    emg_window_reset(&emg_acc, esp_timer_get_time());

    while (1) {
        ads1292_sample_t ads = {0};

        /*
         * ADS1292 仍然作为本地采样主节拍。
         * 每个 ADS 样本都进入 EMG 窗口统计器。
         */
        esp_err_t ret = ads1292_read_sample(&ads);

        if (ret == ESP_OK) {
            ads_ok_count++;
            last_ads = ads;

            /*
             * 1. ADS 每来一个样本，都进入 EMG 窗口统计。
             */
            emg_window_add_sample(&emg_acc, &ads);

            /*
             * 2. 读取最近一次 IMU 缓存。
             *    IMU 按自己的输出频率更新。
             */
            ESP_ERROR_CHECK(wit_imu_get_latest_pair(&last_imu_pair));

            /*
             * 3. 每 SENSOR_STATE_PACKET_PERIOD_US 组一次状态快照包。
             */
            int64_t now_us = esp_timer_get_time();

            if ((now_us - last_packet_us) >= SENSOR_STATE_PACKET_PERIOD_US) {
                sensor_packet_t pkt = {0};

                sensor_build_packet(&pkt, tx_seq++, &emg_acc, &last_imu_pair);
                sensor_output_packet(&pkt);

                emg_window_reset(&emg_acc, now_us);
                last_packet_us = now_us;
            }
        } else if (ret == ESP_ERR_TIMEOUT) {
            ads_timeout_count++;
        } else {
            ads_error_count++;
        }

        int64_t now_us = esp_timer_get_time();

        if ((now_us - last_print_us) >= STATUS_PRINT_PERIOD_MS * 1000LL) {
            float dt_s = (now_us - last_print_us) / 1000000.0f;
            float ads_rate = ads_ok_count / dt_s;

            const wit_imu_data_t *imu1 = &last_imu_pair.imu[WIT_IMU_ID_1];
            const wit_imu_data_t *imu2 = &last_imu_pair.imu[WIT_IMU_ID_2];

            ESP_LOGI(TAG,
                     "node=%u,type=%u,send_rate=%uHz"
                     ", ADS rate=%.1f Hz, ok=%" PRIu32 ", timeout=%" PRIu32 ", err=%" PRIu32
                     ", latest ADS=[%" PRId32 ", %" PRId32 ", %" PRId32 ", %" PRId32 "]"
                     ", tx_seq=%" PRIu32
                     ", EMG window_n=%" PRIu32
                     ", IMU1 valid=%u, age=%d ms, updates=%" PRIu32
                     ", A1=[%.2f, %.2f, %.2f]"
                     ", IMU2 valid=%u, age=%d ms, updates=%" PRIu32
                     ", A2=[%.2f, %.2f, %.2f]",
                     SENSOR_NODE_ID,
                     SENSOR_NODE_TYPE,
                     SENSOR_PACKET_RATE_HZ,
                     ads_rate,
                     ads_ok_count,
                     ads_timeout_count,
                     ads_error_count,
                     last_ads.m1_ch1,
                     last_ads.m1_ch2,
                     last_ads.m2_ch1,
                     last_ads.m2_ch2,
                     tx_seq,
                     emg_acc.sample_count,
                     imu1->valid,
                     sensor_imu_age_ms(now_us, imu1),
                     imu1->update_count,
                     imu1->angle_x_deg,
                     imu1->angle_y_deg,
                     imu1->angle_z_deg,
                     imu2->valid,
                     sensor_imu_age_ms(now_us, imu2),
                     imu2->update_count,
                     imu2->angle_x_deg,
                     imu2->angle_y_deg,
                     imu2->angle_z_deg);

            ads_ok_count = 0;
            ads_timeout_count = 0;
            ads_error_count = 0;
            last_print_us = now_us;
        }
    }
}
