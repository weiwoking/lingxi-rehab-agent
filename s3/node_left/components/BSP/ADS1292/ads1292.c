/**
 * @file ads1292.c
 * @brief ADS1292 双模块四通道采集驱动实现
 *
 * 封存版原则：
 * 1. 底层只负责初始化、寄存器配置、读原始数据；
 * 2. 不在驱动层做滤波、动作判断、无线发送；
 * 3. 对外只暴露 ads1292_init() 和 ads1292_read_sample()；
 * 4. 后续接 ESP32-P4 时，不需要再改本文件。
 */

#include "ads1292.h"

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#include "driver/gpio.h"
#include "driver/spi_master.h"

#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"

static const char *TAG = "ADS1292";

/* ============================================================
 * 1. ADS1292 命令定义
 * ============================================================ */

#define ADS1292_CMD_WAKEUP     0x02
#define ADS1292_CMD_STANDBY    0x04
#define ADS1292_CMD_RESET      0x06
#define ADS1292_CMD_START      0x08
#define ADS1292_CMD_STOP       0x0A
#define ADS1292_CMD_RDATAC     0x10
#define ADS1292_CMD_SDATAC     0x11
#define ADS1292_CMD_RDATA      0x12

#define ADS1292_CMD_RREG       0x20
#define ADS1292_CMD_WREG       0x40

/* ============================================================
 * 2. ADS1292 寄存器定义
 * ============================================================ */

#define ADS1292_REG_ID         0x00
#define ADS1292_REG_CONFIG1    0x01
#define ADS1292_REG_CONFIG2    0x02
#define ADS1292_REG_LOFF       0x03
#define ADS1292_REG_CH1SET     0x04
#define ADS1292_REG_CH2SET     0x05

/* ============================================================
 * 3. ADS1292 固定配置
 * ============================================================ */

/**
 * CONFIG1：采样率配置
 * 0x00 = 125 SPS
 * 0x01 = 250 SPS
 * 0x02 = 500 SPS
 * 0x03 = 1000 SPS
 *
 * 当前封存使用 250 SPS。对康复训练中的肌电趋势和动作触发，
 * 250 SPS 先够用；如果后续明确要做高频 EMG 频谱，再把这里改高。
 */
#define ADS1292_CONFIG1_VALUE      0x01

/** @brief CONFIG2：沿用当前已跑通的内部参考相关配置 */
#define ADS1292_CONFIG2_VALUE      0xA0

/** @brief LOFF：沿用当前已跑通配置 */
#define ADS1292_LOFF_VALUE         0x10

/** @brief CHnSET：0x10 表示正常电极输入，PGA 配置沿用当前可用值 */
#define ADS1292_CH1SET_VALUE       0x10
#define ADS1292_CH2SET_VALUE       0x10

/** @brief 250 SPS 周期约 4 ms，20 ms 超时足够宽松 */
#define ADS1292_DRDY_TIMEOUT_MS    20

/** @brief ADS1292 单次 RDATA 返回：3 字节 STATUS + 3 字节 CH1 + 3 字节 CH2 */
#define ADS1292_RDATA_FRAME_SIZE   9

/* ============================================================
 * 4. 驱动内部状态
 * ============================================================ */

static spi_device_handle_t s_ads1 = NULL;
static spi_device_handle_t s_ads2 = NULL;
static SemaphoreHandle_t s_drdy_sem = NULL;

/* ============================================================
 * 5. DRDY 中断
 * ============================================================ */

static void IRAM_ATTR ads1292_drdy_isr_handler(void *arg)
{
    (void)arg;

    BaseType_t high_task_woken = pdFALSE;

    if (s_drdy_sem != NULL) {
        xSemaphoreGiveFromISR(s_drdy_sem, &high_task_woken);
    }

    if (high_task_woken == pdTRUE) {
        portYIELD_FROM_ISR();
    }
}

/* ============================================================
 * 6. SPI 基础访问函数
 * ============================================================ */

static esp_err_t ads1292_spi_txrx(spi_device_handle_t dev,
                                  const uint8_t *tx,
                                  uint8_t *rx,
                                  size_t len_bytes)
{
    if (dev == NULL || tx == NULL || rx == NULL || len_bytes == 0) {
        return ESP_ERR_INVALID_ARG;
    }

    spi_transaction_t trans = {
        .length = len_bytes * 8,
        .tx_buffer = tx,
        .rx_buffer = rx,
    };

    return spi_device_transmit(dev, &trans);
}

