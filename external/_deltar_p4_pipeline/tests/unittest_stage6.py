from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deltar_p4_pipeline.scan_aggregation import aggregate_frames
from deltar_p4_pipeline.scan_profiles import load_profile_bundle

ROOT = Path(__file__).resolve().parents[1]


def observation(frame_id: str, bbox: list[float], complete: bool = True) -> dict:
    visibility = {"visibility_status": "complete" if complete else "partial", "top_visible": complete,
                  "bottom_visible": True, "full_extent_visible": complete, "touches_image_border": not complete}
    return {"frame_object_id": f"{frame_id}:seg", "class_id": 56, "class_name": "chair", "class_scope": "core_now",
            "object_role": "operable_object", "object_source": "segmentation", "confidence": 0.8, "bbox_xyxy": bbox,
            "canvas": {"comparison": {"agreement_status": "high"}, "selected": "direct_640x360"},
            "distance_m": 1.2, "distance_semantics": "visible_surface_distance", "centroid_3d_m": None,
            "geometry_point_count": 100, "depth_compactness": 0.9, "visibility": visibility,
            "visible_height": {"value_m": 0.5, "available": True, "absolute_valid": False},
            "physical_height": {"value_m": None, "available": False, "absolute_valid": False},
            "bottom_height": {"value_m": None, "available": False, "absolute_valid": False},
            "placement_level": "unknown", "warnings": [], "rejection_reason": None}


def frame(frame_id: str, sequence: int, objects: list[dict]) -> dict:
    return {"frame_id": frame_id, "sequence": sequence, "status": "success", "objects": objects,
            "warnings": [], "processing_ms": {}}


class Stage6CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)

    def test_partial_never_forges_physical_height(self) -> None:
        obs = observation("frame_000000", [0, 0, 100, 100], complete=False)
        objects, _, _ = aggregate_frames([frame("frame_000000", 0, [obs])], self.bundle)
        self.assertIsNone(objects[0]["physical_height"]["value_m"])
        self.assertFalse(objects[0]["physical_height"]["absolute_valid"])

    def test_repeat_aggregation_is_deterministic(self) -> None:
        frames = [frame("frame_000000", 0, [observation("frame_000000", [10, 10, 100, 100])])]
        self.assertEqual(aggregate_frames(frames, self.bundle), aggregate_frames(frames, self.bundle))


if __name__ == "__main__":
    unittest.main()
