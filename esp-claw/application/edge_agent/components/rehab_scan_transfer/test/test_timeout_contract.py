from __future__ import annotations

from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
HEADER = (COMPONENT / "include" / "rehab_scan_transfer.h").read_text(
    encoding="utf-8"
)
SOURCE = (COMPONENT / "src" / "rehab_scan_transfer.c").read_text(
    encoding="utf-8"
)


def test_scan_coordinator_wait_matches_300_second_pc_contract() -> None:
    assert ".total_wait_timeout_ms = 300000U" in HEADER
    assert "handle->config.total_wait_timeout_ms * 1000LL" in SOURCE
    assert 'error_code = "E_PIPELINE_TIMEOUT"' in SOURCE
