# 灵犀智伴——脑卒中上肢康复系统

> 基于 ESP32-P4、ESP32-S3、多模态传感、计算机视觉与康复评估算法的智能上肢康复系统。

本项目面向脑卒中上肢康复训练，将穿戴式 EMG / IMU 采集、边缘训练控制、视觉与空间感知、动作评分和医生 / 患者交互组织为一套系统。本地工作区保存完整工程、当前模型与参数、项目专用环境，以及标定和硬件研发资料，是项目维护、展示、复现和二次开发的总入口。公开根仓库统一保留 P4 / S3 工程、PC 业务代码、算法与模型资产、环境定义及项目示例数据；`esp-claw/` 为项目核心 ESP32-P4 / Hardware Agent 工程，现作为 RehabProject monorepo 的核心子目录统一版本管理。完整环境、`node_modules`、本机凭据、`hardware/`、`external/ESP-Claw_localappdata/` 及 TTS cache/logs 仅本地保留。

在 `esp-claw/` 子目录中直接运行 Git 时会自动使用项目根 Git；只打开该子目录也可以正常查看和提交修改。

**以下文件和目录路径均相对于项目根目录。** 系统关系以 `esp-claw` 中的主工程、正式启动器及其实际加载链为准。

## 1. 项目简介

ESP32-P4 是边缘主控：接收左右穿戴节点的数据，维护传感器状态，控制康复训练与记录流程，协调 Camera、ToF、语音和小车。两个 ESP32-S3 穿戴节点承担上肢运动与肌电采集，第三个 ESP32-S3 驱动康复小车。

PC 侧提供医生端、患者端、训练数据管理与动作评分，并承担 Vision / Identity / Person-Follow、DELTAR 深度估计、目标检测与分割、Camera + ToF 空间几何融合和 TTS 服务。

## 2. 项目成果

项目曾获全国大学生物联网设计竞赛国家级一等奖、企业最佳奖、乐鑫赛道第一，并进入全国八强。

## 3. 系统总体架构与通信

系统由“左右穿戴节点 → P4 主控 → PC 算法与数据服务”和“P4 → S3 小车”组成。PC 内的 Doctor、Score、Vision、Scan、TTS 分别承担不同业务；DELTAR、Detect、Segment 和 Geometry 由 Scan 内部调度。

| 数据链 | 通信方式 / 默认端口 | 主要内容与方向 |
| --- | --- | --- |
| S3 Left / Right → P4 | Wi-Fi UDP 广播，P4 监听 `5005` | Sensor Packet v5；两个节点分别以 100 Hz 发送状态包 |
| P4 → S3 Car | UART，115200 baud | 文本控制命令；P4 使用 UART1，TX GPIO46、RX GPIO47 |
| P4 ↔ Score | HTTP，PC `8020` | 创建评分 job、上传原始训练 CSV、查询状态并取回结果 |
| P4 ↔ Scan | HTTP，PC `8030` | 上传扫描帧与元数据，查询处理状态，取得场景决策包 |
| P4 ↔ Vision / Person-Follow | HTTP，PC `8000` | 图像 / 感知请求与目标检测结果；跟随检测入口为 `/api/v1/person-follow/detect` |
| P4 ↔ TTS | HTTP，PC `8765` | 提交文本，取得音频地址并下载播放 |
| CSV 实时数据流 → Doctor | UDP，PC `6006` | Doctor 的 86 列 CSV 接收入口；也支持本地 CSV tail / replay 数据源 |
| 浏览器 ↔ Doctor | HTTP + WebSocket，后端 `8001` | 数据查询、训练交互，以及 `/ws/live` 实时状态推送 |
| 浏览器 → Frontend | HTTP，`5173` | 医生端 / 患者端界面；前端与 Doctor 后端配合运行 |

主 PC 启动器将 Doctor 配置为 `pc_local` 模式，由 PC 管理数据与界面状态。UDP `6006` 是该模式的数据接收端口；WebSocket 是 Doctor 与浏览器之间的通道。P4 的 Score / Scan HTTP 上传链分别独立运行。

PC 与 P4 通过局域网互通。当前固件和视觉启动器使用的 PC 热点地址为 `192.168.137.1`；服务地址、端口和设备连接以实际网络及当前配置为准。

## 4. 当前本地工作区目录

