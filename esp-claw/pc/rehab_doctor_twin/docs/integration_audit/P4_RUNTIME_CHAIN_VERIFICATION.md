# P4 Runtime Chain Verification

验证时间：2026-07-24 02:17-02:25 CST

依据：

- `C:\ev\rehab_doctor_twin\docs\integration_audit\CURRENT_SYSTEM_FACTS.md`
- `C:\ev\rehab_doctor_twin\docs\integration_audit\RUNTIME_VERIFICATION_REPORT.md`

本轮只验证现有实现的真实运行链路。未修改代码、配置、接口、sdkconfig、dependencies.lock、Board Manager、ESP-IDF、Conda 环境或服务器；未执行构建、烧录、Git commit、push、reset。

## 1. 板卡与串口信息

- 自动枚举串口：发现 `COM24`。
- esptool 识别结果：`ESP32-P4`，revision `v3.2`。
- MAC：`e8:f6:0a:e3:a9:4b`。
- 连接状态：esptool 可连接，stub flasher 可运行，随后硬复位成功。
- PC 侧板卡通信网段：Windows 移动热点接口 `192.168.137.1/24`。
- P4 Wi-Fi STA 地址：`192.168.137.6`，网关 `192.168.137.1`。

证据：

- esptool 输出：`Chip is ESP32-P4 (revision v3.2)`，`MAC: e8:f6:0a:e3:a9:4b`。
- 串口日志：`sta ip: 192.168.137.6, mask: 255.255.255.0, gw: 192.168.137.1`。

## 2. 是否使用现有固件或重新 flash

- 未 flash。
- 未执行 `idf.py build`。
- 现有 build 文件均存在：
  - `build\bootloader\bootloader.bin`
  - `build\partition_table\partition-table.bin`
  - `build\edge_agent.bin`
  - `build\flash_args`
- build 缓存确认：
  - `idf_path`: `E:/esp/.espressif/v5.5.4/esp-idf`
  - `git_revision`: `v5.5.4-dirty`
  - `target`: `esp32p4`
- 板上现有固件启动日志确认：
  - bootloader：`ESP-IDF v5.5.4-dirty`
  - app：`Project name: edge_agent`
  - app version：`3dae9be`
  - app ESP-IDF：`v5.5.4-dirty`

## 3. 固件启动模块状态

| 项 | 运行态结论 | 证据 |
|---|---|---|
| NVS | 未发现明确 `nvs_flash_init` 成功日志 | 串口日志未直接打印 NVS 初始化结果 |
| Board Manager | 初始化成功 | `BOARD_MANAGER: All peripherals initialized`，`Board manager initialized` |
| 文件系统 | 初始化成功 | `/system` FATFS、`/sdcard` FATFS、`/ramfs` 均挂载成功 |
| Wi-Fi | 最终 STA 连接成功，AP 也开启 | 初期多次 reason=201 断开，随后 `sta ip: 192.168.137.6` |
| Camera | 真实工作 | `Detected Camera sensor PID=0xcb3a`，`CAM_SNAPSHOT ... output_length=23844/460800` |
| ToF | 真实工作 | `TOF_STREAM_STARTED`，持续 `TOF_FRAME` 和 `TOF_EXT_SUMMARY` |
| rehab sensor receiver | 组件运行但外部 IMU/sEMG 未在线 | `node1_online=0 ... node2_online=0 ... required_ok=0` |
| training recorder | 初始化/状态可查询，未启动真实训练 | `rehab_training_status ... state=idle` |
| score bridge | 初始化成功，未触发新评分 | `score bridge initialized`，无 P4 到评分服务 job 请求 |
| cloud fusion | 真实接通 | 多次 `CLOUD_FUSION ... http_status=200` |
| person-follow | 真实接通 | 多次 `PF_FRAME ... http_status=200` |
| scan capture | 捕获聚合链路运行 | `CAPTURE_PAIR ... image_seq ... tof_seq ... within_limit=1` |
| scan transfer | 本轮未触发 | 未运行 `rehab_scan_send` |
| fatigue | 状态可用，未收到本轮新评分回调 | `rehab_fatigue_status ... available=1 fatigue=0` |
| 本地 HTTP server | 路由注册可见，未单独 HTTP 调用验证 | `CAM_HTTP_ROUTES`、`TOF_HTTP_ROUTES`、`CAPTURE_HTTP_ROUTES` |

