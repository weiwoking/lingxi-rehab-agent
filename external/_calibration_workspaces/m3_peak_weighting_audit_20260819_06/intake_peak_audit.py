from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

W = Path(__file__).resolve().parent
INPUTS = [
    Path(r"C:\ec\_codex_reports\single_frame_candidate_information_audit_round2_20260819_01\REPORT.md"),
    Path(r"C:\ec\_codex_reports\single_frame_candidate_information_audit_round2_20260819_01\single_frame_candidate_information_audit_round2_20260819_01_evidence.zip"),
    Path(r"C:\ec\_codex_reports\geometry_final_research_closure_round5_20260819_01\REPORT.md"),
    Path(r"C:\ec\_codex_reports\geometry_final_research_closure_round5_20260819_01\geometry_final_research_closure_round5_20260819_01_evidence.zip"),
]
SOURCE = Path(r"C:\ec\_calibration_workspaces\single_frame_candidate_information_audit_20260819_02\extract_and_counterfactual.py")
COMPACT = Path(r"C:\ec\_calibration_workspaces\single_frame_candidate_information_audit_20260819_02\round2_compact_candidate_data.json")


def digest_bytes(data: bytes):
    return hashlib.sha256(data).hexdigest()


def digest(path: Path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
    return h.hexdigest()


def git(repo: str):
    def run(*args): return subprocess.run(["git","-C",repo,*args],check=True,capture_output=True).stdout
    diff=run("diff","--binary","--no-ext-diff")
    return {"repo":repo,"head":run("rev-parse","HEAD").decode().strip(),"status":run("status","--short","--branch").decode("utf-8",errors="replace"),"diff_bytes":len(diff),"diff_sha256":digest_bytes(diff)}


def main():
    out={"schema_version":"m3_peak_audit_intake.v1","git_pre":{"pipeline":git(r"C:\ec\_deltar_p4_pipeline"),"esp_claw":git(r"C:\ec\esp-claw")},"inputs":[],"authoritative_source":{"path":str(SOURCE),"bytes":SOURCE.stat().st_size,"sha256":digest(SOURCE)},"frozen_compact":{"path":str(COMPACT),"bytes":COMPACT.stat().st_size,"sha256":digest(COMPACT)}}
    members=decompressed=0
    for p in INPUTS:
        data=p.read_bytes(); row={"path":str(p),"bytes":len(data),"sha256":digest_bytes(data),"fully_read":True}
        if p.suffix.lower()==".zip":
            row["members"]=[]
            with zipfile.ZipFile(p) as z:
                for info in z.infolist():
                    payload=z.read(info.filename); members+=1; decompressed+=len(payload)
                    row["members"].append({"name":info.filename,"bytes":len(payload),"sha256":digest_bytes(payload),"fully_read":True})
        out["inputs"].append(row)
    out["read_totals"]={"zip_members":members,"zip_decompressed_bytes":decompressed}
    (W/"INTAKE_FREEZE.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(out["read_totals"],indent=2))


if __name__=="__main__": main()
