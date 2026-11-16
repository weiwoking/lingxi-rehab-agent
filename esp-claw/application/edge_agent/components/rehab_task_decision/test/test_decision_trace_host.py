from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
COMPONENT = HERE.parent
SOURCE = COMPONENT / "src" / "rehab_decision_trace.c"
TEST = HERE / "test_decision_trace_host.c"


class DecisionTraceHostTest(unittest.TestCase):
    def test_transitions_elapsed_terminal_freeze_and_generation_binding(self) -> None:
        compiler = shutil.which("gcc")
        if compiler is None:
            self.skipTest("gcc is not installed")
        with tempfile.TemporaryDirectory(prefix="decision_trace_host_") as temp:
            executable = Path(temp) / "decision_trace_host.exe"
            env = os.environ.copy()
            env["PATH"] = str(Path(compiler).parent) + os.pathsep + env["PATH"]
            subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    f"-I{COMPONENT / 'include'}",
                    f"-I{COMPONENT / 'src'}",
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
            self.assertIn("decision-trace host tests passed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
