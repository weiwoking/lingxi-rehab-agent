from __future__ import annotations


def valid_tof() -> dict:
    return {
        "schema_version": "rehab_tof_raw.v1",
        "sequence": 1,
        "timestamp_us": 0,
        "distance_mm": [2000] * 64,
        "range_sigma_mm": [40] * 64,
        "target_status": [5] * 64,
        "nb_target_detected": [1] * 64,
    }


def valid_profile(**overrides) -> dict:
    value = {
        "schema_version": "deltar_p4_profile.v1",
        "profile_id": "test_v1",
        "sample_kind": "synthetic",
        "image": {"width": 640, "height": 360, "stride": 1280, "pixel_format": "RGB565_LE"},
        "canvas": {"strategy": "letterbox_640x480", "fill_rgb": [0, 0, 0]},
        "zone_mapping": {"strategy": "identity"},
        "histogram": {"mu_strategy": "distance_mm_to_m", "sigma_strategy": "range_sigma_mm_to_m"},
        "mask": {"strategy": "all_valid"},
        "fr": {"strategy": "regular_grid_source_then_transform"},
        "seed": 0,
        "test_intrinsics": {"fx": 500.0, "fy": 500.0, "cx": 320.0, "cy": 180.0},
    }
    value.update(overrides)
    return value