| 路径 | 用途 |
| --- | --- |
| `esp-claw` | 系统主工程、公共组件、PC 业务代码、启动器及文档，统一由根 Git 管理 |
| `esp-claw/application/edge_agent` | ESP32-P4 主工程 |
| `esp-claw/components` | ESP-Claw 公共框架、Agent 能力和 Lua 模块 |
| `esp-claw/pc` | Doctor / Patient、Score、Vision、TTS 服务 |
| `esp-claw/tools` | 当前 PC 启动工具 |
| `esp-claw/docs` | 框架与开发文档 |
| `esp-claw/.agents` | 工程设计与开发约定 |
| `s3/node_left` | 左侧 EMG + 双 IMU 穿戴节点 |
| `s3/node_right` | 右侧双 IMU 穿戴节点 |
| `s3/car_node` | 小车 Arduino 工程 |
| `s3/libraries/QGP_EVMotor` | 小车电机与手柄控制库 |
| `external/_deltar_formal` | DELTAR 源码、权重、专用环境与验证资料 |
| `external/_deltar_p4_pipeline` | Scan 服务、处理链、模型 manifest 与正式 Geometry |
| `external/_deltar_yolo_cuda` | Scan YOLO 专用环境 |
| `external/_conda_envs` | Score 和 Vision 专用环境 |
| `external/_rehab_scoring_assets` | 评分模板、标准动作、标定样本和派生研发资产 |
| `external/_rehab_training_data` | 公开保留的项目数据结构示例：86 列 CSV、session、评分结果与 Doctor 数据流 |
| `external/_calibration_workspaces` | Camera / ToF / Geometry 标定工程 |
| `external/_deltar_scan_service_data` | 公开保留的 Scan 本地数据与回归样例，包括 RGB / ToF / metadata、progress 和 performance guard |
| `external/P4RehabTTS` | TTS 本地运行目录；venv、cache 和 logs 不随根仓库发布 |
| `external/ESP-Claw_localappdata` | PC 启动器状态与日志，仅本地保留 |
| `external/model_provenance` | 模型来源与导入资料 |
| `external/geometry_validation` | Geometry 专项验证资料 |
| `hardware` | 硬件文档、接线资料及厂商工具，仅本地保留 |

各子工程保留自身的源码、配置、资源和文档组织。模型来源、标定过程与运行数据分别保存在对应资料目录中。

## 5. ESP32-P4 主控

工程入口：`esp-claw/application/edge_agent`。

当前板卡配置为 **ESP32-P4 Function-EV Board**（`esp32_p4_function_ev`），target 为 `esp32p4`，工程配置使用 **ESP-IDF 5.5.4**。板级定义位于 `esp-claw/application/edge_agent/boards/espressif/esp32_p4_function_ev`。

主要模块包括：

- Sensor RX / Runtime：接收左右 S3 的状态包，统一 EMG、4 个 IMU 及节点质量状态。
- Recorder / Training：以统一时间轴形成训练 CSV，管理训练 session 与任务流程。
- Score Bridge：管理 PC 评分 job 的创建、CSV 上传和结果同步。
- Camera / VL53L5CX ToF：采集图像与 8×8 ToF 数据，支撑视觉和空间扫描。
- Scan Capture / Transfer / Environment Scan：组织多方向采集、传输与场景决策。
- Person-Follow / Target-Follow / Car：组织目标感知、跟随决策和车辆控制，动作输出按固件开关与控制模式管理。
- UI / Audio / Lua / Agent：提供本地交互、语音输出、脚本和智能体能力。

主入口为 `esp-claw/application/edge_agent/main/main.c`。构建配置、分区、依赖锁定、managed components 和 FATFS 资源均随工程保留；具体说明见 [P4 子工程](esp-claw/application/edge_agent/README.md)。

## 6. S3 Left：EMG + 双 IMU

路径：`s3/node_left`；开发框架为 **ESP-IDF 5.5.4**，target 为 `esp32s3`。

节点固定为 `node_id = 1`，通过 SPI 读取两个 ADS1292，共 **4 路 EMG**，并通过 UART 读取 **2 个 WT901C-TTL IMU**。ADS1292 的当前本地采样率为 250 SPS；节点每 10 ms 汇总 EMG 窗口，将 latest / RMS / MAV / peak 等特征与最新 IMU 状态打包，以 100 Hz UDP 广播发送至 P4 的 `5005` 端口。

接线与节点说明见 [Left Node](s3/node_left/README.md)。

