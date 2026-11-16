# Stage2C Camera↔ToF independent validation toolkit

Python：`C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe`

主工具：`stage2c_camera_tof_validate.py`

该 workspace 只考试、不改答案。每个 CLI command 首先核验 `frozen_authority_manifest.json` 中三项 path+SHA-256；hash 变化即 fail closed。Pose registry 是字典结构，安全唯一 ID、重复报错、数量动态无上限。

正式实拍前本 workspace 只有 source-first、synthetic、dry-run 与 read-only evidence；没有注册当前 latest scan。
