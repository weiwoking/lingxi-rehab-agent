# Context Recovery Report

生成时间：2026-07-23

本报告用于恢复上一轮 Codex 对 ESP-Claw、PC 视觉服务、PC 评分服务、医生端 PC UI 与审计文档的处理上下文。除新增本报告外，本轮未修改 ESP-Claw 工程源码、未回退、未构建、未烧录、未访问阿里云。

## 1. 当前工程状态

### ESP-Claw

- 路径：`C:\ec\esp-claw`
- 当前分支：`master`（由 reflog `checkout ... to master` 推断）
- 当前 HEAD：`3dae9be feat(rehab-ui): show realtime person follow diagnostics`
- 工作区：有未提交改动；`git status --porcelain=v1` 为 19 条状态项。
- 暂存区：4 个 `application/edge_agent/p4_scan_serial_*.log` 新增文件已暂存，其中 `p4_scan_serial_20260723_182305.log` 处于 `AD` 状态（暂存新增、工作区删除）。
- 未跟踪：`application/edge_agent/build.stale_v55_20260723_2118/` 与 `tools/vision_launcher/Bootstrap-VisionPC.ps1`。
- `dependencies.lock`：未发现 Git diff。
- `sdkconfig`：已被 ESP-IDF 6.0.0 重新生成，且当前文件头为 `Espressif IoT Development Framework (ESP-IDF) 6.0.0 Project Configuration`。

### 医生端 PC UI

- 路径：`C:\ev\rehab_doctor_twin`
- 当前 HEAD：`4d3d51b 终版`
- 工作区：`git status --porcelain=v1` 为 3 条状态项。
- 未跟踪项：两个 demo session 目录，以及整个 `docs/integration_audit/` 目录。
- `git diff --stat` 与 `git diff --cached --stat` 均为空。

## 2. 当前 Git 分支与 commit

### ESP-Claw 最近提交

1. `3dae9be feat(rehab-ui): show realtime person follow diagnostics`
2. `7874d55 feat(rehab-ui): control person follow from display`
3. `1fc6983 feat(rehab-ui): add home car debug and follow entry`
4. `ac5d516 fix(rehab): require realtime tools for chat control`
5. `ea51dda feat(rehab-ui): show fatigue summary on home`

reflog 显示最近发生过 `master -> ac5d516 -> 8aa863e -> master` 的 checkout 往返，没有发现本轮执行回退。

### 医生端最近提交

1. `4d3d51b 终版`
2. `287c0a4 demo动作`
3. `6942735 初版`

## 3. 当前未提交修改

### ESP-Claw 工作区改动

- 9 个 rehab 组件 `CMakeLists.txt`：`json` 依赖改为 `espressif__cjson`。
- `application/edge_agent/components/rehab_score_bridge/src/rehab_score_bridge_storage.c`：SHA-256 实现从 `mbedtls/sha256.h` 及版本分支 API 改为 `mbedtls/md.h` 通用摘要 API。
- `application/edge_agent/sdkconfig`：从 ESP-IDF 5.5.4 格式/内容变为 ESP-IDF 6.0.0 生成内容，diff 很大。
- `tools/vision_launcher/Start-VisionPC.ps1`：增加 Python/Conda/端口/日志路径发现逻辑，并调整热点检查顺序。
- `tools/vision_launcher/README.md`：更新为项目 `.venv` 优先、Conda 可选后备、支持 `VISION_*` 环境变量。
- `tools/vision_launcher/Bootstrap-VisionPC.ps1`：新增视觉 `.venv` bootstrap 脚本。
- `application/edge_agent/build.stale_v55_20260723_2118/`：未跟踪 build cache 目录，名称含 v55，但实测 `project_description.json` 仍指向 v6.0 IDF。
- `application/edge_agent/p4_scan_serial_*.log`：串口日志暂存/工作区状态混合，属于现场日志，不应与源码回退混淆。

### 医生端工作区改动

