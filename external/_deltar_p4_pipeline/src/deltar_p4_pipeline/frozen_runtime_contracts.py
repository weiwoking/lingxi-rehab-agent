"""Lightweight IPC and path contracts for the persistent scan worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DELTAR_WORKTREE = Path(__file__).resolve().parents[3] / "_deltar_formal" / "work" / "deltar_f3c5175_win_cu128"
DEFAULT_DELTAR_CHECKPOINT = (Path(__file__).resolve().parents[3] / "_deltar_formal" / "assets" / "nyu.pt")


class ScanJobCancelled(RuntimeError):
    """Canary-only cooperative cancellation at a pipeline safe boundary."""


@dataclass(frozen=True)
class ScanJobRequest:
    scan_dir: Path
    output_dir: Path
    profile_dir: Path
    device: str = "cuda:0"
    resume: bool = False
    overwrite: bool = False
    stage7d_horizontal_planes: bool = False
    stage7d_diagnostics: bool = False
    stage7d_profile: Path | None = None
    generate_full_visualization: bool = True
    validation_receipt_required: bool = False
    cancel_path: Path | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ScanJobRequest":
        required = ("scan_dir", "output_dir", "profile_dir")
        missing = [key for key in required if not isinstance(value.get(key), str)]
        if missing:
            raise ValueError(f"missing runtime request paths: {missing}")
        path_fields = {
            "scan_dir",
            "output_dir",
            "profile_dir",
            "stage7d_profile",
            "cancel_path",
        }
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(value) - known)
        if unknown:
            raise ValueError(f"unknown runtime request fields: {unknown}")
        converted = dict(value)
        for key in path_fields:
            raw = converted.get(key)
            converted[key] = None if raw is None else Path(raw)
        return cls(**converted)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key in self.__dataclass_fields__:
            item = getattr(self, key)
            value[key] = str(item) if isinstance(item, Path) else item
        return value


def identity_hints_unchanged(hints: dict[str, dict[str, Any]]) -> bool:
    """Check immutable-runtime stat hints without importing the heavy runtime."""

    for raw_path, expected in hints.items():
        path = Path(raw_path)
        try:
            stat = path.stat()
        except OSError:
            return False
        if stat.st_size != expected.get("size") or stat.st_mtime_ns != expected.get(
            "mtime_ns"
        ):
            return False
    return True
