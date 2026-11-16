#ifndef __WIT_IMU_H__
#define __WIT_IMU_H__

/**
 * @file wit_imu.h
 * @brief 双 WT901C-TTL IMU 的 ESP32-S3 封装层
 *
 * 设计目标：
 * 1. 支持两个 WT901C-TTL 同时接入 ESP32-S3；
 * 2. 不使用 ESP32-S3 的烧录/日志串口 UART0；
 * 3. 对 main.c 只暴露“获取最近一次 IMU 数据”的稳定接口；
 * 4. 不修改维特官方 SDK 文件 wit_c_sdk.c / wit_c_sdk.h / REG.h。
 *
 * 重要说明：
 * - 维特官方 wit_c_sdk 使用全局解析状态和全局 sReg，不适合两个 UART 同时喂数据；
 * - 所以本封装层直接解析 WT901 标准 0x55 帧，官方 SDK 文件保留但不在双 IMU 数据流中调用；
 * - 这样两个 IMU 的数据不会互相覆盖，后续接 P4 时底层也不用再改。
 */

#include <stdint.h>

#include "esp_err.h"
#include "driver/gpio.h"
#include "driver/uart.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================
 * 1. 双 IMU 硬件连接配置
 * ============================================================
 *
 * IMU1：沿用你已经跑通的接线
 * ESP32-S3 GPIO4 TX1  ---->  WT901-1 RX
 * ESP32-S3 GPIO5 RX1  <----  WT901-1 TX
 * ESP32-S3 GND        ----   WT901-1 GND
 *
 * IMU2：使用 UART2，避开 UART0 烧录/日志串口
 * ESP32-S3 GPIO6 TX2  ---->  WT901-2 RX
 * ESP32-S3 GPIO7 RX2  <----  WT901-2 TX
 * ESP32-S3 GND        ----   WT901-2 GND
 *
 * 不要使用 GPIO43/GPIO44 作为 IMU 串口。
 * 它们通常是 ESP32-S3 DevKitC 的 UART0 控制台/烧录串口。
 */

#define WIT_IMU1_UART_PORT          UART_NUM_1
#define WIT_IMU1_PIN_TX             GPIO_NUM_4
#define WIT_IMU1_PIN_RX             GPIO_NUM_5

#define WIT_IMU2_UART_PORT          UART_NUM_2
#define WIT_IMU2_PIN_TX             GPIO_NUM_6
#define WIT_IMU2_PIN_RX             GPIO_NUM_7

/**
 * @brief 初始波特率
 *
 * 你之前日志已经证明 WT901 在 9600 baud 被识别。
 * 初始化函数内部仍会自动扫描常见波特率。
 */
#define WIT_IMU_DEFAULT_BAUDRATE    9600

#define WIT_IMU_RX_BUF_SIZE         2048
#define WIT_IMU_TASK_STACK_SIZE     4096
#define WIT_IMU_TASK_PRIORITY       5

/* ============================================================
 * 2. 数据结构
 * ============================================================ */

typedef enum
{
    WIT_IMU_ID_1 = 0,
    WIT_IMU_ID_2 = 1,
    WIT_IMU_COUNT = 2,
} wit_imu_id_t;

typedef struct
{
    /** @brief 最近一次 IMU 数据更新时间戳，单位 us */
    int64_t ts_us;

    /** @brief 三轴加速度，单位 g */
    float acc_x_g;
    float acc_y_g;
    float acc_z_g;

    /** @brief 三轴角速度，单位 deg/s */
    float gyro_x_dps;
    float gyro_y_dps;
    float gyro_z_dps;

    /** @brief 欧拉角，单位 deg。x=Roll, y=Pitch, z=Yaw */
    float angle_x_deg;
    float angle_y_deg;
    float angle_z_deg;

    /** @brief 三轴磁场原始值 */
    int16_t mag_x;
    int16_t mag_y;
    int16_t mag_z;

    /** @brief 温度，单位摄氏度 */
    float temp_c;

    /** @brief 1 表示已经收到过有效姿态数据 */
    uint8_t valid;

    /** @brief 接收更新次数，用于判断 IMU 是否仍在刷新 */
    uint32_t update_count;

} wit_imu_data_t;

typedef struct
{
    wit_imu_data_t imu[WIT_IMU_COUNT];
} wit_imu_pair_data_t;

/* ============================================================
 * 3. 对外接口
 * ============================================================ */

/**
 * @brief 初始化两个 WT901 IMU
 *
 * @return
 *      - ESP_OK：两个 UART 初始化成功，后台接收任务已启动
 *      - 其他：初始化失败
 */
esp_err_t wit_imu_init(void);

/**
 * @brief 获取某一个 IMU 的最近一次数据
 *
 * @param id   WIT_IMU_ID_1 或 WIT_IMU_ID_2
 * @param data 输出缓存数据
 */
esp_err_t wit_imu_get_latest_by_id(wit_imu_id_t id, wit_imu_data_t *data);

/**
 * @brief 获取两个 IMU 的最近一次数据
 *
 * @param pair 输出两个 IMU 的缓存数据
 */
esp_err_t wit_imu_get_latest_pair(wit_imu_pair_data_t *pair);

/**
 * @brief 兼容旧代码：默认返回 IMU1 数据
 */
esp_err_t wit_imu_get_latest(wit_imu_data_t *data);

#ifdef __cplusplus
}
#endif

#endif