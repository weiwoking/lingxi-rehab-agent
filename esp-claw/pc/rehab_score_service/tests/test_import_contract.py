import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]


class ImportContractTests(unittest.TestCase):
    def test_asgi_app_imports_from_arbitrary_cwd(self):
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(SERVICE_ROOT)!r}); "
            "from cloud_interface.score_server import PROJECT_ROOT, app; "
            "assert app is not None; "
            f"assert PROJECT_ROOT == __import__('pathlib').Path({str(SERVICE_ROOT)!r})"
        )
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                [sys.executable, "-B", "-c", code],
                cwd=temp,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_has_no_forbidden_heavy_imports(self):
        forbidden = {"torch", "torchvision", "ultralytics", "cv2", "opencv"}
        found = set()
        for path in SERVICE_ROOT.rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module.split(".")[0])
        self.assertFalse(found & forbidden, found & forbidden)

    def test_assets_use_separate_authority_and_code_assets_stay_package_relative(self):
        from cloud_interface import score_server
        from cloud_interface.cloud_score_api import DEFAULT_HEALTHY_TEMPLATE_PATH
        from scoring_assets import SCORING_ASSETS_ROOT

        self.assertEqual(score_server.PROJECT_ROOT, SERVICE_ROOT)
        self.assertTrue(score_server.HEALTHY_TEMPLATE_PATH.is_relative_to(SCORING_ASSETS_ROOT))
        self.assertTrue(score_server.LATERAL_TEMPLATE_PATH.is_relative_to(SCORING_ASSETS_ROOT))
        self.assertTrue(score_server.TASK_REGISTRY_PATH.is_relative_to(SERVICE_ROOT))
        self.assertTrue(DEFAULT_HEALTHY_TEMPLATE_PATH.is_relative_to(SCORING_ASSETS_ROOT))
        self.assertFalse(score_server.HEALTHY_TEMPLATE_PATH.is_relative_to(SERVICE_ROOT))
        self.assertNotIn(r"C:\ec\rehab_score_system", sys.path)


if __name__ == "__main__":
    unittest.main()