- `docs/integration_audit/`：未跟踪审计目录，本报告也位于此目录。
- `backend/data/patients/P-0217/sessions/SESSION_DEMO_20260723_092013_2e609f/`：未跟踪 demo session。
- `backend/data/patients/P-0217/sessions/SESSION_DEMO_20260723_092314_54da88/`：未跟踪 demo session。

## 4. 已完成的环境恢复

依据审计文档与现场文件核对，上一轮报告称已完成：

- 修复视觉启动器不再强依赖 `C:\ProgramData\anaconda3\Scripts\conda.exe`。
- 本机实际 Conda：`E:\rengongzhineng\Anaconda\install\Scripts\conda.exe`。
- 已创建 Conda prefix：`C:\ec\_conda_envs\vision_interpreter` 与 `C:\ec\_conda_envs\rehab_score`。
- 视觉启动器支持 `VISION_PYTHON`、项目 `.venv`、`VISION_CONDA_ENV`、`VISION_CONDA_EXE`、PATH fallback、`VISION_PORT`、`VISION_HOST` 等。
- 新增 `tools/vision_launcher/Bootstrap-VisionPC.ps1`。
- 审计文档记录视觉 health/openapi/person-follow、评分 check/smoke、医生端 `/health`、前端 typecheck/build/dev HTTP 200 曾通过。

这些恢复主要属于 PC 环境与启动器自动发现，应原则上保留；不要因为回退 v6 固件兼容改动而删除。

## 5. 当前 ESP-IDF 环境

### 当前 shell

- `IDF_PATH`、`IDF_PYTHON_ENV_PATH`、`IDF_TOOLS_PATH`：当前 shell 未导出。
- `idf.py --version`：当前 PATH 中找不到 `idf.py`。
- `where.exe idf.py/python/cmake/ninja`：`idf.py/cmake/ninja` 未找到；`python` 指向 `C:\Users\ZDragon\scoop\apps\python313\current\python.exe`。
- `python -c`：Python `3.13.12`。

### 实际已安装 IDF

- 实际 v5.5.4 路径：`E:\esp\.espressif\v5.5.4\esp-idf`，不是附件中写的 `E:\esp.espressif\v5.5.4\esp-idf`。
- v5.5.4 profile：`C:\Espressif\tools\Microsoft.v5.5.4.PowerShell_profile.ps1`，其中 `IDF_PATH=E:\esp\.espressif\v5.5.4\esp-idf`，`IDF_PYTHON_ENV_PATH=C:\Espressif\tools\python\v5.5.4\venv`。
- v5.5.4 Git：`v5.5.4-dirty`，有 4 个本地补丁文件：
  - `components/esp_http_client/esp_http_client.c`
  - `components/esp_http_client/include/esp_http_client.h`
  - `components/esp_lcd/parl/esp_lcd_panel_io_parl.c`
  - `components/usb/include/usb/usb_types_ch9.h`
- v6.0 路径：`E:\esp\.espressif\v6.0\esp-idf`。
- v6.0 Git：`v6.0-dirty`，至少 `components/esp_lcd/parl/esp_lcd_panel_io_parl.c` 有本地改动。
- 直接运行两套 `idf.py --version` 均打印版本，但返回码为 1，错误为查找 `C:\Users\ZDragon\.espressif\python_env\...` 下的 Python env 失败；profile 文件自身指向 `C:\Espressif\tools\python\...\venv`。

## 6. 固件产物来源核对

### 当前 `application/edge_agent/build`

- build 目录最后修改：2026-07-23 22:04:14。
- `edge_agent.bin`：2026-07-23 22:04:13，大小 3,939,424 bytes。
- `bootloader.bin`：2026-07-23 21:48:29，大小 24,560 bytes。
- `project_description.json`：`idf_path=E:/esp/.espressif/v6.0/esp-idf`，`target=esp32p4`，`project_version=3dae9be-dirty`。
- 结论：当前 build 产物来自 v6.0，不是 v5.5.4。

### `build.stale_v55_20260723_2118`

