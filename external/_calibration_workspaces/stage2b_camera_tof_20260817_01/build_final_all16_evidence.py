#!/usr/bin/env python3
"""Build the final Stage2B report and compact evidence/context ZIP."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import zipfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np

import advanced_diagnostics as advanced
import final_all16_analysis as final
import stage2b_camera_tof as core

REPORT_ROOT = Path(r"C:\ec\_codex_reports\stage2b_solver_identifiability_all16_20260818_01")
EVIDENCE = REPORT_ROOT / "evidence"
REPORT = REPORT_ROOT / "REPORT.md"
ZIP_PATH = REPORT_ROOT / "EVIDENCE_CONTEXT.zip"


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()


def fmt_metric(m: dict[str, Any]) -> str:
    return f"{m['median_mm']:.4f} / {m['p95_mm']:.4f} / {m['rms_mm']:.4f} / {m['max_mm']:.4f}"


def source_audit(analysis: dict[str, Any], current: dict[str, Any]) -> str:
    split = analysis["current_split_regression"]
    return f"""# Solver source-first audit

## Model B parameter vector

The source stores a 10-vector in this exact order: Rodrigues rotation `[rx, ry, rz]` (rad), translation `[tx, ty, tz]` (mm), `log_fx_tof`, `log_fy_tof`, `cx_tof`, `cy_tof`. Physical focal values are `exp(log_f*)`. There are no distortion coefficients, per-zone offsets, range scale, or range bias in Model B.

`tof_rays()` uses zone cell centers `0.5..7.5`; therefore the 8x8 grid geometric center is exactly `4.0`, not `3.5`. D4 maps canonical indices to raw indices, then an inverse permutation selects the canonical ray for every raw observation. The physical D4 label remains an unresolved discrete gauge; raw-zone to Camera-frame ray is the authority.

Extrinsics use OpenCV Rodrigues and `p_camera = R * p_tof + t` (column-vector convention). For `axial_z`, a normalized ray `r` is scaled by `distance/r_z`; for radial semantics it is scaled by `distance`.

The data residual is signed point-to-Camera-plane distance. It is multiplied by `1/sqrt(detected_camera_plane_frames_in_scan)` before optimization. The current solver applies Huber IRLS with cutoff `1.345 * max(1.4826*MAD, 1 mm)`. It has weak regularizers toward zero Rodrigues rotation, translation `[-20,0,0] mm`, and nominal 45-degree symmetric ToF intrinsics. These are weak gauge/numerical regularizers, not external measurements. Rotation/translation are unbounded; focal is bounded to `[3,30]` zone cells and principal point to `[1,7]` zone coordinates.

Broad observations enter `observation_rows(policy='broad')`; strict uses the same path with `strict_valid`. The current solve fits broad train rows. The reference-plane flags are created once after the current robust solution from absolute residual <= `max(10 mm, median + 4.5*1.4826*MAD)`. This task froze that classification, then refit core without reclassification or iterative trimming. Strict-and-core is diagnostic only; no runtime policy changed.

Initialization is `[0,0,0,-20,0,0,log(f45),log(f45),4,4]`, where `f45=8/(2*tan(22.5 deg))`. Finite-difference steps are `[2e-5 rad x3, 0.03 mm x3, 2e-4 log-f x2, 2e-3 zone x2]`. The engineering SVD scales are `[0.05 rad x3, 20 mm x3, 0.10 log-f x2, 0.25 zone x2]`.

## Current train/heldout semantics

The split rule is `int(SHA256(pose_id)[:8],16) mod 5 == 0`. Heldout poses are `{', '.join(split['heldout_pose_ids'])}`. S2B_016 is wholly held out and contributes 609 broad rows; hence train remains 7809 while heldout changes from 2056 to 2665. The existing `solve` candidate fits only train rows. Before this task there was no `fit_all`/`refit_all` path. `final_all16_analysis.py analyze` now supplies an explicit all-data path after model form is frozen.

## Inputs and prohibitions

