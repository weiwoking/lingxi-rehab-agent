import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / "tools" / "P4_Rehab_TTS_Launcher"
COMMON = LAUNCHER / "P4RehabTTS.Common.ps1"


def run_version_gate(version: str):
    common_path = str(COMMON).replace("'", "''")
    command = (
        "$ErrorActionPreference='Stop'; "
        f". '{common_path}'; "
        f"Assert-P4RehabTTSPythonVersionText -VersionText '{version}'"
    )
    return subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_launcher_is_double_clickable_and_repository_relative():
    cmd = (LAUNCHER / "P4_Rehab_TTS_Launcher.cmd").read_text(encoding="utf-8")
    common = (LAUNCHER / "P4RehabTTS.Common.ps1").read_text(encoding="utf-8")
    assert "P4_Rehab_TTS_Launcher.ps1" in cmd
    assert r"..\..\pc\rehab_tts_service" in common
    assert "$script:ServicePort = 8765" in common
    assert "P4RehabTTS\\venv" not in common  # built from LOCALAPPDATA, not a fixed user path


def test_launcher_never_kills_by_port_only():
    common = (LAUNCHER / "P4RehabTTS.Common.ps1").read_text(encoding="utf-8")
    stop_body = common.split("function Stop-P4RehabTTS", 1)[1]
    assert "Get-P4RehabTTSOwnedProcess" in stop_body
    assert "CommandLine" in common
    assert "rehab_tts_service.app:app" in common
    assert "Stop-Process -Id $owned.ProcessId" in stop_body
    assert "Get-NetTCPConnection" not in stop_body.split(
        "function Get-P4RehabTTSStatus", 1
    )[0]


def test_repeated_start_uses_health_identity():
    common = (LAUNCHER / "P4RehabTTS.Common.ps1").read_text(encoding="utf-8")
    start_body = common.split("function Start-P4RehabTTS", 1)[1].split(
        "function Stop-P4RehabTTS", 1
    )[0]
    assert "Get-P4RehabTTSHealth" in start_body
    assert "$health.service -eq $script:ServiceName" in start_body
    assert "Port $($script:ServicePort) is occupied" in start_body


def test_python_version_gate_rejects_39_and_accepts_310_or_newer():
    rejected = run_version_gate("3.9.18")
    assert rejected.returncode != 0
    assert "Python 3.10" in rejected.stderr

    for accepted in ("3.10.0", "3.11.9", "3.13.1", "4.0.0"):
        result = run_version_gate(accepted)
        assert result.returncode == 0, (accepted, result.stderr)


def test_python_version_gate_rejects_unparseable_output():
    result = run_version_gate("Python version unknown")
    assert result.returncode != 0
    assert "Python 3.10 or newer is required" in result.stderr


def test_selected_interpreter_is_checked_before_venv_and_pip():
    common = COMMON.read_text(encoding="utf-8")
    init_body = common.split("function Initialize-P4RehabTTSVenv", 1)[1].split(
        "function Get-P4RehabTTSLanIPv4", 1
    )[0]
    base_check = (
        "Assert-P4RehabTTSPythonVersion -PythonCommand $pythonCommand"
    )
    venv_check = (
        "Assert-P4RehabTTSPythonVersion -PythonCommand @($venvPython)"
    )
    assert init_body.index(base_check) < init_body.index(
        '$arguments += @("-m", "venv", $script:VenvRoot)'
    )
    assert init_body.index(venv_check) < init_body.index(
        '"-m", "pip", "install"'
    )
