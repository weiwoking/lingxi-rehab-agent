# ESP32-S3-R 传感节点封存说明

本工程是康复训练采集系统中的右侧/第二块 ESP32-S3 传感节点，系统编号为：

```text
node_id = 2
node_type = SENSOR_NODE_TYPE_DUAL_IMU
```

本工程已封存。后续只建议修正文档、注释和归档说明，不应改动采样逻辑、协议结构、节点编号、UDP 端口或发包频率。

## 在系统中的位置

```text
S3-R
  -> 采集 2 个 IMU
  -> 组装 sensor_packet_t v5
  -> 100Hz UDP broadcast 到 P4:5005
  -> P4 融合为全局 IMU3 + IMU4
```

整套系统：

```text
S3-L  node_id=1  EMG + IMU1 + IMU2
S3-R  node_id=2  IMU3 + IMU4
P4    接收两个 S3，融合状态，输出 CSV
PC    接收 P4 的 UDP 6006 CSV 并保存
```

## 本节点采集内容

S3-R 负责：

- 两个 WT901C-TTL IMU。
- 不初始化 ADS1292。
- 每 10 ms 组装并发送一个 `sensor_packet_t`。

注意：S3-R 在包内仍然使用 `imu1` 和 `imu2` 字段，这是节点本地视角。P4 融合层会把它们映射为全局 `imu3` 和 `imu4`。

## 通信参数

```text
目标网络: 与 P4、PC 处在同一个热点
发送方式: UDP broadcast
目标端口: 5005
协议类型: binary sensor_packet_t
协议版本: SENSOR_PACKET_VERSION = 5
发包频率: 100 Hz
```

Wi-Fi SSID 和密码在代码宏中定义，文档不复述具体值。

## 关键文件

```text
main/main.c
  应用入口。初始化 IMU 和 UDP 客户端；按 FreeRTOS 周期延时以 100Hz 发包。

components/BSP/COMMON/sensor_packet.h
  S3 与 P4 共用的数据包结构。必须与 P4 和 S3-L 的同名协议文件保持一致。

components/BSP/COMM/rehab_udp_client.c
  Wi-Fi STA 和 UDP broadcast 发送任务。

components/BSP/IMU/wit_imu.c
  两个 WT901C-TTL IMU 的 UART 接收和解析封装。

components/BSP/ADS1292/
  目录仍保留在 BSP 中，但 S3-R 主程序不初始化 ADS1292。

components/BSP/LED/
  LED 辅助模块，不是当前采集链路的核心。
```

## 硬件连接摘要

IMU：

```text
IMU1: UART1, GPIO4 TX, GPIO5 RX
IMU2: UART2, GPIO6 TX, GPIO7 RX
```

S3-R 不需要 ADS1292 才能运行当前封存主链路。

## 正常运行现象

串口日志应能看到：

```text
S3 sensor node start, node_id=2, dual IMU only
IMU init done, ADS1292 disabled for node2
rehab udp broadcast client started
node=2,type=2,send_rate=100Hz...
```

P4 端应能收到 `node=2` 的包，并在融合 CSV 中体现：

```text
node2_rx 持续增加
node2_age_ms 小于 3000
imu3_valid = 1
imu4_valid = 1
```

## 封存维护原则

- 不要更改 `SENSOR_NODE_ID`。
- 不要单独修改 `sensor_packet_t`。
- 不要只改 S3-R 而不同步 P4/S3-L 的协议文件。
- 如果需要重构，建议另开新工程，不在封存版本上直接改主逻辑。