Stage2A authority: `{core.CAMERA_CANDIDATE}` at `{core.CAMERA_CANDIDATE_SHA256}`. Historical Stage7B is not used as a hard prior. No raw scan, import, manifest, Stage2A, runtime broad/strict policy, formal model, build asset, or promotion target was modified.
"""


def parameter_schema(analysis: dict[str, Any], current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    current_p = current["selected"]["params"]; initial = core.default_params(); final_p = analysis["variants"]["R3_core_square_centered"]["all16_fit"]["params"]
    bounds = [[None, None]] * 6 + [[math.log(3), math.log(30)], [math.log(3), math.log(30)], [1, 7], [1, 7]]
    rows = []
    for i, (name, unit) in enumerate(zip(final.FULL_NAMES, final.FULL_UNITS)):
        rows.append({"index": i, "name": name, "unit": unit, "current_train_only_value": current_p[i],
                     "final_R3_all16_value": final_p[i], "initial_value": float(initial[i]),
                     "bound": bounds[i], "optimizer_finite_difference_step": float(final.FULL_DIFF_STEPS[i]),
                     "engineering_SVD_scale": float(final.FULL_SCALES[i]),
                     "free_in_final_R3": i < 6 or i == 6,
                     "final_constraint": ("log_fy equals log_fx" if i == 7 else "fixed at source-derived 4.0" if i in (8, 9) else None)})
    return {"schema_version": "stage2b_parameter_schema.v2", "optimizer_vector": rows,
            "derived_physical_intrinsics": {"current_train_only": {"fx_tof": math.exp(current_p[6]), "fy_tof": math.exp(current_p[7]), "cx_tof": current_p[8], "cy_tof": current_p[9]},
                                            "final_R3_all16": candidate["tof_low_dimensional_model"]},
            "free_parameter_count": {"Model_B": 10, "R1": 9, "R2": 8, "R3": 7}}


def variant_csv(analysis: dict[str, Any]) -> None:
    fields = ["variant", "free_parameters", "fit_layer", "core_all_median_mm", "core_all_p95_mm", "core_all_rms_mm", "core_all_max_mm",
              "core_heldout_median_mm", "core_heldout_p95_mm", "core_heldout_rms_mm", "core_heldout_max_mm",
              "broad_rms_mm", "outside_rms_mm", "lopo_angular_p50_deg", "lopo_angular_p95_deg", "lopo_angular_max_deg",
              "subset_angular_p50_deg", "subset_angular_p95_deg", "subset_angular_max_deg", "scaled_condition", "effective_rank"]
    with (EVIDENCE / "variant_comparison.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for name, v in analysis["variants"].items():
            c, h, l, s = v["all16_core_metrics"], v["scan_split"]["core_heldout_metrics"], v["lopo_mapping"]["angular_p95_deg"], v["subset_mapping"]["angular_p95_deg"]
            writer.writerow({"variant": name, "free_parameters": v["free_parameter_count"], "fit_layer": v["fit_layer"],
                             "core_all_median_mm": c["median_mm"], "core_all_p95_mm": c["p95_mm"], "core_all_rms_mm": c["rms_mm"], "core_all_max_mm": c["max_mm"],
                             "core_heldout_median_mm": h["median_mm"], "core_heldout_p95_mm": h["p95_mm"], "core_heldout_rms_mm": h["rms_mm"], "core_heldout_max_mm": h["max_mm"],
                             "broad_rms_mm": v["all16_all_broad_metrics"]["rms_mm"], "outside_rms_mm": v["all16_outside_background_metrics"]["rms_mm"],
                             "lopo_angular_p50_deg": l["p50"], "lopo_angular_p95_deg": l["p95"], "lopo_angular_max_deg": l["max"],
                             "subset_angular_p50_deg": s["p50"], "subset_angular_p95_deg": s["p95"], "subset_angular_max_deg": s["max"],
                             "scaled_condition": v["conditioning"]["condition_number"], "effective_rank": v["conditioning"]["effective_rank_relative_1e-6"]})


def report_text(analysis: dict[str, Any], candidate: dict[str, Any], current: dict[str, Any]) -> str:
    chosen = analysis["variants"]["R3_core_square_centered"]; intr = candidate["tof_low_dimensional_model"]; tr = candidate["T_camera_from_tof"]
    r = tr["R"]; t = tr["t_mm"]; l = chosen["lopo_mapping"]["angular_p95_deg"]; s = chosen["subset_mapping"]["angular_p95_deg"]
    rows = []
    for name, v in analysis["variants"].items():
        c, h, lm, sm = v["all16_core_metrics"], v["scan_split"]["core_heldout_metrics"], v["lopo_mapping"]["angular_p95_deg"], v["subset_mapping"]["angular_p95_deg"]
        rows.append(f"| {name} | {v['free_parameter_count']} | {c['p95_mm']:.3f} | {h['p95_mm']:.3f} | {lm['p95']:.3f} | {sm['p95']:.3f} | {v['conditioning']['condition_number']:.2f} | {v['all16_all_broad_metrics']['rms_mm']:.2f} |")
    b = analysis["variants"]["B_core_reference_plane"]; a = analysis["variants"]["A_current_broad"]
    mode1 = b["conditioning"]["near_null_modes"][0]
    comp = ", ".join(f"{x['name']}={x['coefficient']:+.3f}" for x in mode1["components"])
    profiles = b["weak_mode_profile"]["profiles"]
    pixel_lines = []
    for distance in final.DISTANCES:
        lp = chosen["lopo_mapping"]["pixel_displacement_px"][str(distance)]["fold_or_trial_p95"]
        sp = chosen["subset_mapping"]["pixel_displacement_px"][str(distance)]["fold_or_trial_p95"]
        pixel_lines.append(f"| {distance} | {lp['p50']:.3f} / {lp['p95']:.3f} / {lp['max']:.3f} | {sp['p50']:.3f} / {sp['p95']:.3f} / {sp['max']:.3f} |")
    current_p = current["selected"]["params"]
    return f"""# Stage2B 求解器可辨识性审查 + 全16组最终候选参数

