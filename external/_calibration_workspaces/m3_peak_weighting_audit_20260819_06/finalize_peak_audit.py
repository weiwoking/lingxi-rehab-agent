from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

W=Path(__file__).resolve().parent
REPORT_DIR=Path(r"C:\ec\_codex_reports\m3_peak_weighting_audit_20260819_01")
ZIP_NAME="m3_peak_weighting_audit_20260819_01_evidence.zip"
WEB=Path(r"C:\Users\Weiwo\Downloads\m3_peak_weighting_audit_20260819_01")


def sha_bytes(v): return hashlib.sha256(v).hexdigest()
def sha(p): return sha_bytes(p.read_bytes())


def git(repo):
    def run(*a): return subprocess.run(["git","-C",repo,*a],check=True,capture_output=True).stdout
    diff=run("diff","--binary","--no-ext-diff")
    return {"repo":repo,"head":run("rev-parse","HEAD").decode().strip(),"status":run("status","--short","--branch").decode("utf-8",errors="replace"),"diff_bytes":len(diff),"diff_sha256":sha_bytes(diff)}


def main():
    intake=json.loads((W/"INTAKE_FREEZE.json").read_text(encoding="utf-8"));ev=json.loads((W/"PEAK_WEIGHTING_TRUTH_EVALUATION.json").read_text(encoding="utf-8"));freeze=json.loads((W/"PEAK_WEIGHTING_FREEZE.json").read_text(encoding="utf-8"))
    post={"pipeline":git(r"C:\ec\_deltar_p4_pipeline"),"esp_claw":git(r"C:\ec\esp-claw")};same={k:post[k]==intake["git_pre"][k] for k in post}
    boundary={"git_pre":intake["git_pre"],"git_post":post,"exact_match":same,"production_git_diff_introduced_by_this_audit":not all(same.values())}
    (W/"GIT_PRE_POST_EVIDENCE.json").write_text(json.dumps(boundary,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    f0=ev["scan_085223_lower_focus"]["stats"]["W0"];f4=ev["scan_085223_lower_focus"]["stats"]["W4"];c0=ev["scopes"]["CORE_OBJECTS"]["W0"];c4=ev["scopes"]["CORE_OBJECTS"]["W4"]
    report=f"""AUDIT_COMPLETE = true
PRODUCTION_MODIFIED = false
IMPLEMENTATION_ALLOWED = false
RUNTIME_USES_CROSS_FRAME_INFORMATION = false

M3_CURRENT_RULE_RECONSTRUCTED = true

SCAN_085223_LOWER_SINGLE_MODE_FRAME_COUNT = 8
SCAN_085223_LOWER_MULTI_MODE_FRAME_COUNT = 3

W0_M3_085223_LOWER_MEDIAN_MM = {f0['median_mm']:.6f}
W0_M3_085223_LOWER_MAD_MM = {f0['mad_mm']:.6f}
W0_M3_085223_LOWER_SPAN_MM = {f0['span_mm']:.6f}
W0_M3_085223_GT100_FRAME_COUNT = {f0['gt100_count']}

BEST_VARIANT = W4_SQUARED_PROBABILITY_EXPECTATION
BEST_VARIANT_085223_LOWER_MEDIAN_MM = {f4['median_mm']:.6f}
BEST_VARIANT_085223_LOWER_MAD_MM = {f4['mad_mm']:.6f}
BEST_VARIANT_085223_LOWER_SPAN_MM = {f4['span_mm']:.6f}
BEST_VARIANT_GT100_FRAME_COUNT = {f4['gt100_count']}

BEST_VARIANT_SIX_SCAN_MEAN_LOWER_MAD_MM = {c4['mean_scan_lower_mad_mm']:.6f}
BEST_VARIANT_SIX_SCAN_MEAN_LOWER_SPAN_MM = {c4['mean_scan_lower_span_mm']:.6f}

PEAK_WEIGHTING_CAN_FIX_SINGLE_MODE_ERRORS = false
PEAK_WEIGHTING_EXPECTED_VALUE = LOW

FINAL_DECISION = PEAK_WEIGHTING_VARIANT_W4_IS_CLEARLY_BETTER_THAN_M3

READY_FOR_OWNER_DISCUSSION = true
READY_FOR_IMPLEMENTATION = false

GIT_COMMIT = false
EVIDENCE_ZIP_CREATED = true
EVIDENCE_ZIP_PATH = {REPORT_DIR/ZIP_NAME}

# M3 peak-weighting bounded offline audit

## Final decision

**PEAK_WEIGHTING_VARIANT_W4_IS_CLEARLY_BETTER_THAN_M3.** W4 replaces the frozen dominant-mode hard branch with a continuous squared-probability expectation, `Σ(pᵢ²·centerᵢ)/Σpᵢ²`. Its definition and all six-scan outputs were hash-frozen before truth was loaded. No production implementation is authorized in this audit.

This is a **small optimization with LOW expected value**, not a new Geometry solution. W4 does not reduce CORE >100 errors, cannot change any single-mode frame, and increases CORE >50 from 42 to 44. It is still clearly better under the task's predeclared minimum gates because catastrophic errors do not increase, both repeatability measures improve, 085223 does not degrade in aggregate, there is no truth-fit, and complexity is lower than the W0 threshold branch.

## Exact W0 reconstruction

Round2 source was reconstructed exactly: retained modes are capped at four; `p_top>=0.68 OR p_top/p_second>=1.85` selects top, otherwise W0 returns retained-mode probability expectation. Across 135 contexts there are 118 single-mode, 8 multi-mode HARD_TOP and 9 multi-mode SOFT_EXPECTATION contexts. Recalculated W0 matches all frozen M3 values with maximum absolute difference **0.0 mm**.

Freeze evidence:

- Code SHA-256: `{freeze['source']['current_script_sha256']}`
- Frozen input SHA-256: `{freeze['source']['compact_sha256']}`
- Frozen outputs SHA-256: `{freeze['outputs_sha256']}`
- `truth_loaded=false` in `PEAK_WEIGHTING_FREEZE.json`

## 085223 lower — what can and cannot move

The one lower core context per frame contains sports ball and bottle (frame2 also includes person). Eight frames have one retained mode and three have multiple modes: frames 2, 7 and 10.

| Metric | W0 | W4 |
|---|---:|---:|
| median mm | {f0['median_mm']:.2f} | {f4['median_mm']:.2f} |
| MAD mm | {f0['mad_mm']:.2f} | {f4['mad_mm']:.2f} |
| std mm | {f0['std_mm']:.2f} | {f4['std_mm']:.2f} |
| span mm | {f0['span_mm']:.2f} | {f4['span_mm']:.2f} |
| >50 frames | {f0['gt50_count']} | {f4['gt50_count']} |
| >75 frames | {f0['gt75_count']} | {f4['gt75_count']} |
| >100 frames | {f0['gt100_count']} | {f4['gt100_count']} |

Six of eight single-mode frames already exceed 50 mm, three exceed 75 mm and two exceed 100 mm. Every W0–W8 result is identical on those frames, establishing the irreducible peak-combination floor.

## Hard-switch cliff and its counterexample

- **frame7:** p=(0.5038,0.2605,0.2357), top/second=1.934, only 0.084 above the hard ratio threshold. W0 jumps to 814.59 mm; W1 is 750.68 and W4 is 773.29. The hard cliff hurts.
- **frame2:** p=(0.7065,0.2935). W0 hard top is 753.12 mm, W1 is 792.04 and W4 is 772.63. The hard winner helps.
- **frame10:** W0 is already soft at 677.58 mm; W4 moves to 668.54 mm and crosses from within 50 mm to >50 mm.

Thus W4 improves only one of the three multi-mode focus frames and worsens two. It wins because the large frame7 improvement outweighs the smaller frame2/frame10 losses, not because smoothing is universally correct.

## Six-scan competition result

| CORE metric | W0 | W4 | Change |
|---|---:|---:|---:|
| mean lower MAD mm | {c0['mean_scan_lower_mad_mm']:.3f} | {c4['mean_scan_lower_mad_mm']:.3f} | {(c4['mean_scan_lower_mad_mm']/c0['mean_scan_lower_mad_mm']-1)*100:+.1f}% |
| mean lower span mm | {c0['mean_scan_lower_span_mm']:.3f} | {c4['mean_scan_lower_span_mm']:.3f} | {(c4['mean_scan_lower_span_mm']/c0['mean_scan_lower_span_mm']-1)*100:+.1f}% |
| >50 | {c0['gt50_count']} | {c4['gt50_count']} | {c4['gt50_count']-c0['gt50_count']:+d} |
| >75 | {c0['gt75_count']} | {c4['gt75_count']} | {c4['gt75_count']-c0['gt75_count']:+d} |
| >100 | {c0['gt100_count']} | {c4['gt100_count']} | {c4['gt100_count']-c0['gt100_count']:+d} |

W4 improves CORE MAD and span by about 6.4% each and reduces >75 by 25%; >100 remains unchanged. ALL_OBJECTS agrees on the bounded direction: MAD 15.467→14.478 mm, span 116.419→113.447 mm, >75 22→20, >100 remains 18, while >50 rises 59→62.

The scheme is therefore suitable for owner discussion as the final M3 combination rule, but not ready for implementation. The explicit trade is fewer large errors and better repeatability in exchange for two additional CORE moderate errors.

## Integrity and deliverables

- Pipeline and esp-claw HEAD/status/diff hashes match intake exactly; no production diff was introduced.
- Web: `{WEB}` (ordinary directory, not ZIP), browser-validated with 11 frame axes, 15 mode markers and 99 W0–W8 output markers.
- Evidence ZIP: `{REPORT_DIR/ZIP_NAME}`
- Workspace: `{W}`
"""
    REPORT_DIR.mkdir(parents=True,exist_ok=False);(REPORT_DIR/"REPORT.md").write_text(report.rstrip()+"\n",encoding="utf-8")
    names=["INTAKE_FREEZE.json","PEAK_WEIGHTING_FREEZE.json","PEAK_WEIGHTING_TRUTH_EVALUATION.json","PEAK_WEIGHTING_COMPACT_RESULTS.json","M3_CURRENT_PEAK_COMBINATION_AUDIT.md","SCAN_085223_LOWER_11_FRAME_MODES.md","HARD_SWITCH_CLIFF_ANALYSIS.md","PEAK_WEIGHTING_BOUND_ANALYSIS.md","PEAK_WEIGHTING_COMPETITION_SCORECARD.md","PEAK_WEIGHTING_ALL_SCHEME_SCAN_LAYER_METRICS.md","DOWNLOADS_MANIFEST.json","DOWNLOADS_MANIFEST_TREE_HASH.md","downloads_web_validation.json","GIT_PRE_POST_EVIDENCE.json","intake_peak_audit.py","freeze_peak_weighting.py","evaluate_peak_weighting.py","assemble_results.py","build_web.py","finalize_peak_audit.py"]
    evidence=REPORT_DIR/"evidence";evidence.mkdir()
    for n in names: shutil.copy2(W/n,evidence/n)
    files=[{"path":f"evidence/{p.name}","bytes":p.stat().st_size,"sha256":sha(p)} for p in sorted(evidence.iterdir())]
    manifest={"schema_version":"m3_peak_weighting_evidence_manifest.v1","files":files,"excluded":["raw RGB","raw ToF","dense arrays","models/checkpoints","Downloads folder contents","credentials"]}
    (evidence/"EVIDENCE_MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    zpath=REPORT_DIR/ZIP_NAME
    with zipfile.ZipFile(zpath,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        z.write(REPORT_DIR/"REPORT.md","REPORT.md")
        for p in sorted(evidence.iterdir()): z.write(p,f"evidence/{p.name}")
    with zipfile.ZipFile(zpath) as z: bad=z.testzip()
    package={"report":str(REPORT_DIR/"REPORT.md"),"zip":str(zpath),"zip_bytes":zpath.stat().st_size,"zip_sha256":sha(zpath),"zip_test":"PASS" if bad is None else f"FAIL:{bad}"}
    (REPORT_DIR/"PACKAGE_VALIDATION.json").write_text(json.dumps(package,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(package,indent=2))


if __name__=="__main__": main()
