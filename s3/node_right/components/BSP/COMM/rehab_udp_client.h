#ifndef REHAB_UDP_CLIENT_H
#define REHAB_UDP_CLIENT_H

#include "esp_err.h"
#include "sensor_packet.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief 启动 S3 Wi-Fi STA + UDP Client
 *
 * 当前目标：
 * - S3 连接 P4 开出来的 Wi-Fi 热点；
 * - 后续 sensor_output_packet() 调用 rehab_udp_client_send_packet()；
 * - UDP 任务负责把最新 packet 发给 P4。
 */
esp_err_t rehab_udp_client_start(void);

/**
 * @brief 发送一个传感器 packet
 *
 * 注意：
 * - 这个函数不会直接阻塞发送；
 * - 它只是把最新 packet 放入队列；
 * - 如果发送速度跟不上，会覆盖旧 packet，只保留最新数据。
 */
esp_err_t rehab_udp_client_send_packet(const sensor_packet_t *pkt);

/**
 * @brief 判断当前 Wi-Fi 是否已连接
 */
bool rehab_udp_client_is_connected(void);

#ifdef __cplusplus
}
#endif

#endif /* REHAB_UDP_CLIENT_H */