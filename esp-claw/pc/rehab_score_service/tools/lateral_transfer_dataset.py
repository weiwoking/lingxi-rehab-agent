from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from feature_extractors.lateral_transfer_profile import EXPECTED_SESSIONS, SESSION_ROOT


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_csv_for_session(session_id: str) -> Path:
    session_dir = SESSION_ROOT / session_id
    raw = session_dir / "raw_session.csv"
    if raw.exists():
        return raw
    csv_files = sorted(session_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"lateral_transfer_source_csv_missing:{session_id}")
    return csv_files[0]


def iter_lateral_session_specs() -> list[dict[str, Any]]:
    rows = []
    for session_id, labels in EXPECTED_SESSIONS.items():
        path = source_csv_for_session(session_id)
        rows.append(
            {
                "session_id": session_id,
                "source_csv": str(path),
                "expected_rep_count": len(labels),
                "expected_labels": labels,
                "source_sha256": sha256_file(path) if path.exists() else None,
                "exists": path.exists(),
            }
        )
    return rows


def write_dataset_manifest(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = iter_lateral_session_specs()
    manifest = {
        "motion_family": "lateral_transfer",
        "session_count": len(rows),
        "expected_rep_count": sum(row["expected_rep_count"] for row in rows),
        "sessions": rows,
        "label_counts": {
            label: sum(row["expected_labels"].count(label) for row in rows)
            for label in ("normal", "lf", "tremor")
        },
    }
    json_path = output_dir / "lateral_transfer_dataset_manifest.json"
    csv_path = output_dir / "lateral_transfer_dataset_manifest.csv"
    json_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    return json_path