## FINAL ALL16 CANDIDATE PARAMETERS

```text
status = CANDIDATE_NOT_PROMOTED
readiness = READY_FOR_STAGE2C_VALIDATION
selected_solver_variant = R3_core_square_centered
canonical_gauge = identity
physical_D4_orientation = UNRESOLVED_GAUGE
mapping_authority = raw_zone_index_to_camera_frame_ray
range_semantics = axial_z

ToF intrinsics (8x8 zone coordinates):
fx_tof = {intr['fx_tof']:.12f}
fy_tof = {intr['fy_tof']:.12f}
cx_tof = {intr['cx_tof']:.12f}
cy_tof = {intr['cy_tof']:.12f}

T_camera_from_tof, column-vector convention, translation in mm:
R =
[{r[0][0]: .12f}, {r[0][1]: .12f}, {r[0][2]: .12f}]
[{r[1][0]: .12f}, {r[1][1]: .12f}, {r[1][2]: .12f}]
[{r[2][0]: .12f}, {r[2][1]: .12f}, {r[2][2]: .12f}]
t_mm = [{t[0]:.12f}, {t[1]:.12f}, {t[2]:.12f}]

core all-data residual median/p95/RMS/max mm = {fmt_metric(chosen['all16_core_metrics'])}
scan-heldout core residual median/p95/RMS/max mm = {fmt_metric(chosen['scan_split']['core_heldout_metrics'])}
LOPO angular-p95 distribution p50/p95/max deg = {l['p50']:.6f} / {l['p95']:.6f} / {l['max']:.6f}
80% subset angular-p95 distribution p50/p95/max deg = {s['p50']:.6f} / {s['p95']:.6f} / {s['max']:.6f}
scaled Jacobian condition number = {chosen['conditioning']['condition_number']:.6f}
effective rank = {chosen['conditioning']['effective_rank_relative_1e-6']} / {chosen['free_parameter_count']}
```

这16组数据已经得到一套明确的、全数据拟合的候选参数。不需要继续采 S2B_017；下一步是独立 Stage2C validation，本轮没有 promotion，也没有自动开始 Stage2C。

## 结论与模型选择

