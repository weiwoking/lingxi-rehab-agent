#ifndef __ADS1292_H__
#define __ADS1292_H__

/**
 * @file ads1292.h
 * @brief ADS1292 双模块四通道采集驱动头文件
 *
 * 当前模块负责：
 * 1. 初始化两个 ADS1292；
 * 2. 配置 ADS1292 寄存器；
 * 3. 等待 DRDY 采样节拍；
 * 4. 读取两个 ADS1292 的四路原始 24 位 ADC 数据。
 *
 * 说明：
 * - 一个 ADS1292 = 2 个采集通道；
 * - 两个 ADS1292 = 4 个采集通道；
 * - 本驱动只输出原始码值，不在底层做滤波和算法判断；
 * - 后续 P4 或云端需要滤波、特征提取时，在上层处理。
 */

#include <stdint.h>

#include "esp_err.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================
 * 1. ADS1292 硬件连接配置
 * ============================================================
 *
 * 两个 ADS1292 共用 SPI 总线：MOSI / MISO / SCLK
 * 两个 ADS1292 使用独立 CS：CS1 / CS2
 * 两个 ADS1292 共用 START 和 PWDN/RESET
 * DRDY1 作为统一采样节拍，DRDY2 用于调试检查
 */

#define ADS1292_SPI_HOST           SPI2_HOST

/** @brief SPI MISO：ADS1292 DOUT -> ESP32-S3 MISO */
#define ADS1292_PIN_MISO           GPIO_NUM_13

/** @brief SPI MOSI：ESP32-S3 MOSI -> ADS1292 DIN */
#define ADS1292_PIN_MOSI           GPIO_NUM_11

/** @brief SPI SCLK：ESP32-S3 SCLK -> ADS1292 SCLK */
#define ADS1292_PIN_SCLK           GPIO_NUM_12

/** @brief 第 1 个 ADS1292 的片选 */
#define ADS1292_PIN_CS1            GPIO_NUM_10

/** @brief 第 2 个 ADS1292 的片选 */
#define ADS1292_PIN_CS2            GPIO_NUM_9

/** @brief 第 1 个 ADS1292 的 DRDY。驱动以它作为采样中断源。 */
#define ADS1292_PIN_DRDY1          GPIO_NUM_14

/** @brief 第 2 个 ADS1292 的 DRDY。当前仅作为状态检查。 */
#define ADS1292_PIN_DRDY2          GPIO_NUM_15

/** @brief 两个 ADS1292 共用 START 引脚 */
#define ADS1292_PIN_START          GPIO_NUM_16

/** @brief 两个 ADS1292 共用 PWDN/RESET 引脚 */
#define ADS1292_PIN_PWDN           GPIO_NUM_17

/**
 * @brief ADS1292 SPI 时钟频率
 *
 * ADS1292 手册要求 SPI 模式 CPOL=0、CPHA=1。
 * 当前先用 100 kHz，稳定优先；250 SPS 下完全够用。
 */
#define ADS1292_SPI_CLOCK_HZ       (100 * 1000)

/** @brief SPI 单次最大传输字节数 */
#define ADS1292_SPI_MAX_TRANSFER   32

/* ============================================================
 * 2. ADS1292 采样数据结构
 * ============================================================ */

typedef struct
{
    /** @brief 采样时间戳，单位 us */
    int64_t ts_us;

    /** @brief 模块 1 通道 1 原始 ADC 值 */
    int32_t m1_ch1;

    /** @brief 模块 1 通道 2 原始 ADC 值 */
    int32_t m1_ch2;

    /** @brief 模块 2 通道 1 原始 ADC 值 */
    int32_t m2_ch1;

    /** @brief 模块 2 通道 2 原始 ADC 值 */
    int32_t m2_ch2;

} ads1292_sample_t;

/* ============================================================
 * 3. 对外接口
 * ============================================================ */

/**
 * @brief 初始化两个 ADS1292 模块
 *
 * @return
 *      - ESP_OK：初始化成功
 *      - 其他：初始化失败
 */
esp_err_t ads1292_init(void);

/**
 * @brief 读取一次双 ADS1292 四通道数据
 *
 * 函数会等待 ADS1292 模块 1 的 DRDY 下降沿。
 * 因为两个 ADS1292 共用 START，并且配置相同，所以模块 1 可作为统一采样节拍。
 *
 * @param sample 输出采样数据
 *
 * @return
 *      - ESP_OK：读取成功
 *      - ESP_ERR_INVALID_ARG：参数为空
 *      - ESP_ERR_INVALID_STATE：驱动未初始化
 *      - ESP_ERR_TIMEOUT：等待 DRDY 超时
 *      - 其他：SPI 读取失败
 */
esp_err_t ads1292_read_sample(ads1292_sample_t *sample);

#ifdef __cplusplus
}
#endif

#endif