## 4. Wi-Fi 与 PC 地址

- PC 视觉服务监听：`0.0.0.0:8000`。
- PC 评分服务监听：`0.0.0.0:8020`。
- PC 移动热点地址：`192.168.137.1/24`。
- P4 地址：`192.168.137.6`。
- P4 配置/默认视觉融合地址：`http://192.168.137.1:8000/api/v1/vision/fuse`。
- person-follow 实际请求地址：`http://192.168.137.1:8000/api/v1/person-follow/detect`。

## 5. Camera 实测

- Camera 初始化成功，CSI `/dev/video0` 可用。
- person-follow 链路生成真实 JPEG：
  - `CAM_SNAPSHOT kind=jpeg ... output_length=23844`
  - 后续 JPEG 大小约 `22670-27262` bytes。
- cloud fusion 链路生成真实 raw frame：
  - `CAM_SNAPSHOT kind=raw_only ... output_length=460800 output_format=rgb565`

结论：Camera 真实工作。

## 6. ToF 实测

- ToF 初始化成功：
  - `TOF_CONFIGURED ... resolution=8x8 frequency=15Hz mode=continuous`
  - `TOF_STREAM_STARTED`
- 连续帧可用：
  - `TOF_FRAME seq=... packet_bytes=448`
  - `TOF_EXT_SUMMARY ... valid_zones=... distance_min_mm=...`
- cloud fusion 捕获配对包含真实 ToF：
  - `CAPTURE_PAIR capture_id=CAP00000001 image_seq=1 tof_candidates=1 selected_index=0 tof_seq=994 ... within_limit=1`

结论：ToF 真实工作。

## 7. IMU/sEMG 实测

- 用户明确说明 IMU 和 EMG 未启动。
- 运行日志与此一致：
  - `node1_online=0 pps=0.0 valid=0`
  - `node2_online=0 pps=0.0 valid=0`
  - `required_ok=0`
- 未开始真实训练 session。

结论：IMU/sEMG 本轮未工作，评分链路因此未进入真实训练与评分。

## 8. P4 到视觉服务请求证据

### person-follow

- 触发方式：串口 CLI 调用现有 capability：`cap call person_follow_start {}`，随后 `cap call person_follow_stop {}`。
- ESP 侧证据：
  - `PF_FRAME gen=1 seq=1 jpeg_bytes=23844 http_ms=330 total_ms=512 http_status=200 raw=1 dedup=1 selected=1`
  - 多帧 `PF_FRAME ... http_status=200`
  - 状态查询显示 `perception_mode:"real"`、`mock_mode:"no_target"`、`last_http_status:200`、`image_format:"JPEG"`。
- PC 侧证据：
  - 多次 `192.168.137.6 - "POST /api/v1/person-follow/detect HTTP/1.1" 200 OK`。

结论：P4 到 PC person-follow 接口真实接通，包含真实摄像头 JPEG。

### vision/fuse

- 触发方式：串口 CLI 调用现有 `follow_target start`，随后 `follow_target stop`。
- ESP 侧证据：
  - `CAPTURE_PAIR ... image_seq=... tof_seq=... tof_json_bytes=... multipart_bytes=4616xx`
  - `CLOUD_FUSION capture_id=CAP00000002 http_status=200 request_bytes=461671 response_bytes=732 cloud_valid=1 detections=1 ... person_count=1`
  - 多次 `CLOUD_FUSION ... http_status=200`
- 车辆状态说明：
  - 用户补充说明本轮车未开启。
  - `REHAB_CAR ... send_result=...` 仅证明 ESP 侧现有逻辑尝试发送 UART 控制命令，不证明车体真实接收、上电或发生物理运动。
- PC 侧证据：
  - 多次 `192.168.137.6 - "POST /api/v1/vision/fuse HTTP/1.1" 200 OK`。
- schema_version：
  - 本轮日志未直接打印 raw response body。
  - ESP 源码解析器要求 `schema_version == "vision_fusion.v1"` 才通过；运行日志 `cloud_valid=1` 且 `CLOUD_FUSION` 成功。因此运行结果支持响应 schema 通过 `vision_fusion.v1` 校验，但本轮未抓取原始响应体逐字确认。

结论：P4 到 PC `/api/v1/vision/fuse` 真实接通，包含真实 RGB565 图像和真实 ToF JSON。

## 9. P4 到评分服务请求证据

