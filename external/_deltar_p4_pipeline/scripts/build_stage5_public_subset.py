from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];path=ROOT/"outputs_stage5"/"public_subset_status.json"
payload={"status":"not_downloaded","blocking":False,"download_bytes":0,"reason":"No official direct subset satisfies the <=1 GB and explicit-license gates; see dataset_audit.json.","raw_data_directory":str(ROOT/"datasets_external")}
path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8");print(json.dumps(payload,indent=2,ensure_ascii=False))