- `edge_agent.bin`：2026-07-23 20:03:08，大小 3,929,056 bytes。
- `bootloader.bin`：2026-07-23 20:02:34，大小 24,192 bytes。
- `project_description.json` 同样显示 `idf_path=E:/esp/.espressif/v6.0/esp-idf`。
- 结论：目录名暗示 stale v55，但内容证据显示仍是 v6.0 build cache，不能作为 v5.5.4 成功构建证明。

## 7. 审计文档读取与结论

本轮读取了 `docs/integration_audit` 下 11 份 Markdown 审计文档，并读取/核对了备份目录中 2 个 `.bak` 文件。文件清单与摘要如下：

| 文件 | 创建目的 | 主要结论 | 是否含实际修改记录 | 是否含 v6.0 内容 | 是否有矛盾点 |
| --- | --- | --- | --- | --- | --- |
| `ALIYUN_CURRENT_STATE.md` | 梳理阿里云当前角色 | 当前康复闭环未接入阿里云；DashScope 只是通用 LLM 后端能力 | 否 | 否 | 与其他文档一致 |
| `ALIYUN_ROLE_ASSESSMENT.md` | 评估是否应接入阿里云 | 阿里云应作为可选外部能力，不应阻塞本地闭环 | 否 | 否 | 与本轮“不得访问阿里云”一致 |
| `CURRENT_ARCHITECTURE.md` | 记录当前实际链路 | ESP/PC 视觉/PC 评分/医生端 demo 是分离链路，医生端尚无真实 Adapter | 否 | 否 | 文档称“本机没有 ESP-IDF 验证”与后续环境修复报告“v6 build 通过”存在阶段差异 |
| `ENVIRONMENT_AUDIT.md` | 审计本机环境 | 记录 PC 环境恢复与 ESP-IDF v6.0 build 通过 | 是 | 是 | 与用户要求 v5.5.4 正式基线冲突 |
| `ENVIRONMENT_FIX_PLAN.md` | 制定环境修复计划 | 计划修复启动器/Conda/医生端/ESP-IDF v6 build | 是，计划级 | 是 | 把 v6.0 当可用固件环境，与当前用户基线冲突 |
| `ENVIRONMENT_FIX_REPORT.md` | 汇总实际修复 | 记录视觉/评分/医生端验证通过，以及 ESP-IDF v6 兼容修复 | 是 | 是 | 记录了 v6 专属改动，应拆分审阅 |
| `FUNCTION_INVENTORY.md` | 功能盘点 | 冻结 ESP、视觉、评分、医生端接口边界 | 否 | 提到当前 shell 无 IDF | 与 v6 build 文档存在阶段差异 |
| `INTERFACE_MATRIX.md` | 接口矩阵 | 明确视觉 8000、评分 8020、医生端 8000/5173、WebSocket 与字段边界 | 否 | 否 | 与其他接口边界一致 |
| `LOCAL_SETUP.md` | 本地恢复操作说明 | 写入 v6.0 profile + build 命令，并记录 PC 环境恢复命令 | 是，命令说明 | 是 | v6.0 设置不应作为正式固件基线继续沿用 |
| `STARTUP_MATRIX.md` | 启动矩阵 | 列出各模块启动命令、环境、端口和阻塞点 | 是，验证状态 | 是 | 固件行以 v6.0 为基线，与本轮要求冲突 |
| `ZERO_REGRESSION_PLAN.md` | 零回归边界 | 冻结视觉/评分/训练/医生端/阿里云边界，列出回归验证命令 | 是，验证记录 | 是 | ESP 验证命令写 v6.0，应改回 v5.5.4 审核后再执行 |
| `backup_20260723_202405/Start-VisionPC.ps1.bak` | 视觉启动器修改前备份 | 原脚本硬编码 `C:\ProgramData\anaconda3` 和 `C:\ec\_conda_envs\vision_interpreter`，启动前强依赖热点 | 是，备份证据 | 否 | 支持保留启动器恢复改动 |
| `backup_20260723_202405/vision_launcher_README.md.bak` | README 修改前备份 | 原文档写固定 Conda 与 8000 流程 | 是，备份证据 | 否 | 支持保留 README 恢复改动 |

