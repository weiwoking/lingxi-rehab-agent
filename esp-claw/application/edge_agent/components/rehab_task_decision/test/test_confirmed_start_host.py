from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import os
from pathlib import Path


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "src" / "rehab_task_decision_confirmed_start.c"
STUBS = HERE / "confirmed_start_host_stubs"
TEST = HERE / "test_confirmed_start_host.c"


class ConfirmedStartHostTest(unittest.TestCase):
    def test_facade_outcomes_call_counts_and_concurrency(self) -> None:
        compiler = shutil.which("gcc")
        if compiler is None:
            self.skipTest("gcc is not installed")
        with tempfile.TemporaryDirectory(prefix="confirmed_start_host_") as temp:
            executable = Path(temp) / "confirmed_start_host.exe"
            env = os.environ.copy()
            env["PATH"] = str(Path(compiler).parent) + os.pathsep + env["PATH"]
            subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-pthread",
                    f"-I{STUBS}",
                    str(SOURCE),
                    str(TEST),
                    "-o",
                    str(executable),
                ],
                check=True,
                cwd=HERE,
                env=env,
            )
            completed = subprocess.run(
                [str(executable)], check=True, capture_output=True, text=True,
                env=env,
            )
            self.assertIn("confirmed-start host tests passed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
