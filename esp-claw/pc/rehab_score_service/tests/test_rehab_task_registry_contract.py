from __future__ import annotations

import unittest

from cloud_interface.task_registry import (
    TASK_REGISTRY_PATH,
    TASK_REGISTRY_VERSION,
    load_task_registry,
    resolve_task,
)
from tools.drink_water_v1_pipeline import DRINK_WATER_V1_IMU_SEGMENTS


EXPECTED_MATRIX = [
    ("水杯拿取与饮水训练（二级）", "drink_water", "cup", "drink_water", "drink_water", 2),
    ("水杯拿取与饮水训练（三级）", "drink_water", "cup", "drink_water", "drink_water", 3),
    ("水杯横向取放训练（一级）", "lateral_transfer", "cup", "lateral_transfer", "lateral_transfer", 1),
    ("球类横向取放训练（一级）", "lateral_transfer", "ball", "lateral_transfer", "lateral_transfer", 1),
    ("球类横向取放训练（二级）", "lateral_transfer", "ball", "lateral_transfer", "lateral_transfer", 2),
]


class RehabTaskRegistryContractTest(unittest.TestCase):
    def test_registry_file_is_canonical_authority(self) -> None:
        self.assertEqual(TASK_REGISTRY_VERSION, "rehab_task_registry_v1")
        self.assertEqual(TASK_REGISTRY_PATH.name, "rehab_task_registry_v1.json")
        registry = load_task_registry()
        self.assertEqual(registry["registry_version"], TASK_REGISTRY_VERSION)
        self.assertEqual(len(registry["tasks"]), 5)

    def test_five_task_routing_matrix(self) -> None:
        for task_name, family, object_type, score_profile, twin_profile, level in EXPECTED_MATRIX:
            with self.subTest(task_name=task_name):
                resolved = resolve_task(task_name)
                self.assertEqual(resolved["task_registry_status"], "resolved")
                self.assertEqual(resolved["task_name"], task_name)
                self.assertEqual(resolved["display_name"], task_name)
                self.assertEqual(resolved["motion_family"], family)
                self.assertEqual(resolved["object_type"], object_type)
                self.assertEqual(resolved["score_profile"], score_profile)
                self.assertEqual(resolved["twin_profile"], twin_profile)
                self.assertEqual(resolved["level"], level)

    def test_lateral_cup_is_not_drink(self) -> None:
        resolved = resolve_task("水杯横向取放训练（一级）")
        self.assertEqual(resolved["motion_family"], "lateral_transfer")
        self.assertEqual(resolved["object_type"], "cup")
        self.assertEqual(resolved["score_profile"], "lateral_transfer")
        self.assertEqual(resolved["twin_profile"], "lateral_transfer")

    def test_unknown_task_does_not_default_to_drink(self) -> None:
        resolved = resolve_task("不存在的训练任务")
        self.assertEqual(resolved["task_registry_status"], "unknown")
        self.assertEqual(resolved["unsupported_task_reason"], "UNKNOWN_TASK")
        self.assertNotEqual(resolved.get("motion_family"), "drink_water")
        self.assertNotEqual(resolved.get("score_profile"), "drink_water")

    def test_current_drink_pipeline_uses_authoritative_imu_mapping(self) -> None:
        self.assertEqual(
            DRINK_WATER_V1_IMU_SEGMENTS,
            {
                "upperarm": "imu1",
                "trunk": "imu2",
                "forearm": "imu3",
                "hand_wrist": "imu4",
            },
        )


if __name__ == "__main__":
    unittest.main()