## 8. v6.0 相关修改清单

| 文件 | 行号 | 修改前含义 | 修改后含义 | 修改目的 | 是否确定由 v6.0 引入 | 是否影响运行行为 | 是否可安全回退 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `application/edge_agent/sdkconfig` | 3 | ESP-IDF 5.5.4 Project Configuration | ESP-IDF 6.0.0 Project Configuration | v6 构建重新生成配置 | 是 | 是，配置面很大 | 是，但需恢复 v5.5.4 版本并重新用 v5.5.4 验证 |
| `application/edge_agent/components/rehab_car/CMakeLists.txt` | 4 | 依赖 `json` | 依赖 `espressif__cjson` | 适配 v6 组件名解析 | 高度确定 | 构建行为，不改运行逻辑 | 可回退到 `json`，需 v5.5.4 build 验证 |
| `application/edge_agent/components/rehab_integration/CMakeLists.txt` | 13 | 依赖 `json` | 依赖 `espressif__cjson` | 同上 | 高度确定 | 构建行为 | 可回退 |
| `application/edge_agent/components/rehab_person_follow/CMakeLists.txt` | 18 | 依赖 `json` | 依赖 `espressif__cjson` | 同上 | 高度确定 | 构建行为 | 可回退 |
| `application/edge_agent/components/rehab_scan_capture/CMakeLists.txt` | 13 | 依赖 `json` | 依赖 `espressif__cjson` | 同上 | 高度确定 | 构建行为 | 可回退 |
| `application/edge_agent/components/rehab_scan_transfer/CMakeLists.txt` | 14 | 依赖 `json` | 依赖 `espressif__cjson` | 同上 | 高度确定 | 构建行为 | 可回退 |
| `application/edge_agent/components/rehab_score_bridge/CMakeLists.txt` | 20 | 依赖 `json` | 依赖 `espressif__cjson` | 同上 | 高度确定 | 构建行为 | 可回退 |
| `application/edge_agent/components/rehab_sensor_recording/CMakeLists.txt` | 11 | 依赖 `json` | 依赖 `espressif__cjson` | 同上 | 高度确定 | 构建行为 | 可回退 |
| `application/edge_agent/components/rehab_target_follow/CMakeLists.txt` | 14 | 依赖 `json` | 依赖 `espressif__cjson` | 同上 | 高度确定 | 构建行为 | 可回退 |
| `application/edge_agent/components/rehab_task_decision/CMakeLists.txt` | 22 | 依赖 `json` | 依赖 `espressif__cjson` | 同上 | 高度确定 | 构建行为 | 可回退 |
| `application/edge_agent/components/rehab_score_bridge/src/rehab_score_bridge_storage.c` | 16, 270-297 | 使用 `mbedtls/sha256.h` 与 sha256 starts/update/finish API | 使用 `mbedtls/md.h` 与 generic md API | 适配 v6/MbedTLS 公开 API 差异 | 文档明确说明，确定 | 理论上同为 SHA-256；实现路径改变 | 可回退，但需确认 v5.5.4 headers/API |
| `application/edge_agent/managed_components/espressif__esp_board_manager/peripherals/periph_dsi/periph_dsi.py` | 12-14, 78, 87-93 | 未确认原始内容 | 含 `get_idf_version()[0] >= 6` 与 `clock_lane_force_hs` header 能力判断 | 适配 v6 DSI 字段 | 文档明确说明；该路径被 `.gitignore` 忽略，不在 Git diff | 影响 board manager 生成行为 | 暂不能直接动，需先确认 managed component 来源 |
| `application/edge_agent/build/` | n/a | 旧 build cache 未知 | `project_description.json` 指向 v6.0 | v6 构建产物 | 是 | 影响后续增量构建来源判断 | 可清理/重建，但本轮禁止删除 |
| `application/edge_agent/build.stale_v55_20260723_2118/` | n/a | 名称暗示旧 v5.5.4 cache | 内容仍指向 v6.0 | 重命名/保留旧 build 现场 | 是 | 可能误导后续判断 | 可清理，但本轮禁止删除 |
| `docs/integration_audit/*.md` 多处 | 见 grep 结果 | 未存在 | 记录 v6 profile/build/兼容修复 | 审计记录上一轮 v6 处理 | 是 | 不直接影响运行 | 不建议删除，应作为审计证据保留并追加纠偏报告 |