- PC 评分服务启动成功，health 为 `ready`。
- ESP 侧 score bridge 初始化成功。
- 源码实际评分链路为 job API：
  - `POST /api/v1/score/jobs`
  - `PUT /api/v1/score/jobs/{job_id}/csv`
  - `GET /api/v1/score/jobs/{job_id}`
  - `GET /api/v1/score/jobs/{job_id}/result`
- 本轮未看到 P4 对评分服务发起上述请求。
- PC 评分日志中无 `192.168.137.6`、无 `/api/v1/score/jobs` 调用。
- 未使用 legacy 冻结接口：
  - `POST /reset_fatigue_state`
  - `POST /set_baseline_from_rows`
  - `POST /score_trial`

结论：P4 到 PC 评分服务本轮未接通，原因是 IMU/sEMG 未启动导致无真实训练 session 和 CSV。

## 10. 新 session/task/CSV/result 路径

- 未生成新的真实训练 session。
- 未生成新的 task.json。
- 未生成新的真实采样 CSV。
- 未生成新的 score result。
- PC 评分 `runtime_data\sessions` 最新目录仍为历史数据：`20260722_TASK020`，未出现本轮新目录。

## 11. fatigue 回调证据

- fatigue 组件状态可用：
  - `rehab_fatigue_status request_id=0 available=1 fatigue=0`
- 本轮未产生新的真实 score result。
- 因此没有本轮真实评分结果进入 fatigue 的回调证据。

结论：fatigue 可查询，但未收到本轮新评分回调。

## 12. 失败和阻塞项

1. IMU/sEMG 未启动，传感器接收端持续 `required_ok=0`，阻塞真实训练 session。
2. 未生成新的 task.json/CSV/result，因此不能验证 P4 到 PC 评分 job API 的真实运行链路。
3. `vision/fuse` 的 raw response body 未抓取，schema_version 只能通过 ESP 解析成功和源码校验条件支持，不能逐字列出响应体。
4. 本地 HTTP server 仅看到路由注册日志，本轮未调用 P4 本地 HTTP API。
5. NVS 未看到明确初始化成功日志，只能确认固件整体启动和依赖 NVS 的 Wi-Fi 配置流程可运行。
6. 固件内既有 WeChat polling 出现 DNS/连接失败日志；本轮未访问或修改阿里云，也未做云端验证。
7. 用户补充说明车未开启；因此车辆执行链路只能确认到 ESP 侧发出 UART 命令，不能确认真实车体动作。

## 13. 与源码静态审计的差异

- 静态审计认为视觉接口存在；本轮运行验证确认：
  - `/api/v1/person-follow/detect` 真实接通。
  - `/api/v1/vision/fuse` 真实接通。
- 静态审计认为评分 job API 存在；本轮仅确认源码调用路径和服务可用，未验证 P4 真实调用，因为无 IMU/sEMG 和真实训练 CSV。
- 静态审计中医生端与 P4 分离；本轮未接入医生端，未改变该结论。

## 14. 未验证事项

- P4 到 PC 评分 job API 的真实请求。
- 新训练 session 的生成、task.json、CSV、score result。
- fatigue 接收本轮真实评分结果后的更新。
- 医生端接入 P4。
- 阿里云在线状态或云端链路。
- 真实 IMU/sEMG 数据采样。
- 真实车体上电、接收 UART 控制和物理运动。
- P4 本地 HTTP API 的运行态访问。

## 15. 本轮产生的日志位置

仅保存必要运行日志，未复制工程、未创建备份：

- `C:\Users\ZDragon\AppData\Local\Temp\codex_p4_chain_verify\p4_serial_monitor.log`
- `C:\Users\ZDragon\AppData\Local\Temp\codex_p4_chain_verify\p4_person_follow_cli.log`
- `C:\Users\ZDragon\AppData\Local\Temp\codex_p4_chain_verify\p4_follow_target_cli.log`
- 视觉服务日志：`C:\Users\ZDragon\AppData\Local\ESP-Claw\vision_launcher\vision_service.stdout.log`
- 评分服务日志：`C:\Users\ZDragon\AppData\Local\ESP-Claw\score_launcher\score_service.stdout.log`

## 16. 测试结束状态

- 已停止 PC 视觉服务。
- 已停止 PC 评分服务。
- 8000 无残留监听。
- 8020 无残留监听。
- 未删除本地历史 session 数据。
- 未执行 Git 操作。
