from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
OUT = Path(r"C:\Users\Weiwo\Downloads\candidate_distribution_audit_round2_20260819_01")
RESULTS = Path(r"C:\ec\_deltar_scan_service_data\results")
REPLAYS = {
    "G001": "scan_20260818_092411_numericfirst01",
    "G002": "scan_20260818_092729_numericfirst01",
    "SCAN_161718": "scan_20260818_161718_numericfirst01",
    "SCAN_165011": "scan_20260818_165011_numericfirst01",
    "SCAN_070604": "scan_20260819_070604_numericfirst01",
    "SCAN_085223": "scan_20260819_085223_numericfirst01",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "images").mkdir(exist_ok=True)
    compact = json.loads((WORKSPACE / "round2_compact_candidate_data.json").read_text(encoding="utf-8"))
    survival = json.loads((WORKSPACE / "candidate_survival_audit.json").read_text(encoding="utf-8"))
    centered = json.loads((WORKSPACE / "RAW_DISTRIBUTION_CENTEREDNESS_AUDIT.json").read_text(encoding="utf-8"))
    repeatability = json.loads((WORKSPACE / "SECOND_ROUND_SINGLE_FRAME_REPEATABILITY_COMPARISON.json").read_text(encoding="utf-8"))
    surv_map = {(r["scan"], r["frame_id"], r["object_id"]): r for r in survival["rows"]}
    center_map = {(r["scan"], r["frame_id"], r["context_id"], r["layer"]): r for r in centered["rows"]}

    specified = {
        ("SCAN_070604", "frame_000002"), ("SCAN_070604", "frame_000004"),
        ("SCAN_165011", "frame_000009"),
        ("SCAN_085223", "frame_000000"), ("SCAN_085223", "frame_000001"),
        ("SCAN_085223", "frame_000009"), ("SCAN_085223", "frame_000010"),
    }
    a_lost, wrong, clean = [], [], []
    for row in centered["rows"]:
        if row["layer"] != "lower":
            continue
        key = (row["scan"], row["frame_id"])
        related = [r for r in survival["rows"] if r["scan"] == row["scan"] and r["frame_id"] == row["frame_id"] and r["context_id"] == row["context_id"] and r["layer"] == "lower"]
        hyp_lost = any(not r["widths_mm"]["50"]["R3_surface_hypotheses"]["exists"] and r["widths_mm"]["50"]["R0_full_selector_input"]["exists"] for r in related)
        survived = any(r["widths_mm"]["50"]["exact_first_loss_stage"] == "SURVIVED_TO_CURRENT_OUTPUT" for r in related)
        if row["classification"].startswith("A_") and hyp_lost and key not in a_lost:
            a_lost.append(key)
        if row["classification"].startswith("C_") and key not in wrong:
            wrong.append(key)
        if row["classification"].startswith("A_") and survived and key not in clean:
            clean.append(key)
    # For the review set, "raw-centered" is object-local: at least half of raw
    # evidence is within ±50 mm and one predeclared robust center is within ±30
    # mm, while the context hypothesis has no ±50 mm center.
    a_ranked = []
    for scan, doc in compact["scans"].items():
        for frame in doc["frames"]:
            for obj in frame["objects"]:
                s = surv_map.get((scan, frame["frame_id"], obj["object_id"]))
                if not s or s["layer"] != "lower":
                    continue
                n50 = s["widths_mm"]["50"]
                if n50["R0_full_selector_input"]["evidence_mass"] < 0.5 or n50["R3_surface_hypotheses"]["exists"]:
                    continue
                truth = float(s["truth_mm"])
                est = obj["object_local_estimators"]
                distance = min(abs(float(est[k]) - truth) for k in ("weighted_median", "huber_location", "trimmed_weighted_mean", "robust_posterior_mean"))
                if distance <= 30:
                    a_ranked.append((distance, scan, frame["frame_id"]))
    a_ranked.sort()
    a_lost = []
    for _, scan, frame_id in a_ranked:
        if (scan, frame_id) not in a_lost:
            a_lost.append((scan, frame_id))
    def diverse(rows: list[tuple[str, str]], count: int = 3) -> list[tuple[str, str]]:
        chosen, seen = [], set()
        for row in rows:
            if row[0] not in seen:
                chosen.append(row); seen.add(row[0])
            if len(chosen) == count:
                return chosen
        for row in rows:
            if row not in chosen:
                chosen.append(row)
            if len(chosen) == count:
                break
        return chosen
    automatic = {"raw_centered_hypothesis_lost": diverse(a_lost), "wrong_centered_raw": diverse(wrong), "clean_correct": diverse(clean)}
    highlighted = specified | {tuple(x) for rows in automatic.values() for x in rows}

    web: dict[str, Any] = {
        "notice": "AUDIT ONLY — NOT PRODUCTION",
        "truth_use": "Ground Truth lines are post-freeze audit labels only.",
        "best_offline_variant": repeatability["best_offline_variant"],
        "highlight_selection": {k: [list(x) for x in v] for k, v in automatic.items()},
        "scans": {},
    }
    image_count = 0
    for scan, doc in compact["scans"].items():
        scan_out = {"frames": []}
        for frame in doc["frames"]:
            source = RESULTS / REPLAYS[scan] / "logs" / frame["frame_id"] / "source_rgb.png"
            image_rel = None
            if source.exists():
                name = f"{scan}_{frame['frame_id']}.png"
                shutil.copy2(source, OUT / "images" / name)
                image_rel = f"images/{name}"
                image_count += 1
            ctx_for = {oid: ctx for ctx in frame["contexts"] for oid in ctx["member_object_ids"]}
            objects = []
            for obj in frame["objects"]:
                s = surv_map.get((scan, frame["frame_id"], obj["object_id"]))
                ctx = ctx_for[obj["object_id"]]
                layer = None if s is None else s["layer"]
                objects.append({
                    "id": obj["object_id"], "class": obj["class_name"], "layer": layer,
                    "current": obj.get("current_support_mm"), "context": ctx["context_id"],
                    "raw_count": obj["r0_full_count"], "serialized_count": obj["r0s_serialized_count"],
                    "loss50": None if s is None else s["widths_mm"]["50"]["exact_first_loss_stage"],
                })
            contexts = []
            for ctx in frame["contexts"]:
                layers = sorted({o["layer"] for o in objects if o["context"] == ctx["context_id"] and o["layer"]})
                classifications = {layer: center_map.get((scan, frame["frame_id"], ctx["context_id"], layer), {}).get("classification") for layer in layers}
                truths = {"lower": 725, "upper": 1065}
                contexts.append({
                    "id": ctx["context_id"], "members": ctx["member_object_ids"], "classes": ctx["member_classes"],
                    "classifications": classifications, "truths": {layer: truths[layer] for layer in layers},
                    "candidates": [{"h": r["height_mm"], "w": r["weight"], "object": r["object_id"], "group": r["provenance_group_id"], "method": r["method"]} for r in ctx["r2_height_preserving_distribution"]],
                    "representatives": [{"h": r["support_mm"], "w": r["normalized_weight"], "group": r["provenance_group_id"], "object": r["object_id"]} for oid in ctx["member_object_ids"] for r in next(o for o in frame["objects"] if o["object_id"] == oid)["r1_round1_representatives"]],
                    "hypotheses": [{"h": r["center_mm"], "p": r["posterior_probability"], "members": r["candidate_count"]} for r in ctx["r3_first_round_hypotheses"]],
                    "modes": [{"h": r["center_mm"], "p": r["probability"]} for r in ctx["p1_p2_p3_estimators"]["modes"][:4]],
                    "estimators": {"weighted median": ctx["p1_p2_p3_estimators"]["weighted_median"], "Huber": ctx["p1_p2_p3_estimators"]["huber_location"], "trimmed mean": ctx["p1_p2_p3_estimators"]["trimmed_weighted_mean"], "posterior mean": ctx["p1_p2_p3_estimators"]["robust_posterior_mean"], "late multimodal": ctx["p1_p2_p3_estimators"]["late_multimodal_soft"]},
                })
            scan_out["frames"].append({"id": frame["frame_id"], "sequence": frame["sequence"], "image": image_rel, "highlight": (scan, frame["frame_id"]) in highlighted, "objects": objects, "contexts": contexts})
        web["scans"][scan] = scan_out

    (OUT / "audit_data.js").write_text("window.AUDIT_DATA=" + json.dumps(web, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    css = """
:root{--bg:#07111d;--card:#101e2c;--ink:#e9f1f7;--muted:#9fb2c2;--a:#57d6c7;--warn:#ffbf69;--bad:#ff6b6b;--truth:#66ff8f}*{box-sizing:border-box}body{margin:0;background:linear-gradient(145deg,#06101a,#0b2030);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,sans-serif}header{position:sticky;top:0;z-index:5;background:#07111dee;border-bottom:1px solid #28445a;padding:15px 24px}.notice{color:var(--warn);font-weight:800;letter-spacing:.08em}.wrap{max-width:1500px;margin:auto;padding:20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}.card{background:var(--card);border:1px solid #29445a;border-radius:12px;padding:15px;box-shadow:0 10px 30px #0004}.frame{margin:20px 0}.highlight{border-color:var(--warn);box-shadow:0 0 0 1px var(--warn),0 10px 30px #0007}.layout{display:grid;grid-template-columns:minmax(250px,360px) 1fr;gap:18px}.preview{width:100%;border-radius:8px;border:1px solid #345}.plot{width:100%;min-height:190px;background:#091522;border-radius:8px}.axis{stroke:#61798d}.truth{stroke:var(--truth);stroke-width:3}.current{stroke:var(--bad);stroke-width:2}.est{stroke:var(--a);stroke-width:2}.candidate{fill:#7fc8ff;opacity:.62}.rep{fill:var(--warn)}.hyp{fill:#ff7cc8}.label{fill:var(--muted);font-size:11px}table{width:100%;border-collapse:collapse}th,td{padding:7px;border-bottom:1px solid #29445a;text-align:left}a{color:#7fdcff}.pill{display:inline-block;padding:2px 7px;margin:2px;border-radius:10px;background:#233c4e;color:#cce3f2}.A_correct_centered{color:var(--truth)}.B_multimodal_truth_peak_substantial{color:var(--warn)}.C_wrong_centered{color:var(--bad)}@media(max-width:850px){.layout{grid-template-columns:1fr}}
"""
    (OUT / "style.css").write_text(css, encoding="utf-8")
    app = r"""
const D=window.AUDIT_DATA;const S=new URLSearchParams(location.search).get('scan')||document.body.dataset.scan;
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function plot(ctx,objects){let all=ctx.candidates.map(x=>x.h).concat(ctx.representatives.map(x=>x.h),ctx.hypotheses.map(x=>x.h),Object.values(ctx.estimators),Object.values(ctx.truths),objects.map(x=>x.current).filter(x=>x!=null));let lo=Math.floor((Math.min(...all)-40)/50)*50,hi=Math.ceil((Math.max(...all)+40)/50)*50;if(hi-lo<200){lo-=100;hi+=100}let x=v=>55+(v-lo)/(hi-lo)*900;let raw=ctx.candidates.map((p,i)=>`<circle class="candidate" cx="${x(p.h)}" cy="${88+(i%5)*9}" r="${Math.max(2,Math.min(7,2+p.w*25))}"><title>raw ${p.h.toFixed(1)} mm; weight ${p.w.toFixed(3)}; ${esc(p.object)}</title></circle>`).join('');let reps=ctx.representatives.map((p,i)=>`<rect class="rep" x="${x(p.h)-2}" y="140" width="4" height="${12+Math.min(25,p.w*40)}"><title>representative ${p.h.toFixed(1)}</title></rect>`).join('');let hyps=ctx.hypotheses.map(p=>`<circle class="hyp" cx="${x(p.h)}" cy="55" r="${5+p.p*8}"><title>hypothesis ${p.h.toFixed(1)}; p=${p.p.toFixed(3)}</title></circle>`).join('');let truth=Object.entries(ctx.truths).map(([k,v])=>`<line class="truth" x1="${x(v)}" x2="${x(v)}" y1="25" y2="175"/><text class="label" x="${x(v)+4}" y="20">GT ${k} ${v}</text>`).join('');let cur=objects.filter(o=>o.context===ctx.id&&o.current!=null).map((o,i)=>`<line class="current" x1="${x(o.current)}" x2="${x(o.current)}" y1="30" y2="175"/><text class="label" x="${x(o.current)+3}" y="${35+i*12}">current ${o.current.toFixed(1)}</text>`).join('');let est=Object.entries(ctx.estimators).map(([k,v],i)=>`<line class="est" x1="${x(v)}" x2="${x(v)}" y1="160" y2="178"><title>${k}: ${v.toFixed(1)}</title></line>`).join('');let ticks='';for(let v=lo;v<=hi;v+=100)ticks+=`<line class="axis" x1="${x(v)}" x2="${x(v)}" y1="178" y2="183"/><text class="label" x="${x(v)-15}" y="198">${v}</text>`;return `<svg viewBox="0 0 1000 205" class="plot">${truth}${cur}${hyps}${raw}${reps}${est}<line class="axis" x1="55" x2="955" y1="178" y2="178"/>${ticks}</svg>`}
function frameCard(f){let ot=f.objects.map(o=>`<tr><td>${esc(o.class)}</td><td>${o.layer||'n/a'}</td><td>${o.current==null?'null':o.current.toFixed(1)}</td><td>${o.raw_count}/${o.serialized_count}</td><td>${esc(o.loss50||'n/a')}</td></tr>`).join('');let plots=f.contexts.map(c=>`<section><h4>${esc(c.id.split(':').slice(-1)[0])} · ${c.classes.map(x=>`<span class="pill">${esc(x)}</span>`).join('')} ${Object.entries(c.classifications).map(([l,v])=>`<span class="${v}">${l}: ${v}</span>`).join(' · ')}</h4>${plot(c,f.objects)}<div>${Object.entries(c.estimators).map(([k,v])=>`<span class="pill">${k} ${v.toFixed(1)}</span>`).join('')}</div></section>`).join('');return `<article id="${f.id}" class="card frame ${f.highlight?'highlight':''}"><h2>${f.id} ${f.highlight?'★ highlighted':''}</h2><div class="layout"><div>${f.image?`<img class="preview" src="${f.image}" alt="RGB preview">`:'RGB unavailable'}<table><thead><tr><th>object</th><th>layer</th><th>current</th><th>R0/R0S</th><th>±50 first loss</th></tr></thead><tbody>${ot}</tbody></table></div><div>${plots}</div></div></article>`}
function renderScan(){let d=D.scans[S];document.querySelector('#title').textContent=S;document.querySelector('#content').innerHTML=d.frames.map(frameCard).join('')}
if(S)renderScan();
"""
    (OUT / "app.js").write_text(app, encoding="utf-8")
    for scan in REPLAYS:
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{scan} candidate audit</title><link rel="stylesheet" href="style.css"></head><body data-scan="{scan}"><header><div class="notice">AUDIT ONLY — NOT PRODUCTION</div><a href="index.html">← all scans</a> · <strong id="title"></strong> · Ground Truth is visualization-only</header><main class="wrap" id="content"></main><script src="audit_data.js"></script><script src="app.js"></script></body></html>"""
        (OUT / f"scan-{scan}.html").write_text(page, encoding="utf-8")
    summary = json.loads((WORKSPACE / "ROUND2_EVALUATION_SUMMARY.json").read_text(encoding="utf-8"))
    cards = []
    for scan, row in summary["survival_summary"].items():
        rates = summary["type_rates_by_scan_lower"][scan]
        cards.append(f"<article class='card'><h2><a href='scan-{scan}.html'>{scan}</a></h2><p>R0 ±50 frame presence: {row['frame_level_raw_near_truth_presence_50mm_rate']:.1%}</p><p><span class='A_correct_centered'>A {rates['A_correct_centered']:.1%}</span> · <span class='B_multimodal_truth_peak_substantial'>B {rates['B_multimodal_truth_peak_substantial']:.1%}</span> · <span class='C_wrong_centered'>C {rates['C_wrong_centered']:.1%}</span></p><p>{row['object_level_loss_stage_50mm']}</p></article>")
    highlight_html = "".join(f"<li>{kind}: " + ", ".join(f"<a href='scan-{s}.html#{f}'>{s} {f}</a>" for s, f in rows) + "</li>" for kind, rows in automatic.items())
    index = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Candidate Distribution Audit Round 2</title><link rel="stylesheet" href="style.css"></head><body><header><div class="notice">AUDIT ONLY — NOT PRODUCTION</div><strong>Single-frame Candidate Information Audit · Round 2</strong></header><main class="wrap"><p>Every estimate is frame-local. The 11 frames are repeatability evaluation only. Ground Truth lines were introduced only after truth-blind estimator freeze.</p><div class="grid">{''.join(cards)}</div><section class="card"><h2>Automatic review set</h2><ul>{highlight_html}</ul><p>Owner-specified frames are additionally highlighted inside scan pages.</p></section><section class="card"><h2>Legend</h2><p>Blue circles: height-preserving raw candidates. Amber bars: first-round provenance representatives. Pink circles: first-round hypotheses. Green: Ground Truth (audit only). Red: current output. Cyan ticks: offline estimators.</p><p>Best repeatability-only offline variant: <strong>{repeatability['best_offline_variant']}</strong>.</p></section></main></body></html>"""
    (OUT / "index.html").write_text(index, encoding="utf-8")

    payload_files = sorted(p for p in OUT.rglob("*") if p.is_file() and p.name not in ("manifest.json", "tree.txt"))
    (OUT / "tree.txt").write_text("\n".join(f"{p.relative_to(OUT).as_posix()}\t{p.stat().st_size}" for p in payload_files) + "\n", encoding="utf-8")
    files = sorted(p for p in OUT.rglob("*") if p.is_file() and p.name != "manifest.json")
    manifest = {
        "schema_version": "candidate_audit_round2_web.v1", "root": str(OUT), "audit_only": True,
        "image_count": image_count, "highlight_selection": {k: [list(x) for x in v] for k, v in automatic.items()},
        "files": [{"path": p.relative_to(OUT).as_posix(), "bytes": p.stat().st_size, "sha256": sha(p)} for p in files],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"root": str(OUT), "images": image_count, "files": len(files) + 1, "automatic": manifest["highlight_selection"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