static esp_err_t ads1292_send_cmd(spi_device_handle_t dev, uint8_t cmd)
{
    uint8_t tx[1] = {cmd};
    uint8_t rx[1] = {0};

    ESP_RETURN_ON_ERROR(ads1292_spi_txrx(dev, tx, rx, sizeof(tx)),
                        TAG,
                        "send command 0x%02X failed",
                        cmd);

    /* ADS1292 命令之间留 50 us，稳定优先。 */
    esp_rom_delay_us(50);

    return ESP_OK;
}

static esp_err_t ads1292_read_regs(spi_device_handle_t dev,
                                   uint8_t addr,
                                   uint8_t *data,
                                   size_t len)
{
    uint8_t tx[2 + 16] = {0};
    uint8_t rx[2 + 16] = {0};

    if (data == NULL || len == 0 || len > 16) {
        return ESP_ERR_INVALID_ARG;
    }

    tx[0] = ADS1292_CMD_RREG | (addr & 0x1F);
    tx[1] = (uint8_t)(len - 1);

    ESP_RETURN_ON_ERROR(ads1292_spi_txrx(dev, tx, rx, 2 + len),
                        TAG,
                        "read regs failed");

    memcpy(data, &rx[2], len);

    esp_rom_delay_us(50);

    return ESP_OK;
}

static esp_err_t ads1292_write_regs(spi_device_handle_t dev,
                                    uint8_t addr,
                                    const uint8_t *data,
                                    size_t len)
{
    uint8_t tx[2 + 16] = {0};
    uint8_t rx[2 + 16] = {0};

    if (data == NULL || len == 0 || len > 16) {
        return ESP_ERR_INVALID_ARG;
    }

    tx[0] = ADS1292_CMD_WREG | (addr & 0x1F);
    tx[1] = (uint8_t)(len - 1);
    memcpy(&tx[2], data, len);

    ESP_RETURN_ON_ERROR(ads1292_spi_txrx(dev, tx, rx, 2 + len),
                        TAG,
                        "write regs failed");

    esp_rom_delay_us(50);

    return ESP_OK;
}

/* ============================================================
 * 7. 数据处理函数
 * ============================================================ */

static int32_t ads1292_s24_to_s32(uint8_t b0, uint8_t b1, uint8_t b2)
{
    int32_t value = ((int32_t)b0 << 16) |
                    ((int32_t)b1 << 8)  |
                    ((int32_t)b2);

    if ((value & 0x800000) != 0) {
        value |= 0xFF000000;
    }

    return value;
}