## 9. 修改分类

- A. 队友原有业务功能：历史提交中的 rehab UI、训练、评分、追人、scan 等功能；本轮未进一步分类回退。
- B. ESP-IDF v5.5.4 原有补丁：v5.5.4 IDF 仓库 4 个 dirty 文件，必须保留，禁止 reset/checkout/clean。
- C. PC 视觉环境恢复：`C:\ec\_conda_envs\vision_interpreter`、视觉启动器自动发现、README、Bootstrap 脚本。
- D. PC 评分环境恢复：`C:\ec\_conda_envs\rehab_score`，审计文档记录 check/smoke 通过。
- E. 医生端环境恢复：医生端后端 `.venv`、前端 `node_modules`，审计文档记录 `/health`、typecheck/build/dev HTTP 200。
- F. 启动器路径和环境自动发现：`Start-VisionPC.ps1`、`README.md`、`Bootstrap-VisionPC.ps1`。
- G. 审计文档：`docs/integration_audit/` 下 Markdown、备份目录、本报告。
- H. ESP-IDF v6.0 专属兼容修改：`sdkconfig` v6、9 个 `espressif__cjson`、MbedTLS md API、ignored board manager DSI 脚本、v6 build cache。
- I. 无法确认来源的修改：串口日志的 staged/AD 状态、ignored managed component 的真实原始版本、两个医生端 demo session 目录。

## 10. 可明确保留的修改

- PC 视觉 Conda prefix：`C:\ec\_conda_envs\vision_interpreter`。
- PC 评分 Conda prefix：`C:\ec\_conda_envs\rehab_score`。
- 视觉启动器对 `VISION_PYTHON`、项目 `.venv`、Conda prefix/name、PATH fallback、`VISION_PORT`、`VISION_HOST`、`VISION_LOG_DIR` 的支持。
- `tools/vision_launcher/Bootstrap-VisionPC.ps1`，前提是确认用户接受新增 bootstrap 脚本。
- 医生端现有 `.venv`、`node_modules` 与 demo 数据，不应因固件回退被删除。
- `docs/integration_audit/` 审计文档和备份目录，作为恢复证据保留。
- v5.5.4 IDF 仓库 4 个本地补丁。

## 11. 可明确回退的修改

以下只是在报告层面建议，未执行：

- `application/edge_agent/sdkconfig` 回到 v5.5.4 成功构建对应配置。
- 9 个 rehab 组件 `CMakeLists.txt` 中 `espressif__cjson` 回到 v5.5.4 所需依赖名 `json`，但需实际 v5.5.4 build 验证。
- `rehab_score_bridge_storage.c` 的 MbedTLS md API 改动可作为 v6 兼容改动候选回退；若 md API 在 v5.5.4 下也可用，则可选择保留，但需要构建与评分桥 SHA 校验确认。
- 当前 `application/edge_agent/build/` 与 `build.stale_v55_20260723_2118/` 都不能作为 v5.5.4 产物继续使用；后续应在用户批准后清理或隔离并用 v5.5.4 重建。

## 12. 无法确认、暂时不能动的修改