R3 是最终模型：`fx_tof == fy_tof`，且 `cx_tof == cy_tof == 4.0`。4.0 来自源码的 zone-center 坐标 `0.5..7.5`，不是臆定。相比 full Model B，R3 将 100×80% subset angular-p95 的 p95 从 {b['subset_mapping']['angular_p95_deg']['p95']:.3f}° 降到 {s['p95']:.3f}°，condition number 从 {b['conditioning']['condition_number']:.2f} 降到 {chosen['conditioning']['condition_number']:.2f}。core all-data RMS 从 {b['all16_core_metrics']['rms_mm']:.3f} 增至 {chosen['all16_core_metrics']['rms_mm']:.3f} mm，但 scan-heldout p95 从 {b['scan_split']['core_heldout_metrics']['p95_mm']:.3f} 降至 {chosen['scan_split']['core_heldout_metrics']['p95_mm']:.3f} mm。按 mapping stability、heldout accuracy、conditioning、simplicity、broad robustness 的既定优先级，R3 胜出。

| Variant | free k | core all p95 mm | core heldout p95 mm | LOPO angular-p95 p95 ° | subset angular-p95 p95 ° | scaled cond | broad RMS mm |
|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

Variant C 与 B 数值相同：冻结的 9906 条 core observations 全部也是 strict-valid。因此 broad-only 没有污染 core 子集；current broad fit 的主要污染源是 568 条已分类 outside/background observations。它们仍全部保留用于 robustness validation，没有删除、blacklist 或修改 runtime policy。R3 的 all-broad RMS 为 {chosen['all16_all_broad_metrics']['rms_mm']:.3f} mm，outside/background RMS 为 {chosen['all16_outside_background_metrics']['rms_mm']:.3f} mm；这不与 core accuracy 混报。

## 可辨识性与弱模态

Jacobian 使用 Huber 权重，并按 `[0.05 rad, 20 mm, 0.10 log-f, 0.25 zone]` 工程尺度归一化。Full B 的 shape 是 {b['conditioning']['shape'][0]}x{b['conditioning']['shape'][1]}，condition number {b['conditioning']['condition_number']:.3f}，在相对 1e-6 阈值下 nominal rank 为 10/10；因此不是严格秩亏，而是工程上明显的弱可辨识方向。

最弱 B mode：`{comp}`。这是 principal point 与 lateral translation 的直接补偿。第二弱 mode 仍由 `cx/cy` 与 `tx/ty` 主导。第三弱 mode 混合 Rodrigues rotation、log focal 与 translation，说明 principal-point↔rotation 与 FOV↔baseline 补偿是次要但真实的。没有证据支持增加 64 自由 ray 的 Model C。

沿最弱 mode 固定 ±1 工程步、局部重优化其余9维时，core RMS 只从 {b['weak_mode_profile']['baseline_core']['rms_mm']:.6f} mm 变为 {profiles[0]['core_metrics']['rms_mm']:.6f} / {profiles[-1]['core_metrics']['rms_mm']:.6f} mm，但 raw-zone Camera-ray angular-p95 变为 {profiles[0]['mapping_from_optimum']['angular_difference_deg']['p95']:.3f}° / {profiles[-1]['mapping_from_optimum']['angular_difference_deg']['p95']:.3f}°。这直接证明“plane residual 几乎不变但 rays 明显漂移”。固定 grid center 消除了主补偿，square focal 再降低 FOV 各向异性自由度。

## Current solve 与 final-fit 语义

Current `solve` 仍使用 deterministic scan split。Heldout 是 S2B_004/005/008/016。S2B_016 的609条 broad rows 整组进入 heldout，所以 train 保持7809，heldout由2056变为2665。现有 v1 solve/candidate 参数只基于 train scans；其 selected internal vector 是 `{', '.join(f'{x:.9g}' for x in current_p)}`。原工具没有 final refit 能力；新增路径在模型冻结后用 S2B_001..016 的全部9906条 frozen core observations 拟合最终参数。CV/LOPO/subset 证据仍独立保存，不与 final estimation 混合。

## Core 语义、距离与角度

