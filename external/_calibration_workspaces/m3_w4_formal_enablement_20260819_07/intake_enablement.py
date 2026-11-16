from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

W=Path(__file__).resolve().parent
REPO=Path(r"C:\ec\_deltar_p4_pipeline")
INPUTS=[
    Path(r"C:\ec\_codex_reports\geometry_final_research_closure_round5_20260819_01\REPORT.md"),
    Path(r"C:\ec\_codex_reports\geometry_final_research_closure_round5_20260819_01\geometry_final_research_closure_round5_20260819_01_evidence.zip"),
    Path(r"C:\ec\_codex_reports\m3_peak_weighting_audit_20260819_01\REPORT.md"),
    Path(r"C:\ec\_codex_reports\m3_peak_weighting_audit_20260819_01\m3_peak_weighting_audit_20260819_01_evidence.zip"),
    Path(r"C:\ec\_codex_reports\single_frame_candidate_information_audit_round2_20260819_01\REPORT.md"),
    Path(r"C:\ec\_codex_reports\single_frame_candidate_information_audit_round2_20260819_01\single_frame_candidate_information_audit_round2_20260819_01_evidence.zip"),
]
PROTECTED={
    "stage2_payload":REPO/r"profiles\formal_geometry_v2\stage2_calibration.json",
    "stage2_projection":REPO/r"src\deltar_p4_pipeline\stage2_projection.py",
    "formal_deltar_d":REPO/r"src\deltar_p4_pipeline\formal_deltar_d.py",
    "stage3a":REPO/r"profiles\formal_geometry_v2\stage3a_gravity.json",
    "support_source":REPO/r"src\deltar_p4_pipeline\all_object_geometry.py",
    "support_selector":REPO/r"src\deltar_p4_pipeline\geometry_v2\support_selector.py",
    "support_config":REPO/r"profiles\formal_geometry_v2\all_object_geometry_v2.json",
    "checkpoint":Path(r"C:\ec\_deltar_formal\assets\nyu.pt"),
    "transformer":Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128\src\models\transformer.py"),
    "decoder":Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128\src\models\decoder.py"),
    "fusion":Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128\src\models\fusion.py"),
}


def sha_bytes(v):return hashlib.sha256(v).hexdigest()
def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
    return h.hexdigest()
def run(*a):return subprocess.run(["git","-C",str(REPO),*a],check=True,capture_output=True).stdout


def git_state():
    diff=run("diff","--binary","--no-ext-diff")
    return {"head":run("rev-parse","HEAD").decode().strip(),"status":run("status","--short","--branch").decode("utf-8",errors="replace"),"diff_bytes":len(diff),"diff_sha256":sha_bytes(diff)}


def tracked_tree():
    names=run("ls-files","-z").split(b"\0");rows=[]
    for raw in names:
        if not raw:continue
        rel=raw.decode();p=REPO/rel
        if p.is_file():rows.append((rel.replace("\\","/"),p.stat().st_size,sha(p)))
    h=hashlib.sha256()
    for rel,size,d in rows:h.update(f"{rel}\0{size}\0{d}\n".encode())
    return {"tracked_file_count":len(rows),"sha256":h.hexdigest()}


def main():
    out={"schema_version":"m3_w4_enablement_intake.v1","repo":str(REPO),"git_pre":git_state(),"tracked_tree_pre":tracked_tree(),"protected_hashes_pre":{k:{"path":str(p),"bytes":p.stat().st_size,"sha256":sha(p)} for k,p in PROTECTED.items()},"inputs":[]}
    members=uncompressed=0
    for p in INPUTS:
        payload=p.read_bytes();row={"path":str(p),"bytes":len(payload),"sha256":sha_bytes(payload),"fully_read":True}
        if p.suffix.lower()==".zip":
            row["members"]=[]
            with zipfile.ZipFile(p) as z:
                for info in z.infolist():
                    data=z.read(info.filename);members+=1;uncompressed+=len(data)
                    row["members"].append({"name":info.filename,"bytes":len(data),"sha256":sha_bytes(data),"fully_read":True})
        out["inputs"].append(row)
    out["read_totals"]={"zip_members":members,"zip_uncompressed_bytes":uncompressed}
    (W/"INTAKE_BASELINE.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"git":out["git_pre"],"tree":out["tracked_tree_pre"],"read":out["read_totals"]},indent=2))


if __name__=="__main__":main()