## 7. S3 Right：双 IMU

路径：`s3/node_right`；开发框架为 **ESP-IDF 5.5.4**，target 为 `esp32s3`。

节点固定为 `node_id = 2`，采集 **2 个 WT901C-TTL IMU**，在 P4 融合结果中对应 IMU3 / IMU4。当前主程序只初始化双 IMU，以 100 Hz UDP 广播发送 Sensor Packet v5 至 P4 的 `5005` 端口。

接线与节点说明见 [Right Node](s3/node_right/README.md)。

## 8. S3 Car：康复小车

工程目录：`s3/car_node`。实际 sketch 为 `s3/car_node/arduino/QGP_Car_TextCommand_A0/QGP_Car_TextCommand_A0.ino`，使用 **Arduino / ESP32 Core**，面向 **ESP32-S3**。

电机与手柄库为 `s3/libraries/QGP_EVMotor`。正式启动模式是 **SERIAL**：S3 从串口接收 `PAD,...`、`STOP`、状态与模式命令，再驱动底盘。P4 主程序明确选择 UART 控制后端；小车以 115200 baud 解析串口文本。

小车还保留显式选择的 `MODE BLE` 手柄测试模式和 `MODE UDP` 网络测试模式，通过 `MODE SERIAL` 返回串口模式。正式 P4 → S3 控制关系以当前 P4 主入口和 sketch 为准。

## 9. 传感数据链

两个穿戴节点使用统一的 **Sensor Packet v5**，由 P4 Sensor RX 按节点编号接收并维护在线状态、时序和数据有效性。左节点提供 EMG + IMU1 / IMU2，右节点提供 IMU3 / IMU4。

P4 Recorder 以 **50 Hz** 将两侧状态对齐到统一训练时间轴，输出 **86 列 CSV**，内容包括序号、时间、节点接收质量、EMG 窗口特征、IMU 姿态、角速度、加速度和线加速度。EMG 的 250 SPS 是节点本地原始采样率；训练 CSV 保存窗口统计特征。

CSV 表头以 `esp-claw/application/edge_agent/components/rehab_sensor_recording/src/rehab_training_recorder.c` 为准，Doctor 的 UDP 与本地 CSV 解析共享相同列定义。正式评分读取原始完整训练 CSV；界面回放使用后端数据源和 WebSocket 展示。

## 10. PC 医生端与患者端

主工程：`esp-claw/pc/rehab_doctor_twin`，包含 Python 后端和前端界面。

- **医生端**：训练数据总览、session 记录、原始日志、动作还原、评分报告和处方相关界面。
- **患者端**：实时传感状态、训练交互、波形与动作展示、报告和 CSV 回放。
- **数据服务**：维护 PC runtime、训练记录、Score 结果和 WebSocket 推送。

正式主启动器以 `pc_local` 模式启动 Doctor。实时输入默认使用 UDP `6006`，并支持 `csv_tail` / `csv_replay`；保存的 session CSV 可供患者端回放。回放控制用于数据展示，评分按完整原始 CSV 的 job 流程执行。