- `application/edge_agent/managed_components/espressif__esp_board_manager/peripherals/periph_dsi/periph_dsi.py`：文件被 `.gitignore:12 managed_components/` 忽略，Git 不追踪；虽然内容含 v6 判断，但不能直接判定原始状态。
- `application/edge_agent/p4_scan_serial_*.log`：现场串口日志，部分已暂存，不能在无用户确认下删除或取消暂存。
- 医生端两个未跟踪 demo session 目录：可能是验证产物或用户数据，暂不删除。
- `C:\ev\rehab_doctor_twin\docs\integration_audit` 作为未跟踪目录：本轮只新增报告，不应整体清理。

## 13. 推荐定向回退顺序

1. 冻结当前现场：保留现有 status/diff/patch 备份，不做 reset。
2. 用户确认后，先处理 `sdkconfig`，恢复 v5.5.4 成功构建时的配置。
3. 回退 9 个 rehab `CMakeLists.txt` 的 `espressif__cjson -> json`。
4. 评估并决定是否回退 `rehab_score_bridge_storage.c` 的 MbedTLS md API；若保留，必须做 SHA-256 输出一致性确认。
5. 单独审计 ignored `managed_components/.../periph_dsi.py`，确认来源后再决定是否恢复。
6. 隔离或清理 v6 build cache，然后用 v5.5.4 profile 重新执行 board manager 与 build。
7. 最后再处理串口日志暂存状态，避免误删现场证据。

## 14. 回退风险

- 直接 `git reset` 或 `git restore` 会混淆 PC 环境恢复、v6 兼容修复和现场日志，风险高。
- 回退 `sdkconfig` 后若未用 v5.5.4 重新生成，可能与 board manager 或当前组件依赖不一致。
- `json`/`espressif__cjson` 名称取决于 IDF/component manager 版本；必须以 v5.5.4 build 为准。
- MbedTLS 回退可能影响评分桥上传 CSV 的 SHA-256 校验；必须用同一 CSV 验证 hash 一致。
- build cache 均为 v6 来源，继续增量构建可能污染判断。
- 医生端和视觉服务默认都用 8000，环境验证需分时启动，不能误判为服务失败。

## 15. 回退后 v5.5.4 验证方式

在用户批准后，使用真实 v5.5.4 profile：

```powershell
. 'C:\Espressif\tools\Microsoft.v5.5.4.PowerShell_profile.ps1'
$env:PYTHONIOENCODING = 'utf-8'
cd C:\ec\esp-claw\application\edge_agent
idf.py bmgr -c ./boards -b esp32_p4_function_ev
idf.py build
```

必须确认：

- `idf.py --version` 输出 `ESP-IDF v5.5.4-dirty`。
- `IDF_PATH=E:\esp\.espressif\v5.5.4\esp-idf`。
- `sdkconfig` 头部回到 ESP-IDF 5.5.4。
- `CONFIG_IDF_TARGET="esp32p4"`。
- `CONFIG_ESP_BOARD_ESP32_P4_FUNCTION_EV=y`。
- `CONFIG_ESP_BOARD_NAME="esp32_p4_function_ev"`。
- `CONFIG_ESP32P4_REV_MIN_301=y`。
- `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions_16MB.csv"`。
- 生成 `build\bootloader\bootloader.bin` 与 `build\edge_agent.bin`。
- 出现 `Project build complete`。

本轮未执行上述构建。

## 16. 本轮行为声明

- 本轮读取了审计文档、Git 状态、diff、reflog、环境变量、ESP-IDF 安装路径、build cache 元数据和现场备份文件。
- 本轮没有修改 ESP-Claw 工程源码。
- 本轮没有修改 `sdkconfig`、`dependencies.lock`、CMake、Kconfig、驱动或 API。
- 本轮没有执行 `git reset`、`git restore`、`git checkout`。
- 本轮没有删除 build、managed_components、Conda 环境或日志。
- 本轮没有运行 `idf.py build`、`idf.py set-target`、`idf.py update-dependencies`。
- 本轮没有烧录开发板，没有 flash/monitor。
- 本轮没有访问、登录或修改阿里云。
- 本轮唯一写入是新增本恢复报告：`C:\ev\rehab_doctor_twin\docs\integration_audit\CONTEXT_RECOVERY_REPORT.md`。
