# S3-L BSP 组件说明

本目录保存 S3-L 传感节点的自研底层组件。封存版本中，BSP 的职责是让 `main/main.c` 能稳定取得 EMG、IMU 和 UDP 发送能力。

## 目录职责

```text
ADS1292/   两个 ADS1292 的 SPI 驱动，输出 4 路 EMG 原始 ADC 样本
COMM/      Wi-Fi STA 和 UDP broadcast 发送
COMMON/    S3/P4 共用的 sensor_packet_t 协议定义
IMU/       两个 WT901C-TTL IMU 的 UART 接收和解析
LED/       LED 辅助模块
```

## 数据链路

```text
ADS1292 + WT901 IMU
  -> main.c 形成 EMG 窗口和 IMU 快照
  -> sensor_packet_t
  -> COMM/rehab_udp_client.c
  -> UDP broadcast 5005
  -> P4
```

## 封存注意事项

- `COMMON/sensor_packet.h` 必须与 P4、S3-R 保持一致。
- `COMM/rehab_udp_client.c` 中的 Wi-Fi 配置属于现场配置，不在文档中复述具体值。
- `IMU/wit_c_sdk.*` 和 `REG.h` 来自 WT901 相关 SDK/寄存器定义，当前主链路主要使用 `wit_imu.*`。
- `LED/` 不是当前数据采集主链路的必要组件。