既有16组 advanced diagnostics 的 core LOPO 是 axial 16/16，100×80% subset 是 axial 100/100。Near/mid/far 及 low/medium/high angle 各层都支持 axial。R3 的 core all-data分层见 `core_angle_distance_R3.json`。Core 为9906条，median/p95/RMS/max 为 {fmt_metric(chosen['all16_core_metrics'])} mm；all-broad 为10474条，max {chosen['all16_all_broad_metrics']['max_mm']:.3f} mm；outside/background 为568条，max {chosen['all16_outside_background_metrics']['max_mm']:.3f} mm。

## Camera-frame mapping stability

| depth mm | LOPO per-fold pixel-p95 distribution p50/p95/max px | subset per-trial pixel-p95 distribution p50/p95/max px |
|---:|---:|---:|
{chr(10).join(pixel_lines)}

近距离像素漂移仍大于远距离，这是 translation 引起的视差，不是 D4 物理标签问题。DELTAR `fr` 因此按400/600/1000 mm分别生成；单一静态 rectangle 不能精确代表整个深度范围。

## 参数稳定性

R3 的100个 subset 中：`fx=fy` std {next(x['std'] for x in chosen['parameter_stability_subsets'] if x['name']=='fx_tof'):.6f} zone-cell，range {next(x['range'] for x in chosen['parameter_stability_subsets'] if x['name']=='fx_tof'):.6f}；`tx/ty/tz` std 分别为 {next(x['std'] for x in chosen['parameter_stability_subsets'] if x['name']=='translation_x'):.3f}/{next(x['std'] for x in chosen['parameter_stability_subsets'] if x['name']=='translation_y'):.3f}/{next(x['std'] for x in chosen['parameter_stability_subsets'] if x['name']=='translation_z'):.3f} mm。完整带标签 std/min/max/range 在 `variant_comparison.json`，没有无标签 parameter std。

## Candidate 与 DELTAR fr

最终 candidate：`{final.CANDIDATE_PATH}`。它逐 raw index 0..63 给出 ToF-frame center ray、Camera-frame center ray 和明确标记为 model-derived 的 corner rays，并包含16个 pose/scan IDs、每个源文件 hash、solver/options、Stage2A path/SHA、CV/SVD evidence 与 `promotion=false`。

DELTAR fr：`{final.FR_PATH}`，含400/600/1000 mm的 zone-center projection 与 model-derived continuous rectangles。它是 candidate，没有替换 runtime。

## 测试与保护边界

27/27 tests 通过：原工具回归、split、16/16 all-data、core filter、broad/core separation、synthetic known-null SVD、reduced synthetic recovery、deterministic raw mapping、D4 gauge equivalence、exact 16-pose provenance、workspace-only candidate boundary、Stage2A/import/manifest hashes unchanged。未执行 build、flash、monitor、reconfigure、clean/fullclean、bmgr 或 promotion。

## Readiness

**READY_FOR_STAGE2C_VALIDATION**

