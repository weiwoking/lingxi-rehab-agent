import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "start_pc_only.ps1"
LIFECYCLE = ROOT / "scripts" / "launcher_lifecycle.ps1"
CANONICAL_CMD = ROOT.parents[1] / "tools" / "ESP_Claw_PC_Launcher" / "START_ESP_CLAW_PC.cmd"


class StartupReadyGateStaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.lifecycle = LIFECYCLE.read_text(encoding="utf-8")
        cls.cmd = CANONICAL_CMD.read_text(encoding="utf-8")

    def test_score_ready_is_checked_before_doctor_starts(self) -> None:
        score_wait = self.source.index("http://127.0.0.1:8020/api/v1/score/health")
        doctor_start = self.source.index("$backendProcess = Start-Process")
        self.assertLess(score_wait, doctor_start)
        self.assertIn('$payload.status -eq "ready"', self.source)
        self.assertIn("$payload.runtime_data.ready -eq $true", self.source)

    def test_doctor_ready_is_checked_before_frontend_starts(self) -> None:
        doctor_wait = self.source.index("http://127.0.0.1:8001/health")
        frontend_start = self.source.index("$frontendProcess = Start-Process")
        self.assertLess(doctor_wait, frontend_start)
        self.assertIn('$payload.status -eq "ok"', self.source)

    def test_frontend_http_ready_precedes_state_and_browser(self) -> None:
        frontend_wait = self.source.index("Wait-FrontendReady $frontendProcess")
        state_save = self.source.index("Write-LauncherStateAtomic")
        browser_open = self.source.index('Start-Process "http://127.0.0.1:5173"')
        self.assertLess(frontend_wait, state_save)
        self.assertLess(state_save, browser_open)
        self.assertIn("Invoke-WebRequest", self.source)

    def test_status_window_wait_does_not_stop_services(self) -> None:
        status = self.source.index("ESP-Claw PC Scoring Stack — RUNNING")
        read_host = self.source.index("Read-Host", status)
        self.assertLess(status, read_host)
        steady_state = self.source[status : self.source.index("\ncatch {", status)]
        self.assertNotIn("Stop-CurrentRole", steady_state)
        self.assertNotIn("Start-Sleep", steady_state)

    def test_canonical_cmd_is_script_relative(self) -> None:
        self.assertIn('%~dp0..\\..', self.cmd)
        self.assertIn("start_pc_only.ps1", self.cmd)
        self.assertNotIn('start "" "http://127.0.0.1:5173"', self.cmd.lower())

    def test_only_8020_has_unconditional_takeover(self) -> None:
        self.assertIn("function Stop-Tcp8020Owner", self.lifecycle)
        self.assertIn("Assert-ConditionalOwnersMatch", self.lifecycle)
        self.assertIn('Stop-ConditionalRole "Frontend"', self.lifecycle)
        self.assertIn('Stop-ConditionalRole "Doctor Backend"', self.lifecycle)

    def test_protected_ports_are_absent_from_termination_helper(self) -> None:
        for protected_port in ("8000", "8030", "8040", "8050", "8765", "5005"):
            self.assertNotIn(protected_port, self.lifecycle)

    def test_failure_path_rolls_back_only_current_roles(self) -> None:
        self.assertIn('Stop-CurrentRole "Frontend"', self.source)
        self.assertIn('Stop-CurrentRole "Doctor"', self.source)
        self.assertIn('Stop-CurrentRole "Score"', self.source)
        self.assertIn("failure.json", self.source)


if __name__ == "__main__":
    unittest.main()
