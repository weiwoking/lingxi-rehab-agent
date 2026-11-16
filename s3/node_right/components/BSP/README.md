# S3-R BSP 组件说明

本目录保存 S3-R 传感节点的自研底层组件。封存版本中，BSP 的职责是让 `main/main.c` 能稳定取得两个 IMU 的数据并通过 UDP 发给 P4。

## 目录职责

```text
ADS1292/   保留目录，但 S3-R 主程序不初始化 ADS1292
COMM/      Wi-Fi STA 和 UDP broadcast 发送
COMMON/    S3/P4 共用的 sensor_packet_t 协议定义
IMU/       两个 WT901C-TTL IMU 的 UART 接收和解析
LED/       LED 辅助模块
```

## 数据链路

```text
WT901 IMU1 + WT901 IMU2
  -> main.c 形成 IMU 快照
  -> sensor_packet_t
  -> COMM/rehab_udp_client.c
  -> UDP broadcast 5005
  -> P4
```

## 封存注意事项

- `COMMON/sensor_packet.h` 必须与 P4、S3-L 保持一致。
- S3-R 在包内仍使用 `imu1/imu2` 字段，P4 融合层会映射为全局 `imu3/imu4`。
- `COMM/rehab_udp_client.c` 中的 Wi-Fi 配置属于现场配置，不在文档中复述具体值。
- `ADS1292/` 保留是为了与 S3-L 工程结构一致，不代表当前节点使用 EMG。
