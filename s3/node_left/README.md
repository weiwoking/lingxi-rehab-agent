# ESP32-S3-L 传感节点封存说明

本工程是康复训练采集系统中的左侧/第一块 ESP32-S3 传感节点，系统编号为：

```text
node_id = 1
node_type = SENSOR_NODE_TYPE_EMG_DUAL_IMU
```

本工程已封存。后续只建议修正文档、注释和归档说明，不应改动采样逻辑、协议结构、节点编号、UDP 端口或发包频率。

## 在系统中的位置

```text
S3-L
  -> 采集 4 路 EMG + 2 个 IMU
  -> 组装 sensor_packet_t v5
  -> 100Hz UDP broadcast 到 P4:5005
  -> P4 融合为 EMG + IMU1 + IMU2
```

整套系统：

```text
S3-L  node_id=1  EMG + IMU1 + IMU2
S3-R  node_id=2  IMU3 + IMU4
P4    接收两个 S3，融合状态，输出 CSV
PC    接收 P4 的 UDP 6006 CSV 并保存
```

## 本节点采集内容

S3-L 负责：

- 两个 WT901C-TTL IMU。
- 两个 ADS1292 模块，共 4 路 EMG 原始 ADC 数据。
- 每 10 ms 形成一个 EMG 窗口统计结果。
- 每 10 ms 组装并发送一个 `sensor_packet_t`。

EMG 输出不是完整高频原始波形，而是窗口特征：

```text
latest_ch1~4
rms_ch1~4
mav_ch1~4
peak_abs_ch1~4
sample_count
```

IMU 输出：

```text
roll/pitch/yaw
gyro_x/y/z
acc_x/y/z
lin_acc_x/y/z
age_ms
update_count
```

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
  应用入口。初始化 IMU、ADS1292、UDP 客户端；循环读取 ADS 样本；
  汇总 EMG 窗口和最近 IMU 状态；按 100Hz 发包。

components/BSP/COMMON/sensor_packet.h
  S3 与 P4 共用的数据包结构。必须与 P4 和 S3-R 的同名协议文件保持一致。

components/BSP/COMM/rehab_udp_client.c
  Wi-Fi STA 和 UDP broadcast 发送任务。

components/BSP/IMU/wit_imu.c
  两个 WT901C-TTL IMU 的 UART 接收和解析封装。

components/BSP/ADS1292/ads1292.c
  两个 ADS1292 的 SPI 驱动和 4 通道样本读取。

components/BSP/LED/
  LED 辅助模块，不是当前采集链路的核心。
```

## 硬件连接摘要

IMU：

```text
IMU1: UART1, GPIO4 TX, GPIO5 RX
IMU2: UART2, GPIO6 TX, GPIO7 RX
```

ADS1292：

```text
MISO        GPIO13
MOSI        GPIO11
SCLK        GPIO12
START       GPIO16
PWDN/RESET  GPIO17
CS1         GPIO10
DRDY1       GPIO14
CS2         GPIO9
DRDY2       GPIO15
```

注意：

- UART0 保留给烧录和日志。
- ADS1292、IMU、S3 必须共地。
- EMG 采集应使用合适的人体电极连接和安全供电方式。

## 正常运行现象

串口日志应能看到：

```text
S3 sensor node start, node_id=1, EMG + dual IMU
All sensors init done
rehab udp broadcast client started
node=1,type=1,send_rate=100Hz...
```

P4 端应能收到 `node=1` 的包，并在融合 CSV 中体现：

```text
node1_rx 持续增加
node1_age_ms 小于 3000
emg_valid = 1
imu1_valid = 1
imu2_valid = 1
```

## 封存维护原则

- 不要更改 `SENSOR_NODE_ID`。
- 不要单独修改 `sensor_packet_t`。
- 不要只改 S3-L 而不同步 P4/S3-R 的协议文件。
- 如果需要重构，建议另开新工程，不在封存版本上直接改主逻辑。