启动后访问 [角色选择](http://127.0.0.1:5173/)、[医生端](http://127.0.0.1:5173/doctor/overview) 或 [患者端](http://127.0.0.1:5173/patient)。后端监听 `8001`，实时推送路径为 `/ws/live`。子工程说明见 [Doctor / Patient](esp-claw/pc/rehab_doctor_twin/README.md)。

## 11. Score：康复动作评分

服务源码：`esp-claw/pc/rehab_score_service`，正式启动入口加载 `cloud_interface.score_server:app`，默认端口 `8020`。

当前任务 authority 为 `esp-claw/pc/rehab_score_service/config/rehab_task_registry_v1.json`，共 **5 个正式任务**：

1. 水杯拿取与饮水训练（二级）。
2. 水杯拿取与饮水训练（三级）。
3. 水杯横向取放训练（一级）。
4. 球类横向取放训练（一级）。
5. 球类横向取放训练（二级）。

基本流程为：**创建 job → 上传原始 CSV → Score Worker → registry 选择 dedicated scorer → 动作周期划分与评分 → session 聚合 → 状态 / 结果返回**。P4 Score Bridge 与 Doctor 按各自业务使用同一评分服务。

当前正式评分路径：

| 内容 | 路径 |
| --- | --- |
| Job 评分适配器 | `esp-claw/pc/rehab_score_service/cloud_interface/dedicated_job_score_adapter.py` |
| 评分资产解析 | `esp-claw/pc/rehab_score_service/scoring_assets.py` |
| 饮水健康模板 | `external/_rehab_scoring_assets/templates/MHH_healthy_template.json` |
| 横向取放健康模板及内嵌 `score_profile` | `external/_rehab_scoring_assets/templates/lateral_transfer_healthy_template_v1.json` |
| 横向取放运行参数定义 | `esp-claw/pc/rehab_score_service/feature_extractors/lateral_transfer_profile.py` |

模板、标准动作及标定样本集中于 `external/_rehab_scoring_assets`；训练事实与 job 数据位于 `external/_rehab_training_data`。评分入口按 registry 和 adapter 选择模板、参数与 session scorer。详细说明见 [Score 子工程](esp-claw/pc/rehab_score_service/README.md)。

## 12. Vision / Identity / Person-Follow

服务源码：`esp-claw/pc/vision_interpreter`。视觉启动器以 `app.main:app` 启动服务，默认端口 `8000`。

当前配置 authority 为 `esp-claw/pc/vision_interpreter/config/vision_config.json`，由 `esp-claw/pc/vision_interpreter/app/config.py` 解析模型路径。服务包含目标检测、Identity 分类、RGB + ToF 融合与 Person-Follow 检测。P4 将采集的图像交给 PC 检测，并在设备侧结合距离和状态组织跟随逻辑。

| 当前用途 | 实际模型路径 |
| --- | --- |
| 通用检测与 Person-Follow 人体检测 | `esp-claw/pc/vision_interpreter/models/yolo11n.pt` |
| Identity 二分类权重 | `esp-claw/pc/vision_interpreter/models/identity/best_zxl_identity.pt` |
| Identity 分类网络基础权重 | `esp-claw/pc/vision_interpreter/models/identity/yolo11n-cls.pt` |

Person-Follow 展示入口为 [Person-Follow View](http://127.0.0.1:8000/person-follow-view)。配置字段和接口说明见 [Vision 子工程](esp-claw/pc/vision_interpreter/README.md)。

## 13. Scan / DELTAR / Detect / Segment

Scan Pipeline 位于 `external/_deltar_p4_pipeline`，服务入口为 `external/_deltar_p4_pipeline/scripts/run_scan_service.py`，默认端口 `8030`。DELTAR 源码位于 `external/_deltar_formal/work/deltar_f3c5175_win_cu128`。

P4 支持 **11 方向扫描**。Scan 接收 RGB、ToF 和扫描元数据后，调度 DELTAR 深度估计、YOLO Detect / Segment、正式 Geometry、Scene Builder 和多方向 Aggregate，形成返回 P4 的 Decision Package。DELTAR 使用图像与 ToF 信息生成稠密深度，Geometry 将深度、目标区域及标定关系用于空间测量。

当前正式模型由 `external/_deltar_p4_pipeline/profiles/model_manifest.json` 声明，加载器按 manifest 所在目录解析路径并核对模型 SHA-256：

| 模型任务 | 当前权重路径 |
| --- | --- |
| DELTAR Depth | `external/_deltar_formal/assets/nyu.pt` |
| Scan Detect | `external/_deltar_p4_pipeline/models/rehab_scan_yolo11n_coco80_volleyball_detect_v5b_exp.pt` |
| Scan Segment | `external/_deltar_p4_pipeline/models/yolo11n-seg.pt` |

DELTAR 的默认源码和 checkpoint 同时由 `external/_deltar_p4_pipeline/src/deltar_p4_pipeline/frozen_runtime_contracts.py` 定义，YOLO worker 从 profile bundle 取得当前模型。模型文件名中的版本标记保留原样，当前选择以 manifest 和 loader 为准。

统一操作界面为 [Vision Console](http://127.0.0.1:8030/vision-console)。具体数据格式和处理细节见 [Scan Pipeline](external/_deltar_p4_pipeline/README.md)。

## 14. Formal Geometry

正式 Geometry 目录为 `external/_deltar_p4_pipeline/profiles/formal_geometry_v2`。加载入口为 `external/_deltar_p4_pipeline/src/deltar_p4_pipeline/formal_geometry_authority.py`。

| 正式文件 | 用途 |
| --- | --- |
| `external/_deltar_p4_pipeline/profiles/formal_geometry_v2/authority.json` | profile 身份、量纲、坐标约定与 promoted payload 哈希 |
| `external/_deltar_p4_pipeline/profiles/formal_geometry_v2/stage2_calibration.json` | 相机内参、Camera / ToF 标定关系及 DELTAR 输入映射 |
| `external/_deltar_p4_pipeline/profiles/formal_geometry_v2/stage3a_gravity.json` | 相机重力方向与安装高度 |
| `external/_deltar_p4_pipeline/profiles/formal_geometry_v2/all_object_geometry_v2.json` | 目标区域选择与空间几何计算参数 |

运行时直接读取正式目录中的 promoted payload，并校验单文件和整组 SHA-256。当前深度距离语义为 `axial_z`，几何长度单位为 mm。

`external/_calibration_workspaces` 保存标定采集、求解、候选参数及研究过程，用于参数追溯和重新标定。正式运行参数由上述 authority 与 loader 决定。

## 15. TTS 语音服务

服务源码位于 `esp-claw/pc/rehab_tts_service`，入口为 `rehab_tts_service.app:app`。运行目录为 `external/P4RehabTTS`，其中保存 venv、音频缓存、日志与进程状态。

P4 通过 PC `8765` 端口的 HTTP 服务提交文本并获取音频，设备侧完成下载与播放。当前语音提供者为 `edge-tts`；生成新语音使用其在线服务，生成的 MP3 由本地服务缓存和提供。接口说明见 [TTS 子工程](esp-claw/pc/rehab_tts_service/README.md)。

## 16. PC 启动方式

在 Windows 上，从项目目录直接使用以下正式启动器：

| 启动内容 | 操作入口 | 行为 |
| --- | --- | --- |
| Doctor / Patient / Frontend / Score | `esp-claw/tools/ESP_Claw_PC_Launcher/START_ESP_CLAW_PC.cmd` | 按顺序启动 Score、Doctor 后端和前端，并打开角色选择页 |
| Vision + Scan | `esp-claw/tools/Start-VisionAndScanPC/Start-VisionAndScanPC_SAME_FOLDER_FIXED.cmd` | 调用同目录 PowerShell 脚本，启动视觉和扫描服务，检查就绪状态，发现 P4 并打开 Vision Console |
| TTS | `esp-claw/tools/P4_Rehab_TTS_Launcher/P4_Rehab_TTS_Launcher.cmd` | 打开管理窗口，点击 **Start** 启动服务，使用 **Stop** 停止服务 |

主 PC 启动器调用 `esp-claw/pc/rehab_doctor_twin/scripts/start_pc_only.ps1`，默认使用项目内 Doctor / Score 环境和前端依赖。Python 路径覆盖配置位于 `esp-claw/tools/ESP_Claw_PC_Launcher/launcher_config.cmd`。

视觉启动器调用 `esp-claw/tools/Start-VisionAndScanPC/start_vision_and_scan_services.ps1`；Scan 再调度 DELTAR 和 YOLO worker。Vision / Scan 启动时按当前网络设置提供 PC 热点地址，连接 P4 后用于实际采集与扫描。

本地开发可使用 Doctor 的 session CSV 回放、Score 原始 CSV 输入，以及视觉 / 扫描子工程的本地文件工具。CSV 回放按原始时间轴展示数据，评分仍使用完整原始文件。TTS 的新语音合成按其 provider 使用网络。

## 17. 嵌入式工程入口

| 工程 | 路径 | 框架 / target |
| --- | --- | --- |
| P4 | `esp-claw/application/edge_agent` | ESP-IDF 5.5.4 / `esp32p4` |
| S3 Left | `s3/node_left` | ESP-IDF 5.5.4 / `esp32s3` |
| S3 Right | `s3/node_right` | ESP-IDF 5.5.4 / `esp32s3` |
| S3 Car | `s3/car_node/arduino/QGP_Car_TextCommand_A0` | Arduino / ESP32 Core，ESP32-S3 |

P4 使用 ESP-IDF 构建。公开配置依据为 [通用默认配置](esp-claw/application/edge_agent/sdkconfig.defaults)、[P4 默认配置](esp-claw/application/edge_agent/sdkconfig.defaults.esp32p4)、[Function-EV 板卡定义](esp-claw/application/edge_agent/boards/espressif/esp32_p4_function_ev/board_info.yaml)、[板级默认配置](esp-claw/application/edge_agent/boards/espressif/esp32_p4_function_ev/sdkconfig.defaults.board)、[构建入口](esp-claw/application/edge_agent/CMakeLists.txt)和[组件依赖清单](esp-claw/application/edge_agent/main/idf_component.yml)。`sdkconfig` 在本地板卡配置、menuconfig / 构建过程中生成，不作为仓库固定文件提交；配置与构建步骤见 [P4 子工程](esp-claw/application/edge_agent/README.md)。穿戴节点的接线、节点编号及采集说明见各自 README；小车从 sketch 进入，并配合项目内 QGP_EVMotor 库使用。

## 18. 项目专用环境与系统工具链

Windows 克隆时，为该仓库启用 Git 长路径支持（不修改全局设置）：

```powershell
git -c core.longpaths=true clone --config core.longpaths=true https://github.com/weiwoking/lingxi-rehab-agent.git
```

初次 clone 后，先恢复不随 Git 再分发的第三方运行依赖，再安装环境和按对应工程入口运行：

```powershell
.\scripts\fetch-third-party-assets.ps1
.\scripts\setup-pc-environments.ps1
# 只检查依赖是否齐全（不联网、不启动服务）
.\scripts\fetch-third-party-assets.ps1 -CheckOnly
```

恢复脚本兼容 Windows PowerShell 5.1 和 PowerShell 7+。`-CheckOnly` 不下载文件；尚未恢复的依赖会列为 `MISSING` 并返回非零退出码，恢复齐全且哈希匹配后返回成功。

恢复脚本从官方来源获取 DELTAR NYU 权重、QGP 1.0.0 core 和左右 S3 的 WitMotion C SDK，按固定提交/包和 SHA256 核验；已有且匹配的文件跳过，未知或不同内容报错且不覆盖。运行 Scan 前需恢复两份 `nyu.pt`；编译 Car 前需恢复 QGP；编译左右 S3 前需恢复 WitMotion。脚本不构建、不运行服务，也不下载可选 demo / ST 文档。可用 `-Components DELTAR`、`QGP` 或 `WitMotion` 只恢复对应依赖。官方链接、全部预期路径和哈希见 [第三方本地资产清单](third_party/local-only-assets.csv)。上游撤回下载或更新包导致哈希改变时应停止并重新核查，不改用未知镜像。

Git 仓库不提交完整 Python / Conda / Node 依赖环境。本机已有环境继续保留；根 Git 范围内的 5 套环境定义见 [environment/README.md](environment/README.md)。初次 clone 后在项目根运行下列脚本创建环境；已有目标默认跳过。`esp-claw/` 统一由根 Git 管理，其 Doctor / 前端依赖仍按该工程说明安装。

```powershell
.\scripts\setup-pc-environments.ps1
```

当前正式启动链使用下列项目专用环境：

| 用途 | 环境目录 | 解释器入口 |
| --- | --- | --- |
| Doctor 后端 | `esp-claw/pc/rehab_doctor_twin/backend/.venv` | `esp-claw/pc/rehab_doctor_twin/backend/.venv/Scripts/python.exe` |
| Score | `external/_conda_envs/rehab_score` | `external/_conda_envs/rehab_score/python.exe` |
| Vision 与 Scan 服务进程 | `external/_conda_envs/vision_interpreter` | `external/_conda_envs/vision_interpreter/python.exe` |
| DELTAR worker | `external/_deltar_formal/envs/deltar_py311_torch271_cu128` | `external/_deltar_formal/envs/deltar_py311_torch271_cu128/python.exe` |
| Scan YOLO worker | `external/_deltar_yolo_cuda/venv` | `external/_deltar_yolo_cuda/venv/Scripts/python.exe` |
| TTS | `external/P4RehabTTS/venv` | `external/P4RehabTTS/venv/Scripts/python.exe` |

前端依赖保存在 `esp-claw/pc/rehab_doctor_twin/frontend/node_modules`。DELTAR 环境对应 Python 3.11 / PyTorch 2.7.1 / CUDA 12.8；Scan worker 的设备与 GPU 策略由当前 runtime 配置控制。

**系统级开发工具由开发电脑提供**：ESP-IDF、Arduino / ESP32 Core、Node.js / npm / pnpm、基础 Python / 环境管理工具，以及与项目 GPU 计算环境匹配的 NVIDIA 驱动和 CUDA 支持。项目专用 Python 环境与系统工具链分别管理；环境依赖信息可结合各服务的 requirements、前端 package / lock 文件及嵌入式依赖锁定文件读取。

## 19. 硬件资料

硬件型号、接口和项目相关说明在本 README 与子工程文档中记录。完整厂商手册、调试工具和本地硬件资料保存在开发者本地工作区的 `hardware/`，不作为公开根仓库内容发布。

QGP 电机与手柄控制实现随 `s3/libraries/QGP_EVMotor` 保存。板级外设定义与固件参数以各嵌入式工程的当前配置为准。

## 20. 当前版本与 authority 判断

维护时沿以下顺序追踪当前运行版本：**`esp-claw` → 正式 launcher / P4 主入口 → service entrypoint → 当前 config → manifest / registry → loader → 实际 runtime asset**。

| 范围 | 当前 authority |
| --- | --- |
| P4 主控与外设 | P4 主入口、已提交的默认配置与板卡定义、构建与依赖清单；实际构建采用本地生成配置 |
| S3 穿戴节点 | 各自 main、Sensor Packet v5 与 P4 接收协议 |
| S3 小车 | P4 主入口选择的 UART 后端，以及当前 Arduino sketch |
| Doctor / Patient / Score 启动 | 主 PC CMD 和其调用的 `start_pc_only.ps1` |
| Score 任务与算法 | 当前 registry、dedicated job adapter、实际调用的 scorer、模板及参数模块 |
| Vision / Identity / Person-Follow | 当前 vision config 及 detector / identity loader |
| Scan 模型 | 当前 model manifest、profile loader 与 worker 加载链 |
| Formal Geometry | 正式 Geometry authority、promoted payload 与校验 loader |
| TTS | TTS launcher、服务配置和 provider |

文件修改时间、目录中的版本名及研发候选用于追溯；判断当前运行内容时，读取正式入口最终解析到的资源和参数。

## 21. 维护与二次开发

本地工作区保留完整工程、专用环境、当前模型、标定工作空间和硬件工具，支持长期维护与研发复现；公开根仓库范围见上文说明。进入子工程后，结合其源码、README / docs、当前配置和依赖文件开展工作。

调整传感协议时同步核对两个 S3 与 P4；调整训练数据列定义时同步核对 Recorder、Doctor 和 Score。更新模型或几何参数时，沿正式配置和加载链管理资源身份与哈希。训练数据存储与评分资产分别管理，便于追溯训练事实和评估基准。

研发参考主要位于 `external/_calibration_workspaces`、`external/_deltar_formal/evidence`、`external/model_provenance`、`external/geometry_validation` 和各子工程文档。PC 运行状态与日志查看 `external/ESP-Claw_localappdata`、`external/_deltar_scan_service_data` 及 `external/P4RehabTTS`。

## 22. Quick Start / 快速入口

1. **查看界面和评分服务**：运行 [主 PC 启动器](esp-claw/tools/ESP_Claw_PC_Launcher/START_ESP_CLAW_PC.cmd)，在打开的页面选择医生端或患者端。
2. **启动视觉与空间扫描**：运行 [Vision / Scan 启动器](esp-claw/tools/Start-VisionAndScanPC/Start-VisionAndScanPC_SAME_FOLDER_FIXED.cmd)，进入 Vision Console。
3. **启动语音**：运行 [TTS 管理器](esp-claw/tools/P4_Rehab_TTS_Launcher/P4_Rehab_TTS_Launcher.cmd)，点击 **Start**。
4. **进入嵌入式工程**：P4 使用 `esp-claw/application/edge_agent`，穿戴节点使用 `s3/node_left` / `s3/node_right`，小车使用 `s3/car_node/arduino/QGP_Car_TextCommand_A0`。
5. **查找正式算法与参数**：Score 从 registry 与 dedicated adapter 进入，Vision 从 config 进入，Scan 从 model manifest 进入，Geometry 从正式 authority 进入；对应文件见上文各模块章节。

## License

Unless otherwise noted, original RehabProject code is licensed under the [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE).

Third-party software, models, documentation, and assets retain their original licenses and terms. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). MPU6050_light / PubSubClient retain MIT notices; encoder headers retain their upstream four-condition BSD-style license and acknowledgment. DELTAR weights/demo, QGP core, WitMotion SDK and two ST PDFs are local-only and excluded from this Git distribution; public download availability is not a redistribution grant. Required runtime files are restored directly from official sources by the setup command in section 18.
