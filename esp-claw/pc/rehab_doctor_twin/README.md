# 灵犀智伴｜脑卒中上肢康复医生端数字孪生系统

全国大学生物联网设计竞赛 PC 医生端演示基线。当前版本只实现医生端：FastAPI 演示网关通过 WebSocket 推送结构化训练数据，React 医生端显示 Three.js 数字孪生、表面肌电归一化活动强度、动作质量、代偿提醒和专业康复报告。

## 竞赛演示目标

- 一键开始、重置和生成报告。
- 无真实硬件、无阿里云、无真实评分服务时稳定运行。
- 明确显示 `DEMO DATA`，不声称临床验证、真实 IMU/sEMG 或 AI 姿态预测。
- 为后续 ESP32-P4、IMU、sEMG、评分服务和 Blender 绑定 GLB 保留接口。

## 技术架构

P4/真实设备（后续） → PC Adapter（后续） → FastAPI Gateway → WebSocket `/ws/live` → React 医生端 → Three.js 数字孪生 → 专业报告。

本轮新增医生工作台与患者工作区：

- `/`：医生工作台首页。
- `/patients/P-0217/live`：实时数字孪生监护。
- `/patients/P-0217/devices`：独立设备状态。
- `/patients/P-0217/logs`：原始数据日志。
- `/patients/P-0217/reports`：历史会话与报告入口。
- `/report/current`：当前报告预览兼容入口。

## 页面截图预留

比赛录屏建议在 1920×1080、浏览器缩放 100% 下录制实时监护页和报告预览页。当前仓库不内置截图文件。

## 目录说明

- `frontend/`：React、TypeScript、Vite、Three.js 医生端。
- `backend/`：FastAPI、WebSocket、演示引擎、报告服务。
- `demo/`：拿杯喝水演示场景与报告模板。
- `docs/`：架构、WebSocket、模型集成、模型署名和比赛操作文档。
- `scripts/`：安装和启动脚本。

## 安装步骤

```powershell
cd C:\ev\rehab_doctor_twin
.\scripts\install.ps1
```

如果当前终端没有 Node/npm，脚本会给出提示；可安装 Node.js，或在当前终端 PATH 中加入可用 Node/npm 后重试。

## 后端启动

```powershell
cd C:\ev\rehab_doctor_twin
.\scripts\start_backend.ps1
```

健康检查：`http://127.0.0.1:8000/health`

## 前端启动

```powershell
cd C:\ev\rehab_doctor_twin
.\scripts\start_frontend.ps1
```

医生端：`http://127.0.0.1:5173`

## 一键启动

```powershell
cd C:\ev\rehab_doctor_twin
.\scripts\start_all.ps1
```

## 演示流程

1. 启动后端和前端。
2. 进入 `http://127.0.0.1:5173`。
3. 确认顶部显示 `DEMO DATA`。
4. 点击“开始演示”或按 Space。
5. 观察状态进入 `PREPARING`、`RUNNING`，完成 5 次动作。
6. 第 3 次动作出现“检测到轻度躯干前倾”黄色代偿提醒。
7. 训练结束进入 `REPORT_READY`。
8. 点击“生成专业报告”，查看报告。
9. 返回实时监护页并点击“重置”。

## WebSocket 协议

消息统一包含 `schema_version`、`type`、`device_id`、`patient_id`、`session_id`、`seq`、`timestamp_ms`、`source_mode` 和 `payload`。详见 `docs/WEBSOCKET_PROTOCOL.md`。

## body.glb 放置方式

当前仓库未内置人体模型。若获取到 Body Anatomy 3D Viewer 官方 `body.glb`，放入：

```text
frontend/public/models/body.glb
```

模型缺失时页面显示 `Anatomical model integration pending.`，不会伪装已完成 Z-Anatomy 集成。

## DRACO 资源放置方式

DRACO 解码文件放入：

```text
frontend/public/draco/
```

安装依赖后可从 `frontend/node_modules/three/examples/jsm/libs/draco/` 复制解码资源。

## 模型许可

如果加入 Body Anatomy 3D Viewer 或 Z-Anatomy 派生模型，网页和文档必须显示：

`3D anatomical model derived from Z-Anatomy and Body Anatomy 3D Viewer, licensed under CC BY-SA 4.0.`

后续 Blender 裁剪、绑定和简化产生的模型仍需遵守相同许可。详见 `docs/MODEL_ATTRIBUTION.md`。

## 当前演示功能

- 医生工作台首页和患者专属工作区导航。
- FastAPI `/health`、场景列表、开始、重置、当前状态、当前报告。
- 患者 API、会话 API、原始日志分页查询、CSV/JSONL 导出。
- WebSocket 多客户端广播、断线不终止演示、慢客户端有界队列。
- 后端 10 Hz 插值生成关节角、肌电、事件、评分和报告。
- 后端后台队列追加写入 `imu_raw.jsonl`、`emg_raw.jsonl`、`events.jsonl`、`scores.jsonl`、`device_status.jsonl`。
- React 大屏实时监护页、专业报告页和浏览器打印样式。
- Three.js 场景、GLTFLoader、DRACOLoader、OrbitControls、参考网格、标准轨迹、实际运动学参考骨架。
- `modelManifest.ts` 统一业务解剖节点映射。
- `JointRigAdapter` 为后续 Blender 骨骼模型和 IMU offset 预留。

## 未接入功能

- 未接真实 ESP32-P4。
- 未接真实 IMU。
- 未接真实 sEMG。
- 未接阿里云。
- 未接真实评分服务。
- 未调用 LLM。
- 未生成 PDF。

## 后续接入点

- P4：在 `backend/app/demo_engine.py` 旁新增真实设备 adapter，保持 P4 为训练状态唯一可信来源。
- IMU：将真实关节角写入 WebSocket `rehab.telemetry.payload`，映射入口为 `frontend/src/twin/JointRigAdapter.ts`。
- sEMG：将归一化活动强度写入 `rehab.emg.payload`，前端入口为 `frontend/src/twin/MuscleHighlighter.ts`；原始日志入口为 `backend/app/raw_log_service.py`。
- 评分服务：保持 `ROM_score` 字段大小写，替换后端 `rehab.score.payload.score` 构造逻辑。
- Blender 绑定模型：替换 `frontend/public/models/body.glb`，骨骼名使用 `root/spine/clavicle_R/upper_arm_R/forearm_R/hand_R`。

## 常见故障排查

- 前端无法启动：确认 Node/npm 可用，并已在 `frontend/` 安装依赖。
- 后端无法启动：确认 `backend/.venv` 已创建并安装 `requirements.txt`。
- 模型未显示：确认 `frontend/public/models/body.glb` 存在，DRACO 文件位于 `frontend/public/draco/`。
- 报告按钮不可用：需要等待演示完成进入 `REPORT_READY`。
- 重复开始失败：设计上同一时间只允许一个演示实例，先点击重置。

## 国赛演示收尾说明（2026-07-22）

本版本新增医生首页一键国赛演示入口，实时页采用 `body.glb` + 分段解剖网格运动映射。姿态由4个IMU相对四元数映射，sEMG固定为 CH1/CH2/CH3/CH4 四路业务肌肉归一化活动度。演示数据保持 `DEMO DATA` 标识，不代表真实临床结果。
