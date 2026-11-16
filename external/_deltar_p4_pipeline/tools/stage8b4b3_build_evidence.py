from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path


PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
REPLAY = Path(r"C:\ec\_stage8b4b3_replay_20260726_030615")
SCAN_ID = "scan_20260725_184615_000"
RESULT = REPLAY / "results" / SCAN_ID
ORIGINAL_RESULT = (
    Path(r"C:\ec\_deltar_scan_service_data\results") / SCAN_ID
)
EVIDENCE = (
    PIPELINE / "tmp" / "stage8b4b3_evidence_20260726_031011"
)
ZIP_PATH = Path(
    r"C:\Users\Weiwo\Downloads"
    r"\STAGE8B4B3_PC_FRAME_DIAGNOSTIC_COMPAT_EVIDENCE_20260726_031011.zip"
)


def copy(source: Path, relative: str) -> None:
    target = EVIDENCE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def write(relative: str, text: str) -> None:
    target = EVIDENCE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    sources = [
        "src/deltar_p4_pipeline/stage6_schema.py",
        "src/deltar_p4_pipeline/frame_pipeline.py",
        "src/deltar_p4_pipeline/scan_job.py",
        "src/deltar_p4_pipeline/scan_service_worker.py",
        "tests/test_stage8b4b3_diagnostic_compat.py",
        "tools/stage8b4b3_replay.py",
        "tools/stage8b4b3_cached_finalize.py",
        "tools/stage8b4b3_verify.py",
        "tools/stage8b4b3_build_evidence.py",
    ]
    for relative in sources:
        copy(PIPELINE / relative, f"modified_sources/{relative}")

    write(
        "source_before_after.md",
        """# Stage 8B-4B-3 source before/after evidence

## Frame diagnostics boundary

Before, `stage6_schema._errors()` called `_string(message)` directly. Missing,
empty, blank, non-string, or over-512 diagnostic text raised
`E_FRAME_RESULT_SCHEMA`.

After, `normalize_frame_diagnostics()` normalizes top-level and object
diagnostics before the unchanged core schema checks. It supplies the stable
fallback, generic code, deterministic text conversion, 512-character bound,
and bounded dropped-item warning.

## Rejected-frame job report

Before:

```python
rejected = {"frame_id": ..., "sequence": ..., "error": {
    "code": "E_FRAME_PROCESSING", "phase": exc.phase, "message": exc.message}}
```

This copied the raw overlong message after the frame result had already been
normalized.

After:

```python
rejected_result = _rejected_frame_result(...)
rejected_frames.append({
    "frame_id": ..., "sequence": ...,
    "error": rejected_result["errors"][0],
})
```

The job report now reuses the single normalized diagnostic.

## Worker error propagation

Before:

```python
if completed.returncode != 0:
    raise ServiceError("E_PIPELINE_FAILED", "pipeline worker failed", ...)
```

After, `structured_worker_error()` accepts only bounded structured JSON stdout
with a valid `E_*` code and short message; the generic error remains the
fallback.
""",
    )
    write(
        "replay_command.txt",
        r"""$env:PYTHONPATH=C:\ec\_deltar_p4_pipeline\src
C:\ec\_conda_envs\vision_interpreter\python.exe C:\ec\_deltar_p4_pipeline\tools\stage8b4b3_replay.py --source-scan C:\ec\_deltar_scan_service_data\validated\scan_20260725_184615_000 --data-root C:\ec\_stage8b4b3_replay_20260726_030615 --pipeline-root C:\ec\_deltar_p4_pipeline --worker-python C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe --device cuda:0

The GPU/shared inference command above ran exactly once. After it exposed the
same diagnostic at the job-report boundary, cached finalization ran with zero
new inference invocations.
""",
    )
    write(
        "cached_finalize_command.txt",
        r"""C:\ec\_conda_envs\vision_interpreter\python.exe C:\ec\_deltar_p4_pipeline\tools\stage8b4b3_cached_finalize.py --data-root C:\ec\_stage8b4b3_replay_20260726_030615 --pipeline-root C:\ec\_deltar_p4_pipeline --worker-python C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe --scan-id scan_20260725_184615_000
""",
    )
    write(
        "modified_files.txt",
        "\n".join(sources[:-1]) + "\n",
    )

    copies = [
        (
            ORIGINAL_RESULT / "logs" / "service_worker.stdout.log",
            "original_failure/service_worker.stdout.log",
        ),
        (
            ORIGINAL_RESULT / "logs" / "service_phases.jsonl",
            "original_failure/service_phases.jsonl",
        ),
        (
            RESULT / "logs" / "service_worker.stdout.log",
            "real_replay/service_worker.stdout.log",
        ),
        (
            RESULT / "logs" / "service_worker.stderr.log",
            "real_replay/service_worker.stderr.log",
        ),
        (
            RESULT / "logs" / "service_phases.jsonl",
            "real_replay/service_phases.jsonl",
        ),
        (
            RESULT / "logs" / "pipeline_phases.jsonl",
            "real_replay/pipeline_phases.jsonl",
        ),
        (
            RESULT / "logs" / "frame_diagnostics.jsonl",
            "diagnostics/frame_diagnostics.jsonl",
        ),
        (
            RESULT / "frame_results" / "frame_000002.json",
            "diagnostics/frame_000002_after_normalization.json",
        ),
        (
            RESULT / "stage8b4a_p4_decision_package.v1.json",
            "package/stage8b4a_p4_decision_package.v1.json",
        ),
        (
            RESULT / "stage8b4a_p4_decision_package.v1.json.sha256",
            "package/stage8b4a_p4_decision_package.v1.json.sha256",
        ),
        (RESULT / "service_job.json", "status/service_job.json"),
        (RESULT / "job_report.json", "status/job_report.json"),
        (
            REPLAY / "stage8b4b3_read_only_verification.json",
            "status/decision_status_and_get_verification.json",
        ),
        (
            RESULT / "logs" / "stage8b3.stdout.log",
            "status/stage8b3.stdout.log",
        ),
        (
            RESULT / "logs" / "stage8b3.stderr.log",
            "status/stage8b3.stderr.log",
        ),
        (
            RESULT / "stage8b1_yolo_batch_manifest.json",
            "inference/stage8b1_yolo_batch_manifest.json",
        ),
        (
            RESULT / "stage8b1_yolo_batch_timing.json",
            "inference/stage8b1_yolo_batch_timing.json",
        ),
        (
            RESULT / "visualization" / "visualization_manifest.v1.json",
            "status/visualization_manifest.v1.json",
        ),
    ]
    for source, relative in copies:
        copy(source, relative)

    p4 = subprocess.run(
        [
            "git",
            "-C",
            r"C:\ec\esp-claw\application\edge_agent",
            "status",
            "--short",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    write("p4_git_status_short.txt", p4.stdout + p4.stderr)

    manifest: dict[str, dict[str, str | int]] = {}
    for path in sorted(EVIDENCE.rglob("*")):
        if path.is_file() and path.name not in {"manifest.json", "manifest.sha256"}:
            relative = path.relative_to(EVIDENCE).as_posix()
            manifest[relative] = {
                "length": path.stat().st_size,
                "sha256": sha256(path),
            }
    manifest_path = EVIDENCE / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    write("manifest.sha256", f"{sha256(manifest_path)}  manifest.json\n")

    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(EVIDENCE.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(EVIDENCE).as_posix())
    print(json.dumps({
        "evidence_root": str(EVIDENCE),
        "zip_path": str(ZIP_PATH),
        "zip_length": ZIP_PATH.stat().st_size,
        "zip_sha256": sha256(ZIP_PATH),
        "file_count": sum(path.is_file() for path in EVIDENCE.rglob("*")),
        "report_in_zip": any(
            "STAGE8B4B3_PC_FRAME_DIAGNOSTIC_COMPAT_REPORT.md" in name
            for name in zipfile.ZipFile(ZIP_PATH).namelist()
        ),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
