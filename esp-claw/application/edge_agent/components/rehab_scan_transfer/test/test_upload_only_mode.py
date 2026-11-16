from __future__ import annotations

from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
HEADER = (COMPONENT / "include" / "rehab_scan_transfer.h").read_text(
    encoding="utf-8"
)
SOURCE = (COMPONENT / "src" / "rehab_scan_transfer.c").read_text(
    encoding="utf-8"
)
PRIVATE = (COMPONENT / "src" / "rehab_scan_transfer_priv.h").read_text(
    encoding="utf-8"
)
HTTP = (COMPONENT / "src" / "rehab_scan_transfer_http.c").read_text(
    encoding="utf-8"
)
CONSOLE = (COMPONENT / "src" / "rehab_scan_transfer_console.c").read_text(
    encoding="utf-8"
)


def test_eleven_frame_six_mib_transfer_bounds() -> None:
    assert ".max_upload_bytes = 6291456U" in HEADER
    assert ".max_frames = 11U" in HEADER
    assert "config->max_frames > 11" in SOURCE
    assert "config->max_upload_bytes > 6291456U" in SOURCE
    assert "REHAB_SCAN_TRANSFER_MAX_PARTS          34U" in PRIVATE


def test_upload_only_uses_explicit_pc_mode_and_stops_before_pipeline_poll() -> None:
    assert "REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE = 0" in HEADER
    assert "REHAB_SCAN_TRANSFER_MODE_UPLOAD_ONLY" in HEADER
    assert '"/api/v1/scan/jobs?mode=upload_only"' in HTTP
    upload_branch = SOURCE.index(
        "handle->mode == REHAB_SCAN_TRANSFER_MODE_UPLOAD_ONLY"
    )
    waiting = SOURCE.index(
        "REHAB_SCAN_TRANSFER_STATE_WAITING_PROCESSING", upload_branch
    )
    polling = SOURCE.index("rehab_scan_transfer_http_get_json", waiting)
    result = SOURCE.index(
        "rehab_scan_transfer_http_download_result", polling
    )
    assert upload_branch < waiting < polling < result


def test_default_and_manual_compatibility_are_preserved() -> None:
    wrapper = SOURCE[SOURCE.index(
        "esp_err_t rehab_scan_transfer_start("
    ):]
    assert "REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE" in wrapper
    assert "rehab_scan_transfer_start(handle, argv[1]" in CONSOLE
    assert "upload-only" in CONSOLE
    assert "REHAB_SCAN_TRANSFER_MODE_UPLOAD_ONLY" in CONSOLE


def test_explicit_selector_remains_exact_not_latest() -> None:
    scan_source = (
        COMPONENT / "src" / "rehab_scan_transfer_scan.c"
    ).read_text(encoding="utf-8")
    selector = scan_source[scan_source.index(
        "esp_err_t rehab_scan_transfer_select_and_validate"
    ):]
    assert 'strcmp(selector, "latest")' in selector
    assert "strlcpy(out_plan->scan_id, selector" in selector
    assert "!directory_exists(out_plan->scan_path)" in selector
