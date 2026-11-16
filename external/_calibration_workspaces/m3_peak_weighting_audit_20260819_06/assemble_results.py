from __future__ import annotations

import json
from pathlib import Path

W=Path(__file__).resolve().parent
SCHEMES=[f"W{i}" for i in range(9)]


def write(name,text): (W/name).write_text(text.rstrip()+"\n",encoding="utf-8")


def main():
    fr=json.loads((W/"PEAK_WEIGHTING_FREEZE.json").read_text(encoding="utf-8"));ev=json.loads((W/"PEAK_WEIGHTING_TRUTH_EVALUATION.json").read_text(encoding="utf-8"))
    multi=[r for r in fr["contexts"] if r["mode_count"]>1]
    recon=[]
    for r in multi:
        recon.append(f"| {r['scan']} | {r['frame_id']} | {r['context_id'].split(':')[-1]} | {r['mode_count']} | {r['top_probability']:.4f} | {r['probability_margin_to_0_68']:+.4f} | {r['top_second_ratio']:.4f} | {r['ratio_margin_to_1_85']:+.4f} | {r['W0_branch']} | {r['outputs_mm']['W0']:.2f} | {r['outputs_mm']['W1']:.2f} | {abs(r['outputs_mm']['W0']-r['outputs_mm']['W1']):.2f} |")
    write("M3_CURRENT_PEAK_COMBINATION_AUDIT.md","""# M3 current peak-combination audit

**M3_CURRENT_RULE_RECONSTRUCTED = true.** W0 reproduces all 135 frozen context outputs with maximum absolute difference **0.0 mm**.

Authoritative source: `extract_and_counterfactual.py`, parameters lines 25–27 and estimator lines 271–276. Modes are sorted by mass/density, limited to four (observed max three), and probabilities sum to one in all retained contexts.

```python
ratio = inf if one_mode else top_probability / second_probability
soft = probability_weighted_average(retained_mode_centers)
W0 = top_center if top_probability >= 0.68 or ratio >= 1.85 else soft
```

Across 135 contexts: **118 single-mode**, **8 multi-mode HARD_TOP**, **9 multi-mode SOFT_EXPECTATION**. Only the eight HARD_TOP contexts exhibit a top-vs-expectation discontinuity.

| Scan | Frame | Context | Modes | top p | p−0.68 | ratio | ratio−1.85 | Branch | W0 | W1 | cliff mm |
|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
"""+"\n".join(recon)+"""

The largest observed cliff is SCAN_085223 frame7: ratio 1.934 is only +0.084 above 1.85, yet the branch changes 750.68 mm expectation into the 814.59 mm top mode, a 63.91 mm jump. This demonstrates a real hard-switch cliff, but only in a small minority of contexts.
""")

    focus=ev["scan_085223_lower_focus"];rows=[]
    for r in focus["frames"]:
        modes="; ".join(f"{m['center_mm']:.1f}@{m['probability']:.3f} σ{m['spread_mm']:.1f}" for m in r["modes"])
        vals=" | ".join(f"{r['outputs_mm'][s]:.1f}" for s in SCHEMES)
        rows.append(f"| {int(r['frame_id'][-6:])} | {modes} | {r['W0_branch']} | {vals} | 725 |")
    write("SCAN_085223_LOWER_11_FRAME_MODES.md","# SCAN_085223 lower core context — 11 independent frames\n\nMode notation is `center@probability σspread`, all in mm except probability. Each frame is independent.\n\n| Frame | Frozen modes | W0 branch | W0 | W1 | W2 | W3 | W4 | W5 | W6 | W7 | W8 | truth |\n|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"+"\n".join(rows)+f"\n\nSINGLE_MODE_FRAMES = 8; MULTI_MODE_FRAMES = 3 (frames 2, 7, 10). Single-mode outputs are identical under W0–W8.")

    f0=focus["stats"]["W0"];f4=focus["stats"]["W4"];effect=focus["multi_mode_effect_vs_W0"]["W4"]
    write("HARD_SWITCH_CLIFF_ANALYSIS.md",f"""# M3 hard-switch cliff analysis

## Frame 7 — hard switch hurts

Modes are 814.59@0.5038, 734.81@0.2605, 631.60@0.2357. The top probability is below 0.68, but top/second=1.934 exceeds 1.85 by only 0.084, so W0 hard-selects 814.59 mm (error +89.59). W1 gives 750.68 mm (error +25.68); W4 gives 773.29 mm (error +48.29); equal mean gives 727.00 mm. The hard cliff is harmful here.

## Frame 2 — hard winner helps

Modes are 753.12@0.7065 and 885.74@0.2935. W0 hard-selects 753.12 mm (error +28.12); W1 moves to 792.04 mm (error +67.04); W4 moves to 772.63 mm (error +47.63). Here the hard rule protects the result.

## Frame 10 — already soft

Modes are 644.43@0.6165 and 730.85@0.3835. W0=W1=677.58 mm (error −47.42); W4 gives 668.54 mm (error −56.46). This is not a hard-cliff failure, and W4 slightly worsens it.

W4 therefore improves absolute error in only **{effect['improved_frames']}/3** multi-mode focus frames, worsens **{effect['worsened_frames']}/3**, and is selected only because the frame7 gain dominates the smaller frame2/frame10 losses in the frozen aggregate. It is not a universal rule that “softer is better.”
""")

    write("PEAK_WEIGHTING_BOUND_ANALYSIS.md",f"""# How much peak weighting can fix

- 085223 lower core focus has **8 single-mode** and **3 multi-mode** frames.
- Under the predeclared >50 mm error boundary, **6/8 single-mode frames are wrong**; **3/8 exceed 75 mm** and **2/8 exceed 100 mm**.
- These are invariant under every W0–W8 rule. Therefore the irreducible peak-combination floor is at least `>50=6`, `>75=3`, `>100=2` frames.
- W4 improves one of the three multi-mode frames and worsens two. Aggregate 085223 MAD improves {f0['mad_mm']:.2f}→{f4['mad_mm']:.2f} mm and >75 drops {f0['gt75_count']}→{f4['gt75_count']}; span stays {f4['span_mm']:.2f} mm and >100 stays {f4['gt100_count']}.
- Peak weighting cannot address wrong single modes or the upstream Type-C distribution. Its expected value is **LOW**: a bounded tail/repeatability optimization, not a material Geometry cure.
""")

    def summary_table(scope):
        lines=[]
        for s in SCHEMES:
            x=ev["scopes"][scope][s]
            lines.append(f"| {s} | {x['mean_scan_lower_mad_mm']:.3f} | {x['mean_scan_lower_std_mm']:.3f} | {x['mean_scan_lower_span_mm']:.3f} | {x['aggregate']['lower']['median_mm']:.2f} | {x['aggregate']['lower']['median_bias_mm']:+.2f} | {x['gt50_count']} | {x['gt75_count']} | {x['gt100_count']} | {x['complexity_score']} |")
        return "| Scheme | mean lower MAD | std | span | lower median | bias | >50 | >75 | >100 | complexity |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"+"\n".join(lines)
    write("PEAK_WEIGHTING_COMPETITION_SCORECARD.md",f"""# Peak-weighting competition scorecard

All W0–W8 definitions and outputs were frozen before truth was loaded. Selection priority is CORE catastrophic >100, the equal-weight normalized span/MAD pair, >75, >50, bias and complexity.

## CORE_OBJECTS

{summary_table('CORE_OBJECTS')}

## ALL_OBJECTS

{summary_table('ALL_OBJECTS')}

**Best variant: W4 squared-probability expectation.** Versus W0, CORE mean lower MAD improves 13.650→12.782 mm (6.4%), span 74.059→69.283 mm (6.4%), >75 drops 8→6, >100 stays 4, while >50 rises 42→44. ALL_OBJECTS shows the same bounded pattern: MAD 15.467→14.478, span 116.419→113.447, >100 unchanged at 18, >75 22→20, >50 59→62.

W4 meets the task's explicit minimum “clearly better” gates, but the unchanged catastrophic floor and increased moderate-error count make it a small competition optimization, not a broad fix.
""")

    all_rows=[]
    for scope in ("CORE_OBJECTS","ALL_OBJECTS"):
        for scheme in SCHEMES:
            for scan,layers in ev["scopes"][scope][scheme]["per_scan"].items():
                for layer,x in layers.items():
                    if not x.get("count"): continue
                    all_rows.append(f"| {scope} | {scheme} | {scan} | {layer} | {x['count']} | {x['median_mm']:.2f} | {x['mad_mm']:.2f} | {x['std_mm']:.2f} | {x['span_mm']:.2f} | {x['gt50_count']} | {x['gt75_count']} | {x['gt100_count']} |")
    write("PEAK_WEIGHTING_ALL_SCHEME_SCAN_LAYER_METRICS.md","# All W0–W8 scan/layer metrics\n\n| Scope | Scheme | Scan | Layer | N | Median | MAD | std | span | >50 | >75 | >100 |\n|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|\n"+"\n".join(all_rows))

    compact={"decision":ev["competition"]["final_decision"],"best_variant":"W4","expected_value":"LOW","focus_085223":{"frames":[{"frame_id":r["frame_id"],"modes":r["modes"],"branch":r["W0_branch"],"outputs_mm":r["outputs_mm"]} for r in focus["frames"]],"stats":focus["stats"],"distribution_bins":focus["distribution_bins"],"single_mode_frame_count":8,"multi_mode_frame_count":3,"single_mode_error_gt50_count":6},"core_summary":{s:{k:ev["scopes"]["CORE_OBJECTS"][s][k] for k in ("mean_scan_lower_mad_mm","mean_scan_lower_std_mm","mean_scan_lower_span_mm","gt50_count","gt75_count","gt100_count")} for s in SCHEMES},"all_summary":{s:{k:ev["scopes"]["ALL_OBJECTS"][s][k] for k in ("mean_scan_lower_mad_mm","mean_scan_lower_std_mm","mean_scan_lower_span_mm","gt50_count","gt75_count","gt100_count")} for s in SCHEMES},"scheme_definitions":fr["scheme_definitions"]}
    (W/"PEAK_WEIGHTING_COMPACT_RESULTS.json").write_text(json.dumps(compact,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"documents":7,"decision":compact["decision"],"best":"W4","expected_value":"LOW"},indent=2))


if __name__=="__main__": main()
