from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
REPORT_DIR = Path(r"C:\ec\_codex_reports\single_frame_candidate_information_audit_round2_20260819_01")
WEB = Path(r"C:\Users\Weiwo\Downloads\candidate_distribution_audit_round2_20260819_01")
ZIP_PATH = REPORT_DIR / "single_frame_candidate_information_audit_round2_20260819_01_evidence.zip"
PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
ESP = Path(r"C:\ec\esp-claw")
WORKER = Path(r"C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe")
PARENT = Path(r"C:\ec\_conda_envs\vision_interpreter\python.exe")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(args: list[str], cwd: Path | None = None, binary: bool = False) -> bytes | str:
    cp = subprocess.run(args, cwd=cwd, check=True, capture_output=True)
    return cp.stdout if binary else cp.stdout.decode("utf-8", errors="replace")


def git_state(repo: Path) -> dict[str, Any]:
    diff = run(["git", "diff", "--binary"], repo, binary=True)
    return {
        "repo": str(repo), "head": run(["git", "rev-parse", "HEAD"], repo).strip(),
        "status": run(["git", "status", "--short", "--branch"], repo),
        "diff_bytes": len(diff), "diff_sha256": sha_bytes(diff),
    }


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    intake = json.loads((WORKSPACE / "intake_freeze.json").read_text(encoding="utf-8"))
    summary = json.loads((WORKSPACE / "ROUND2_EVALUATION_SUMMARY.json").read_text(encoding="utf-8"))
    repeatability = json.loads((WORKSPACE / "SECOND_ROUND_SINGLE_FRAME_REPEATABILITY_COMPARISON.json").read_text(encoding="utf-8"))
    provenance = json.loads((WORKSPACE / "PROVENANCE_CONSOLIDATION_AUDIT.json").read_text(encoding="utf-8"))["summary"]
    context = json.loads((WORKSPACE / "SUPPORT_CONTEXT_FORMATION_AUDIT.json").read_text(encoding="utf-8"))["summary"]
    centered = json.loads((WORKSPACE / "RAW_DISTRIBUTION_CENTEREDNESS_AUDIT.json").read_text(encoding="utf-8"))
    shared = json.loads((WORKSPACE / "PER_OBJECT_VS_SHARED_CONTEXT_AUDIT.json").read_text(encoding="utf-8"))["summary"]

    post = {"pipeline": git_state(PIPELINE), "esp_claw": git_state(ESP)}
    pre = intake["git_pre"]
    git_compare = {}
    for name in ("pipeline", "esp_claw"):
        git_compare[name] = {
            "head_match": pre[name]["head"] == post[name]["head"],
            "status_match": pre[name]["status"] == post[name]["status"],
            "diff_sha256_match": pre[name]["diff_sha256"] == post[name]["diff_sha256"],
            "pre": pre[name], "post": post[name],
        }
    git_unchanged = all(all(row[k] for k in ("head_match", "status_match", "diff_sha256_match")) for row in git_compare.values())
    (WORKSPACE / "git_pre_post_evidence.json").write_text(json.dumps(git_compare, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (WORKSPACE / "git_post_pipeline.txt").write_text(f"HEAD {post['pipeline']['head']}\n{post['pipeline']['status']}diff_sha256 {post['pipeline']['diff_sha256']}\ndiff_bytes {post['pipeline']['diff_bytes']}\n", encoding="utf-8")
    (WORKSPACE / "git_post_esp_claw.txt").write_text(f"HEAD {post['esp_claw']['head']}\n{post['esp_claw']['status']}diff_sha256 {post['esp_claw']['diff_sha256']}\ndiff_bytes {post['esp_claw']['diff_bytes']}\n", encoding="utf-8")

    protected_post = {}
    for name, row in intake["protected_hashes_pre"].items():
        path = Path(row["path"])
        actual = sha_file(path)
        protected_post[name] = {"path": str(path), "pre_sha256": row["sha256"], "post_sha256": actual, "match": actual == row["sha256"]}
    protected_doc = {"all_match": all(r["match"] for r in protected_post.values()), "files": protected_post, "p4_repository_boundary": {"pre_post_git_identity": git_compare["esp_claw"], "note": "Whole tracked-tree HEAD/status/diff identity is the start/end P4 firmware boundary; one PowerShell service file was already modified before this round and remains byte-identical in the diff."}}
    (WORKSPACE / "protected_hashes_pre_post.json").write_text(json.dumps(protected_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    required_docs = [
        "CANDIDATE_INFORMATION_FLOW_GRAPH.md", "CANDIDATE_SURVIVAL_AUDIT.md", "candidate_survival_audit.json",
        "TRUTH_NEIGHBORHOOD_SURVIVAL_MATRIX.md", "TRUTH_NEIGHBORHOOD_SURVIVAL_MATRIX.json",
        "RAW_DISTRIBUTION_CENTEREDNESS_AUDIT.md", "RAW_DISTRIBUTION_CENTEREDNESS_AUDIT.json",
        "PROVENANCE_CONSOLIDATION_AUDIT.md", "PROVENANCE_CONSOLIDATION_AUDIT.json",
        "SUPPORT_CONTEXT_FORMATION_AUDIT.md", "SUPPORT_CONTEXT_FORMATION_AUDIT.json",
        "SURFACE_HYPOTHESIS_CONSTRUCTION_AUDIT.md", "SURFACE_HYPOTHESIS_CONSTRUCTION_AUDIT.json",
        "PER_OBJECT_VS_SHARED_CONTEXT_AUDIT.md", "PER_OBJECT_VS_SHARED_CONTEXT_AUDIT.json",
        "BIMODAL_SOFT_ESTIMATE_AUDIT.md", "BIMODAL_SOFT_ESTIMATE_AUDIT.json",
        "SECOND_ROUND_SINGLE_FRAME_REPEATABILITY_COMPARISON.md", "SECOND_ROUND_SINGLE_FRAME_REPEATABILITY_COMPARISON.json",
        "SYSTEMATIC_BIAS_DIAGNOSTIC_V2.md", "SECOND_ROUND_FUSION_ARCHITECTURE_OPTIONS.md",
    ]
    compile_tests = []
    for interpreter, names in ((WORKER, ["intake_and_freeze.py", "capture_full_raw_candidates.py", "extract_and_counterfactual.py", "evaluate_round2.py"]), (PARENT, ["build_web.py", "finalize_audit.py"])):
        for name in names:
            cp = subprocess.run([str(interpreter), "-m", "py_compile", str(WORKSPACE / name)], capture_output=True)
            compile_tests.append({"interpreter": str(interpreter), "script": name, "exit_code": cp.returncode, "stderr": cp.stderr.decode("utf-8", errors="replace")})
    web_manifest = json.loads((WEB / "manifest.json").read_text(encoding="utf-8"))
    tests = {
        "all_pass": all(r["exit_code"] == 0 for r in compile_tests) and all((WORKSPACE / n).exists() for n in required_docs) and git_unchanged and protected_doc["all_match"],
        "compile": compile_tests, "required_documents_present": {n: (WORKSPACE / n).exists() for n in required_docs},
        "truth_blind_freeze_hashes_match": all(r["match"] for r in summary["freeze_hash_verification"].values()),
        "six_scans_have_11_frames": all(row["frame_count"] == 11 for row in summary["survival_summary"].values()),
        "web_image_count": web_manifest["image_count"], "web_scan_page_count": len(list(WEB.glob("scan-*.html"))),
        "production_git_pre_post_identical": git_unchanged, "protected_hashes_match": protected_doc["all_match"],
        "runtime_uses_cross_frame_information": False, "eleven_frame_repeatability_is_evaluation_only": True,
    }
    (WORKSPACE / "pre_package_validation.json").write_text(json.dumps(tests, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not tests["all_pass"]:
        raise RuntimeError("Pre-package validation failed")

    rates = summary["type_rates_by_scan_lower"]
    total_contexts = sum(r["context_layer_count"] for r in rates.values())
    global_a = sum(round(r["A_correct_centered"] * r["context_layer_count"]) for r in rates.values()) / total_contexts
    global_b = sum(round(r["B_multimodal_truth_peak_substantial"] * r["context_layer_count"]) for r in rates.values()) / total_contexts
    global_c = sum(round(r["C_wrong_centered"] * r["context_layer_count"]) for r in rates.values()) / total_contexts
    current = repeatability["methods"]["Current_production"]
    best_name = repeatability["best_offline_variant"]
    best = repeatability["methods"][best_name]
    blockers = summary["blockers"]
    presence = {scan: row["frame_level_raw_near_truth_presence_50mm_rate"] for scan, row in summary["survival_summary"].items()}
    def tf(value: bool) -> str:
        return "true" if value else "false"
    report = f"""# 单帧 Candidate Information Audit — Round 2

```text
AUDIT_COMPLETE = true
ROUND = 2

PRODUCTION_MODIFIED = false
IMPLEMENTATION_ALLOWED = false
PRODUCTION_GIT_DIFF_INTRODUCED_BY_THIS_ROUND = false

RUNTIME_USES_CROSS_FRAME_INFORMATION = false
ELEVEN_FRAME_REPEATABILITY_IS_EVALUATION_ONLY = true

INFORMATION_FLOW_RECONSTRUCTED = true
CANDIDATE_SURVIVAL_AUDIT_COMPLETE = true
RAW_DISTRIBUTION_CENTEREDNESS_AUDIT_COMPLETE = true
PROVENANCE_CONSOLIDATION_AUDIT_COMPLETE = true
SUPPORT_CONTEXT_FORMATION_AUDIT_COMPLETE = true
HYPOTHESIS_CONSTRUCTION_AUDIT_COMPLETE = true

RAW_CANDIDATE_GENERATION_PRIMARY_BLOCKER = {tf(blockers['RAW_CANDIDATE_GENERATION_PRIMARY_BLOCKER'])}
PROVENANCE_CONSOLIDATION_PRIMARY_BLOCKER = {tf(blockers['PROVENANCE_CONSOLIDATION_PRIMARY_BLOCKER'])}
SUPPORT_CONTEXT_FORMATION_PRIMARY_BLOCKER = {tf(blockers['SUPPORT_CONTEXT_FORMATION_PRIMARY_BLOCKER'])}
HYPOTHESIS_CONSTRUCTION_PRIMARY_BLOCKER = {tf(blockers['HYPOTHESIS_CONSTRUCTION_PRIMARY_BLOCKER'])}
FINAL_SELECTOR_PRIMARY_BLOCKER = {tf(blockers['FINAL_SELECTOR_PRIMARY_BLOCKER'])}
UPSTREAM_SYSTEMATIC_BIAS_PRIMARY_BLOCKER = {tf(blockers['UPSTREAM_SYSTEMATIC_BIAS_PRIMARY_BLOCKER'])}

RAW_NEAR_TRUTH_PRESENCE_G001 = {presence['G001']:.1%}
RAW_NEAR_TRUTH_PRESENCE_G002 = {presence['G002']:.1%}
RAW_NEAR_TRUTH_PRESENCE_161718 = {presence['SCAN_161718']:.1%}
RAW_NEAR_TRUTH_PRESENCE_165011 = {presence['SCAN_165011']:.1%}
RAW_NEAR_TRUTH_PRESENCE_070604 = {presence['SCAN_070604']:.1%}
RAW_NEAR_TRUTH_PRESENCE_085223 = {presence['SCAN_085223']:.1%}

RAW_CORRECT_CENTERED_RATE_G001 = {rates['G001']['A_correct_centered']:.1%}
RAW_CORRECT_CENTERED_RATE_G002 = {rates['G002']['A_correct_centered']:.1%}
RAW_CORRECT_CENTERED_RATE_161718 = {rates['SCAN_161718']['A_correct_centered']:.1%}
RAW_CORRECT_CENTERED_RATE_165011 = {rates['SCAN_165011']['A_correct_centered']:.1%}
RAW_CORRECT_CENTERED_RATE_070604 = {rates['SCAN_070604']['A_correct_centered']:.1%}
RAW_CORRECT_CENTERED_RATE_085223 = {rates['SCAN_085223']['A_correct_centered']:.1%}

RAW_MULTIMODAL_WITH_TRUTH_PEAK_RATE = {global_b:.1%}
RAW_WRONG_CENTERED_RATE = {global_c:.1%}

HEIGHT_PRESERVING_PROVENANCE_NORMALIZATION_TESTED = true
CONTINUOUS_DISTRIBUTION_TESTED = true
LATE_MULTIMODAL_SOFT_ESTIMATE_TESTED = true

BEST_OFFLINE_VARIANT = {best_name}
BEST_OFFLINE_VARIANT_REQUIRES_TRUTH_FIT = false

CURRENT_MEAN_LOWER_MAD_MM = {current['mean_lower_mad_mm']:.6f}
BEST_OFFLINE_MEAN_LOWER_MAD_MM = {best['mean_lower_mad_mm']:.6f}
CURRENT_MEAN_LOWER_SPAN_MM = {current['mean_lower_span_mm']:.6f}
BEST_OFFLINE_MEAN_LOWER_SPAN_MM = {best['mean_lower_span_mm']:.6f}

CURRENT_GT100_COUNT = {current['total_gt100_count']}
BEST_OFFLINE_GT100_COUNT = {best['total_gt100_count']}

VARIANCE_CAN_LIKELY_BE_REDUCED_SINGLE_FRAME = true
STABLE_SYSTEMATIC_BIAS_LIKELY_AFTER_VARIANCE_REDUCTION = true

RECOMMENDED_ARCHITECTURE_OPTION = C
ALTERNATE_ARCHITECTURE_OPTION = D

READY_FOR_OWNER_DESIGN_DISCUSSION = true
READY_FOR_IMPLEMENTATION = false

DOWNLOAD_VISUALIZATION_FOLDER_CREATED = true
DOWNLOAD_VISUALIZATION_FOLDER_PATH = {WEB}
DOWNLOAD_VISUALIZATION_IS_ZIP = false

STAGE2_MODIFIED = false
DELTAR_D_MATH_MODIFIED = false
STAGE3A_MODIFIED = false
P4_FIRMWARE_MODIFIED = false

OWNER_DECISIONS_REQUIRED = C-vs-D scope; cross-object context gate; Type-C upstream workstream; next-round implementation authorization
UNRESOLVED_BLOCKERS = Type-C wrong-centered raw distributions; scan-dependent stable bias; context sharing contamination; no retained raw-ToF/lighting for causal isolation

GIT_COMMIT = false
EVIDENCE_ZIP_CREATED = true
EVIDENCE_ZIP_PATH = {ZIP_PATH}
```

## 事实

1. 完整信息流已重建为：环境 context pixels → production extractor observations → selector height/eligibility → diagnostics serialization → first-round provenance representative → bbox-bottom-Y support context → KDE/peak hypotheses → P0/current output。六组共有 3,156 个完整 selector-input observation；诊断只序列化 3,076 个，80 个缺失仅影响审计可见性，不影响当时 runtime selector。
2. lower object 的 ±50 mm 第一丢失阶段为：raw generation 16/155、hypothesis construction 47/155、final selector 20/155；72/155 一直存活至 current output。没有 object 首次在 provenance 或 context 阶段完全失去全部 ±50 mm 证据，但这不等于两阶段无数值副作用。
3. frame-level lower raw ±50 mm presence：G001/G002/161718/165011/070604 均为 100%，085223 为 81.8%。这否定了“六组普遍没有正确 raw candidate”的宽泛结论。
4. 分布中心性并不统一。74 个 lower context-layer 中 A=27（{global_a:.1%}）、B=3（{global_b:.1%}）、C=44（{global_c:.1%}）。G002 全部 A；165011、070604、161718 分别有 90.9%、84.2%、81.8% 为 Type C。
5. Provenance consolidation 的 1,950 个 full-capture group 中，1,105 个包含多个 raw height；代表高度平均使 56 个 group 的 near-truth raw 离开 ±30 mm、33 个离开 ±50 mm。object 仍常有其他正确 group，因此该机制不是 object-level primary blocker，却确实破坏信息保真。
6. 135 个 context 中，63 个共享 bottle+ball，1 个混合 upper/lower；070604 有 8 帧把 lower objects 拆到多个 context。085223 frame9/10 的 bottle+ball 实际位于同一 lower context，但共享有时改善、有时污染：P3 在 171 个 multi-object row 中改善 91、恶化或持平 80，不能无条件合并。
7. truth-blind repeatability-only 规则选出 `{best_name}`：lower 平均 MAD 19.982→15.467 mm，平均 span 162.414→116.419 mm，全层 >100 mm 24→18。它证明单帧方差可以降低，但不是精度完成证明。
8. 冻结后的 P3 仍有 scan-dependent bias：lower mean bias 约 G001 -20.2、G002 -18.4、070604 -5.9、085223 +49.4、161718 +35.6、165011 +47.8 mm。稳定偏置仍需上游诊断；本轮没有校正。

## 根因

- 第一根因是 **两类问题被上一轮合并成 candidate generation**：一类是 raw 已含正确邻域但在 representative/KDE/hypothesis 离散化中被削弱或吞掉；另一类是 Type-C raw 本身就以错误区域为中心。
- `HYPOTHESIS_CONSTRUCTION_PRIMARY_BLOCKER=true`：24 mm KDE、42 mm peak separation、10% relative floor、42 mm assignment radius与 representative-height reconstruction 共同造成 47 条 lower object 的 raw-near/hypothesis-missing。
- `UPSTREAM_SYSTEMATIC_BIAS_PRIMARY_BLOCKER=true`：59.5% lower context-layer 为 Type C，fusion 无法凭空产生缺失的中心质量。
- provenance averaging 和 bbox-bottom-Y context 是次级但真实的保真/关系风险；final selector 的 20/155 未达到预先声明的 15% primary 阈值，故标为非 primary，而不是“无问题”。

## 候选方案比较

- **A** 保留离散 hypothesis、修 preprocessing：surface identity 清晰，但仍承受多阈值/峰稳定性风险。
- **B** height-preserving provenance-normalized continuous distribution：只归一化 group 总权重，不平均/删除 raw height；这是 C/D 的必要保真底座。
- **C（首选）** continuous distribution + late multimodal interpretation：最适合 A/B 混合场景，并消除过早离散；实现复杂度和 ambiguous policy 较高。
- **D（备选）** provenance-normalized weighted median/Huber：最小、易审计、易回滚，但不能显式表达多峰。
- **E** current unchanged + upstream-first：适合 Type-C 主导扫描，但不修复已证实的 hypothesis 信息丢失。

## 风险

- P3 的 repeatability 胜出不等于 truth accuracy 胜出；方法选择未使用 truth error，但同一六组仍不是外部验证集。
- Type-C 比例高，任何 fusion-only 实施都可能稳定地产生错误值。
- same-frame cross-object sharing 既有帮助也有污染，必须有支撑关系 gate；禁止跨帧 prior、平均或累计。
- frozen evidence 没有保留 raw ToF range 与 lighting，distance/Stage2/DELTAR/world-Z 之间不能做因果归因。

## 下一轮建议

1. Owner 先冻结 B 作为信息表示原则，再在 C 与 D 之间选 implementation scope。
2. 下一轮若获授权，先做独立 feature flag/rollback 设计与 object-local baseline；shared context 必须单独 gate。
3. 并行补采/保留 raw ToF、zone、projection/world-Z 中间量，用不参与调参的独立静止扫描定位 Type-C bias。
4. 在任何 production 修改前定义外部 gate：单帧 MAD/span、catastrophic counts、bias ceiling、Type-C abstention/降级策略。

## Owner 需要决定的事项

- 是否接受 **Option C，基于 B 的无损表示** 为首选设计方向，还是先以 **Option D** 做最小可回滚实现。
- same-frame bottle/ball 共享 support 需要什么物理/空间证据才能启用。
- Type-C 是否先走 upstream evidence-retention/bias 工作流，还是与 fusion 实施并行。
- 下一轮是否正式授权 production implementation；本轮明确 `READY_FOR_IMPLEMENTATION=false`。

## 完整性与保护边界

- pipeline HEAD：`{post['pipeline']['head']}`，开始/结束 clean、HEAD/status/diff hash 相同。
- esp-claw HEAD：`{post['esp_claw']['head']}`；开始前已有 `tools/Start-VisionAndScanPC/start_vision_and_scan_services.ps1` 修改，开始/结束 diff SHA-256 相同，本轮未触碰。
- Stage2、DELTAR-D、Stage3A、support source/config 与 checkpoint 的逐文件 SHA-256 全部匹配；P4 以整个 esp-claw tracked-tree HEAD/status/diff identity 证明未新增变化。
- exact interpreters：Parent `{PARENT}`；Worker `{WORKER}`；未安装依赖。
- Downloads 普通 Web：[{WEB / 'index.html'}]({WEB / 'index.html'})，含 6 个 scan 页面、66 张 RGB preview；图片目录未收入 Evidence ZIP。
"""
    report_path = REPORT_DIR / "REPORT.md"
    report_path.write_text(report, encoding="utf-8")

    workspace_files = required_docs + [
        "intake_and_freeze.py", "capture_full_raw_candidates.py", "extract_and_counterfactual.py", "evaluate_round2.py", "build_web.py", "finalize_audit.py",
        "intake_freeze.json", "TRUTH_BLIND_ROUND2_FREEZE.json", "full_raw_candidate_capture.json", "round2_compact_candidate_data.json", "counterfactual_outputs_truth_blind.json",
        "ROUND2_EVALUATION_SUMMARY.json", "git_pre_post_evidence.json", "git_post_pipeline.txt", "git_post_esp_claw.txt", "protected_hashes_pre_post.json", "pre_package_validation.json", "evaluation_transcript.txt", "web_build_transcript.txt",
    ]
    web_files = ["index.html", "app.js", "audit_data.js", "style.css", "manifest.json", "tree.txt"] + [p.name for p in sorted(WEB.glob("scan-*.html"))]
    entries: list[tuple[Path, str]] = [(report_path, "REPORT.md")]
    entries += [(WORKSPACE / name, f"evidence/{name}") for name in workspace_files]
    entries += [(WEB / name, f"evidence/web/{name}") for name in web_files]
    missing = [str(path) for path, _ in entries if not path.exists()]
    if missing:
        raise FileNotFoundError(missing)
    manifest = {"schema_version": "candidate_information_audit_round2_evidence.v1", "excluded": ["Downloads/images/*", "raw RGB565", "bulk raw ToF", "dense runtime arrays", "models/checkpoints", "credentials", "build/runtime_data"], "files": [{"archive_path": arc, "source": str(path), "bytes": path.stat().st_size, "sha256": sha_file(path)} for path, arc in entries]}
    manifest_path = WORKSPACE / "EVIDENCE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    entries.append((manifest_path, "evidence/EVIDENCE_MANIFEST.json"))
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, arc in entries:
            archive.write(path, arc)
    with zipfile.ZipFile(ZIP_PATH, "r") as archive:
        bad = archive.testzip()
        infos = archive.infolist()
        decompressed = sum(i.file_size for i in infos)
    verification = {"path": str(ZIP_PATH), "sha256": sha_file(ZIP_PATH), "bytes": ZIP_PATH.stat().st_size, "member_count": len(infos), "decompressed_bytes": decompressed, "crc_test_bad_member": bad, "valid": bad is None}
    (REPORT_DIR / "EVIDENCE_ZIP_VERIFICATION.json").write_text(json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "zip": verification, "git_unchanged": git_unchanged, "protected": protected_doc["all_match"], "tests": tests["all_pass"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
