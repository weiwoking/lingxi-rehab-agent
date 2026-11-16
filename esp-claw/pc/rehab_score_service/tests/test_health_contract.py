import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_interface import score_server


class HealthContractTests(unittest.TestCase):
    def _asset_paths(self, root: Path):
        return (
            root / "SERVICE_VERSION",
            root / "template" / "MHH_healthy_template.json",
            root / "template" / "lateral_transfer_healthy_template_v1.json",
            root / "config" / "rehab_task_registry_v1.json",
        )

    def test_ready_identity_and_capabilities(self):
        with tempfile.TemporaryDirectory() as temp:
            version, healthy, lateral, registry = self._asset_paths(Path(temp))
            version.write_text("0.1.0\n", encoding="utf-8")
            healthy.parent.mkdir(parents=True)
            healthy.write_text("{}", encoding="utf-8")
            lateral.write_text("{}", encoding="utf-8")
            registry.parent.mkdir(parents=True, exist_ok=True)
            registry.write_text(
                '{"registry_version":"rehab_task_registry_v1","tasks":['
                '{"score_profile":"drink_water"},'
                '{"score_profile":"drink_water"},'
                '{"score_profile":"lateral_transfer"},'
                '{"score_profile":"lateral_transfer"},'
                '{"score_profile":"lateral_transfer"}]}',
                encoding="utf-8",
            )
            with (
                patch.object(score_server, "SERVICE_VERSION_FILE", version),
                patch.object(score_server, "HEALTHY_TEMPLATE_PATH", healthy),
                patch.object(score_server, "LATERAL_TEMPLATE_PATH", lateral),
                patch.object(score_server, "TASK_REGISTRY_PATH", registry),
            ):
                payload = score_server.build_health_payload()

        self.assertEqual(payload["service"], "rehab_score_service")
        self.assertEqual(payload["service_version"], "0.1.0")
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["compatibility_endpoints_available"])
        self.assertEqual(payload["formal_task_count"], 5)
        self.assertEqual(payload["score_profiles"], ["drink_water", "lateral_transfer"])
        self.assertTrue(payload["assets"]["ready"])
        self.assertIn("job_worker", payload)
        self.assertIn("running", payload["job_worker"])
        self.assertIn("active_job_id", payload["job_worker"])
        self.assertIn("live_queue_depth", payload["job_worker"])
        self.assertIn("recovery_backlog", payload["job_worker"])
        self.assertFalse(payload["capabilities"]["fatigue"]["available"])
        self.assertFalse(payload["capabilities"]["brs"]["available"])

    def test_degraded_lists_specific_missing_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            version, healthy, lateral, registry = self._asset_paths(Path(temp))
            version.write_text("0.1.0\n", encoding="utf-8")
            with (
                patch.object(score_server, "SERVICE_VERSION_FILE", version),
                patch.object(score_server, "HEALTHY_TEMPLATE_PATH", healthy),
                patch.object(score_server, "LATERAL_TEMPLATE_PATH", lateral),
                patch.object(score_server, "TASK_REGISTRY_PATH", registry),
            ):
                payload = score_server.build_health_payload()

        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["assets"]["ready"])
        self.assertIn("template/MHH_healthy_template.json", payload["assets"]["missing"])
        self.assertIn("template/lateral_transfer_healthy_template_v1.json", payload["assets"]["missing"])

    def test_health_does_not_execute_scoring(self):
        with (
            patch.object(score_server, "score_trial_json", side_effect=AssertionError("scored")),
        ):
            payload = score_server.score_health()
        self.assertIn(payload["status"], {"ready", "degraded"})

    def test_health_route_is_registered(self):
        paths = {route.path for route in score_server.app.routes}
        self.assertIn("/api/v1/score/health", paths)


if __name__ == "__main__":
    unittest.main()
