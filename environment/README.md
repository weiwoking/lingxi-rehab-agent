# PC 环境重建（Windows x64）

本目录从当前 5 套项目专用环境的解释器导出，未安装、升级或降级任何包。环境本体保留本机并由根 Git ignore。每套含 Python / pip 版本、完整 package list、pip check、关键版本和实际 runtime 查询；Conda 环境另含 environment.yml、精确 conda-explicit.txt 和 pip-freeze.txt。Conda 导出移除了本机 name/prefix；runtime 路径用项目相对路径记录。

在项目根运行：

```powershell
.\scripts\setup-pc-environments.ps1
# 只显示计划，不创建或安装：
.\scripts\setup-pc-environments.ps1 -Plan
# 对现有环境执行只读版本、import、pip check：
.\scripts\setup-pc-environments.ps1 -CheckExisting
# 只处理指定环境（YOLO 会先处理其 DELTAR 基础解释器）：
.\scripts\setup-pc-environments.ps1 -Environment scan_yolo
```

默认遇到任何已有目标目录输出 SKIP_EXISTING，绝不覆盖，包括未完成的目录。脚本不提供删除或 Recreate 功能；需要重新创建时，应在新的空 clone 中运行。缺少系统 Conda 或 TTS 所需 Python 3.14.3 会明确 FAIL，不自动安装系统工具。Conda 先使用 conda-explicit.txt 创建固定版本基础环境，再安装由 environment.yml 的 pip 段生成的 requirements.pip.txt。venv 使用记录的精确 Python 版本，先恢复 pip/setuptools/wheel 工具版本，再安装固定版本 requirements.freeze.txt。YOLO 使用重建的 DELTAR Python 3.11.15 作为基础解释器。

DELTAR / YOLO 的 cu128 源为 https://download.pytorch.org/whl/cu128，分别由 external/_deltar_formal/evidence/pip_install_torch_cu128.log 和 external/_deltar_yolo_cuda/requirements.lock.txt 证实；不是从版本号猜测。安装文件在原 freeze / pip export 上添加了这一已证实的源参数，未改变包版本。Vision 当前为 CPU Torch 2.13.0（运行时 2.13.0+cpu），没有记录额外 wheel 源，使用 pip 的默认源；其原始 wheel URL 未保存。GPU wheel / cuDNN / 编译架构和本机设备快照见 deltar/runtime-info.json 与 scan_yolo/runtime-info.json。需要匹配 CUDA 12.8 的 NVIDIA 驱动和受 wheel 支持的 GPU；不捆绑驱动或 CUDA / Torch DLL。

原始 freeze 是观测清单；Conda 中 pip 的 direct_url 指向供应商构建机，不应照该 file URL 安装。重建以 explicit 基础包加 pip-only 导出为准。精确版本依赖上游包仍可下载；这不是含 wheel 的离线归档。本轮验证了 Plan、默认跳过和现有环境健康，未在空机器实际下载重建，完整新机重建仍待独立验证。

脚本只创建根 Git 范围的 5 套环境。esp-claw 是独立且根 Git 忽略的仓库，其 Doctor .venv、前端 node_modules、package.json 和 pnpm-lock.yaml 保持原样；根 clone 不会携带 esp-claw 的业务源码，使用整套系统须另外取得该独立工程并遵照其安装说明。不要把本脚本当作完整项目或系统工具链安装器。