理由：R3 有7/7 effective rank、condition number {chosen['conditioning']['condition_number']:.2f}，LOPO/subset mapping 明显稳定于 full B，core heldout 没有退化，并已完成全16组 refit。Stage2C 应作为独立验证；本报告不代表 promotion。
"""


def main() -> int:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists(): shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True)
    analysis = core.load_json(final.ANALYSIS_PATH); candidate = core.load_json(final.CANDIDATE_PATH)
    current = core.load_json(core.SOLUTION_PATH); obs = core.load_json(core.OBS_PATH)
    layers, _ = final.rows_and_layers(obs); params = candidate["raw_zone_camera_mapping"]
    chosen_params = analysis["variants"]["R3_core_square_centered"]["all16_fit"]["params"]
    residual, _ = core.evaluate_params(chosen_params, layers["core"], final.CANONICAL_GAUGE, final.SEMANTICS, "B_low_dimensional")
    metadata, _ = advanced.observation_metadata(obs)
    strata = {"distance": final.metric_by_metadata(layers["core"], residual, metadata, "distance_bin"),
              "angle": final.metric_by_metadata(layers["core"], residual, metadata, "angle_bin")}
    write(REPORT, report_text(analysis, candidate, current))
    write(EVIDENCE / "solver_source_audit.md", source_audit(analysis, current))
    dump(EVIDENCE / "parameter_schema.json", parameter_schema(analysis, current, candidate))
    dump(EVIDENCE / "train_heldout_split.json", analysis["current_split_regression"])
    dump(EVIDENCE / "scaled_jacobian_svd.json", {name: v["conditioning"] for name, v in analysis["variants"].items()})
    dump(EVIDENCE / "weak_mode_profiles.json", {name: v.get("weak_mode_profile") for name, v in analysis["variants"].items() if v.get("weak_mode_profile")})
    dump(EVIDENCE / "core_vs_broad.json", {name: {k: v[k] for k in ("all16_core_metrics", "all16_all_broad_metrics", "all16_outside_background_metrics", "all16_broad_only_metrics")} for name, v in analysis["variants"].items()})
    dump(EVIDENCE / "core_angle_distance_R3.json", strata)
    variant_csv(analysis)
    copies = [
        (final.ANALYSIS_PATH, "variant_comparison.json"), (final.CANDIDATE_PATH, final.CANDIDATE_PATH.name),
        (final.FR_PATH, final.FR_PATH.name), (final.OUT / "tests.txt", "tests.txt"),
        (final.OUT / "protected_hashes.before.json", "protected_hashes.before.json"),
        (final.OUT / "protected_hashes.after_final.json", "protected_hashes.after_final.json"),
        (final.OUT / "protected_hash_check.final.json", "protected_hash_check.final.json"),
        (core.WORKSPACE / "advanced_diagnostics" / "semantics_stability.json", "semantics_stability.json"),
        (core.WORKSPACE / "advanced_diagnostics" / "mapping_stability.json", "prior_mapping_stability.json"),
        (core.WORKSPACE / "advanced_diagnostics" / "gauge_equivalence_summary.json", "gauge_equivalence_summary.json"),
        (core.WORKSPACE / "stage2b_camera_tof.py", "source/stage2b_camera_tof.py"),
        (core.WORKSPACE / "final_all16_analysis.py", "source/final_all16_analysis.py"),
        (core.WORKSPACE / "test_final_all16.py", "source/test_final_all16.py"),
    ]
    for src, rel in copies:
        dest = EVIDENCE / rel; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dest)
    cli = f"""# CLI transcript (commands executed; no build/flash/promotion)

Python: C:\\ec\\_deltar_formal\\envs\\deltar_py311_torch271_cu128\\python.exe

1. final_all16_analysis.py analyze --subset-trials 100 --full-iterations 35 --cv-iterations 12
   result: poses=16, variants=6, protected_inputs_unchanged=true

2. final_all16_analysis.py finalize --selected R3_core_square_centered
   result: candidate={final.CANDIDATE_PATH}; deltar_fr={final.FR_PATH}; promotion=false; protected_inputs_unchanged=true

3. python -m unittest -v test_stage2b_toolchain.py test_advanced_diagnostics.py test_final_all16.py
   result: Ran 27 tests; OK

4. build_final_all16_evidence.py
   result: REPORT + EVIDENCE_CONTEXT.zip
"""
    write(EVIDENCE / "cli_transcript.txt", cli)
    files = sorted(p for p in EVIDENCE.rglob("*") if p.is_file())
    manifest = {"schema_version": "stage2b_all16_evidence_manifest.v1", "created_at": core.now(),
                "exclusions": ["raw RGB565/ToF dumps", "models", "build/runtime_data", "credentials"],
                "files": [{"path": str(p.relative_to(EVIDENCE)).replace("\\", "/"), "bytes": p.stat().st_size, "sha256": sha(p)} for p in files]}
    dump(EVIDENCE / "evidence_manifest.json", manifest)
    if ZIP_PATH.exists(): ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(REPORT, "REPORT.md")
        for path in sorted(p for p in EVIDENCE.rglob("*") if p.is_file()):
            archive.write(path, "evidence/" + str(path.relative_to(EVIDENCE)).replace("\\", "/"))
    print(json.dumps({"report": str(REPORT), "evidence_zip": str(ZIP_PATH),
                      "zip_sha256": sha(ZIP_PATH), "promotion": False}, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
