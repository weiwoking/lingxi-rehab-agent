from pathlib import Path
import re


SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "claw_cap.c"
).read_text(encoding="utf-8")


def core_output_capacity() -> int:
    match = re.search(
        r"#define\s+CLAW_CAP_CORE_OUTPUT_SIZE\s+"
        r"\((\d+)\s*\*\s*1024\)",
        SOURCE,
    )
    assert match is not None
    return int(match.group(1)) * 1024


def fits_c_string(payload_bytes: int, capacity: int) -> bool:
    return payload_bytes >= 0 and payload_bytes + 1 <= capacity


def test_root_capability_result_boundary_is_64_kib() -> None:
    capacity = core_output_capacity()

    assert capacity == 64 * 1024
    assert fits_c_string(32 * 1024, capacity)
    assert fits_c_string(48 * 1024, capacity)
    assert fits_c_string(capacity - 1, capacity)
    assert not fits_c_string(capacity, capacity)


def test_bridge_remains_temporary_and_psram_preferred() -> None:
    call = SOURCE[
        SOURCE.index("esp_err_t claw_cap_call_from_core") :
        SOURCE.index("static esp_err_t claw_cap_tools_collect_for_caller")
    ]

    assert "heap_caps_calloc_prefer(1, output_size, 2" in call
    assert "MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT" in call
    assert "MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT" in call
    assert "*out_output = output" in call
