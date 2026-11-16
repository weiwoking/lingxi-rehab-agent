# Focused change inventory

本轮没有 repository diff：`C:\ec` 不是 Git worktree。全部新增内容位于独立 Stage2C workspace 与指定 report 目录。

## Stage2C workspace additions

- `stage2c_camera_tof_validate.py` — frozen-authority validation CLI
- `test_stage2c_toolchain.py` — 7 项 geometry/dynamic-N/no-refit tests
- `run_synthetic_validation.py` — perfect/noisy/perturbed/outside examples
- `build_evidence.py` — CLI transcripts、protected tree hashes、sanitized ZIP
- `frozen_authority_manifest.json` / `workspace_manifest.json`
- `SOURCE_FIRST_AUDIT.md` / `VALIDATION_MATH.md` / `CAPTURE_GUIDE_STAGE2C.md` / `README_STAGE2C.md`
- `synthetic/`、`evidence/`、空 registry 的 `observations/coverage/validation` 派生产物

## Explicit non-changes

- Stage2A candidate/workspace：未改
- Stage2B candidate/workspace manifest/imports：未改
- DELTAR FR candidate：未改
- formal validated scans：未改；当前 latest 仅做完整 11-frame dry-run contract 检查，未注册
- `esp-claw`、`_deltar_p4_pipeline`、`_deltar_formal`、Stage7A/B/C、Geometry、Competition Shelf Stabilizer、runtime policy：未改
- 未执行 build/flash/monitor/reconfigure/clean/fullclean/bmgr/promotion