static esp_err_t ads1292_read_rdata_frame(spi_device_handle_t dev, uint8_t *frame9)
{
    uint8_t tx[1 + ADS1292_RDATA_FRAME_SIZE] = {0};
    uint8_t rx[1 + ADS1292_RDATA_FRAME_SIZE] = {0};

    if (frame9 == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    tx[0] = ADS1292_CMD_RDATA;

    ESP_RETURN_ON_ERROR(ads1292_spi_txrx(dev, tx, rx, sizeof(tx)),
                        TAG,
                        "RDATA failed");

    memcpy(frame9, &rx[1], ADS1292_RDATA_FRAME_SIZE);

    return ESP_OK;
}

/* ============================================================
 * 8. GPIO 和 SPI 初始化
 * ============================================================ */

static esp_err_t ads1292_gpio_init(void)
{
    gpio_config_t out_conf = {
        .pin_bit_mask = (1ULL << ADS1292_PIN_START) |
                        (1ULL << ADS1292_PIN_PWDN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };

    ESP_RETURN_ON_ERROR(gpio_config(&out_conf),
                        TAG,
                        "config START/PWDN gpio failed");

    gpio_config_t drdy1_conf = {
        .pin_bit_mask = (1ULL << ADS1292_PIN_DRDY1),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_NEGEDGE,
    };

    ESP_RETURN_ON_ERROR(gpio_config(&drdy1_conf),
                        TAG,
                        "config DRDY1 gpio failed");

    gpio_config_t drdy2_conf = {
        .pin_bit_mask = (1ULL << ADS1292_PIN_DRDY2),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };

    ESP_RETURN_ON_ERROR(gpio_config(&drdy2_conf),
                        TAG,
                        "config DRDY2 gpio failed");

    s_drdy_sem = xSemaphoreCreateBinary();
    if (s_drdy_sem == NULL) {
        return ESP_ERR_NO_MEM;
    }

    esp_err_t ret = gpio_install_isr_service(0);
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        return ret;
    }

    ESP_RETURN_ON_ERROR(gpio_isr_handler_add(ADS1292_PIN_DRDY1,
                                             ads1292_drdy_isr_handler,
                                             NULL),
                        TAG,
                        "add DRDY1 isr failed");

    return ESP_OK;
}

static esp_err_t ads1292_spi_init(void)
{
    spi_bus_config_t bus_cfg = {
        .mosi_io_num = ADS1292_PIN_MOSI,
        .miso_io_num = ADS1292_PIN_MISO,
        .sclk_io_num = ADS1292_PIN_SCLK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = ADS1292_SPI_MAX_TRANSFER,
    };

    spi_device_interface_config_t dev1_cfg = {
        .clock_speed_hz = ADS1292_SPI_CLOCK_HZ,
        .mode = 1,
        .spics_io_num = ADS1292_PIN_CS1,
        .queue_size = 1,
    };

    spi_device_interface_config_t dev2_cfg = {
        .clock_speed_hz = ADS1292_SPI_CLOCK_HZ,
        .mode = 1,
        .spics_io_num = ADS1292_PIN_CS2,
        .queue_size = 1,
    };

    ESP_RETURN_ON_ERROR(spi_bus_initialize(ADS1292_SPI_HOST,
                                           &bus_cfg,
                                           SPI_DMA_DISABLED),
                        TAG,
                        "spi bus init failed");

    ESP_RETURN_ON_ERROR(spi_bus_add_device(ADS1292_SPI_HOST,
                                           &dev1_cfg,
                                           &s_ads1),
                        TAG,
                        "add ADS1292 module 1 failed");

    ESP_RETURN_ON_ERROR(spi_bus_add_device(ADS1292_SPI_HOST,
                                           &dev2_cfg,
                                           &s_ads2),
                        TAG,
                        "add ADS1292 module 2 failed");

    return ESP_OK;
}

/* ============================================================
 * 9. ADS1292 上电、复位、寄存器配置
 * ============================================================ */

static void ads1292_shared_powerup(void)
{
    gpio_set_level(ADS1292_PIN_START, 0);

    gpio_set_level(ADS1292_PIN_PWDN, 0);
    vTaskDelay(pdMS_TO_TICKS(20));

    gpio_set_level(ADS1292_PIN_PWDN, 1);

    /* 你当前工程已经验证 1100 ms 稳定，封存版继续保留。 */
    vTaskDelay(pdMS_TO_TICKS(1100));
}

static esp_err_t ads1292_config_one(spi_device_handle_t dev, const char *name)
{
    uint8_t id = 0;

    ESP_RETURN_ON_ERROR(ads1292_send_cmd(dev, ADS1292_CMD_SDATAC),
                        TAG,
                        "%s SDATAC failed",
                        name);

    ESP_RETURN_ON_ERROR(ads1292_send_cmd(dev, ADS1292_CMD_RESET),
                        TAG,
                        "%s RESET failed",
                        name);

    vTaskDelay(pdMS_TO_TICKS(20));

    ESP_RETURN_ON_ERROR(ads1292_send_cmd(dev, ADS1292_CMD_SDATAC),
                        TAG,
                        "%s SDATAC after reset failed",
                        name);

    ESP_RETURN_ON_ERROR(ads1292_read_regs(dev, ADS1292_REG_ID, &id, 1),
                        TAG,
                        "%s read ID failed",
                        name);

    ESP_LOGI(TAG, "%s ID = 0x%02X", name, id);

    uint8_t reg_block[] = {
        ADS1292_CONFIG1_VALUE,
        ADS1292_CONFIG2_VALUE,
        ADS1292_LOFF_VALUE,
        ADS1292_CH1SET_VALUE,
        ADS1292_CH2SET_VALUE,
    };

    ESP_RETURN_ON_ERROR(ads1292_write_regs(dev,
                                           ADS1292_REG_CONFIG1,
                                           reg_block,
                                           sizeof(reg_block)),
                        TAG,
                        "%s write config regs failed",
                        name);

    uint8_t verify[5] = {0};

    ESP_RETURN_ON_ERROR(ads1292_read_regs(dev,
                                          ADS1292_REG_CONFIG1,
                                          verify,
                                          sizeof(verify)),
                        TAG,
                        "%s read back config regs failed",
                        name);

    ESP_LOGI(TAG,
             "%s CONFIG1=0x%02X CONFIG2=0x%02X LOFF=0x%02X CH1SET=0x%02X CH2SET=0x%02X",
             name,
             verify[0],
             verify[1],
             verify[2],
             verify[3],
             verify[4]);

    if (verify[0] != ADS1292_CONFIG1_VALUE ||
        verify[1] != ADS1292_CONFIG2_VALUE ||
        verify[2] != ADS1292_LOFF_VALUE    ||
        verify[3] != ADS1292_CH1SET_VALUE  ||
        verify[4] != ADS1292_CH2SET_VALUE) {
        ESP_LOGE(TAG, "%s register verify failed", name);
        return ESP_ERR_INVALID_RESPONSE;
    }

    return ESP_OK;
}

static esp_err_t ads1292_start_conversion(void)
{
    if (s_drdy_sem != NULL) {
        while (xSemaphoreTake(s_drdy_sem, 0) == pdTRUE) {
        }
    }

    ESP_RETURN_ON_ERROR(ads1292_send_cmd(s_ads1, ADS1292_CMD_START),
                        TAG,
                        "M1 START command failed");

    ESP_RETURN_ON_ERROR(ads1292_send_cmd(s_ads2, ADS1292_CMD_START),
                        TAG,
                        "M2 START command failed");

    gpio_set_level(ADS1292_PIN_START, 1);

    /* 等待数字滤波器输出稳定，丢掉刚启动时的旧节拍。 */
    vTaskDelay(pdMS_TO_TICKS(20));

    if (s_drdy_sem != NULL) {
        while (xSemaphoreTake(s_drdy_sem, 0) == pdTRUE) {
        }
    }

    return ESP_OK;
}

/* ============================================================
 * 10. 对外接口
 * ============================================================ */

esp_err_t ads1292_init(void)
{
    ESP_LOGI(TAG, "ADS1292 init start");

    ESP_RETURN_ON_ERROR(ads1292_gpio_init(), TAG, "gpio init failed");
    ESP_RETURN_ON_ERROR(ads1292_spi_init(), TAG, "spi init failed");

    ads1292_shared_powerup();

    ESP_RETURN_ON_ERROR(ads1292_config_one(s_ads1, "M1"),
                        TAG,
                        "config ADS1292 module 1 failed");

    ESP_RETURN_ON_ERROR(ads1292_config_one(s_ads2, "M2"),
                        TAG,
                        "config ADS1292 module 2 failed");

    ESP_RETURN_ON_ERROR(ads1292_start_conversion(),
                        TAG,
                        "start conversion failed");

    ESP_LOGI(TAG, "ADS1292 init done");
    return ESP_OK;
}

esp_err_t ads1292_read_sample(ads1292_sample_t *sample)
{
    if (sample == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    if (s_ads1 == NULL || s_ads2 == NULL || s_drdy_sem == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    BaseType_t ok = xSemaphoreTake(s_drdy_sem,
                                   pdMS_TO_TICKS(ADS1292_DRDY_TIMEOUT_MS));

    if (ok != pdTRUE) {
        ESP_LOGW(TAG,
                 "DRDY1 timeout: drdy1=%d, drdy2=%d",
                 gpio_get_level(ADS1292_PIN_DRDY1),
                 gpio_get_level(ADS1292_PIN_DRDY2));
        return ESP_ERR_TIMEOUT;
    }

    uint8_t frame1[ADS1292_RDATA_FRAME_SIZE] = {0};
    uint8_t frame2[ADS1292_RDATA_FRAME_SIZE] = {0};
    int64_t ts = esp_timer_get_time();

    ESP_RETURN_ON_ERROR(ads1292_read_rdata_frame(s_ads1, frame1),
                        TAG,
                        "read ADS1292 module 1 frame failed");

    ESP_RETURN_ON_ERROR(ads1292_read_rdata_frame(s_ads2, frame2),
                        TAG,
                        "read ADS1292 module 2 frame failed");

    sample->ts_us = ts;

    sample->m1_ch1 = ads1292_s24_to_s32(frame1[3], frame1[4], frame1[5]);
    sample->m1_ch2 = ads1292_s24_to_s32(frame1[6], frame1[7], frame1[8]);
    sample->m2_ch1 = ads1292_s24_to_s32(frame2[3], frame2[4], frame2[5]);
    sample->m2_ch2 = ads1292_s24_to_s32(frame2[6], frame2[7], frame2[8]);

    return ESP_OK;
}
