from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"src")); sys.path.insert(0,str(ROOT))
from deltar_p4_pipeline.appearance_compositor import build_official_chair_texture
from deltar_p4_pipeline.analytic_renderer import stage5_scene_family
from scripts.build_stage5_analytic_scenes import build


def main() -> None:
    texture=ROOT/"samples"/"stage5_assets"/"official_chair.png"; manifest=build_official_chair_texture(ROOT,texture)
    output=ROOT/"samples"/"stage5_appearance"; output.mkdir(parents=True,exist_ok=True)
    scenes=[s for s in stage5_scene_family(str(texture)) if s["appearance_kind"]!="analytic"]
    rows=build(scenes,output); (output/"asset_manifest.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"status":"pass","scene_count":len(rows),"object_count":sum(r["object_count"] for r in rows),
        "render_mean_ms":sum(r["render_ms"] for r in rows)/len(rows),"tof_simulation_mean_ms":sum(r["tof_simulation_ms"] for r in rows)/len(rows),"asset":manifest},indent=2,ensure_ascii=False))


if __name__=="__main__":main()
