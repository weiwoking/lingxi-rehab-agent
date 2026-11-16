from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = REPO_ROOT / "tools" / "Start-VisionAndScanPC" / "start_vision_and_scan_services.ps1"
OLD_LAUNCHER = REPO_ROOT / "tools" / "vision_launcher"


def test_old_launcher_is_removed():
    assert not OLD_LAUNCHER.exists()


def test_unified_launcher_uses_two_health_gates_and_one_console():
    source = LAUNCHER.read_text(encoding="utf-8-sig")
    assert "C:\\ec\\_conda_envs\\vision_interpreter" in source
    assert "pc\\vision_interpreter" in source
    assert "app.main:app" in source
    assert "$script:VisionHealthUrl" in source
    assert "$script:ScanLocalHealthUrl" in source
    assert "--host', '0.0.0.0'" in source
    assert "--auto-open-visualization" not in source
    assert source.count("Start-Process -FilePath $script:ConsoleUrl") == 1
    assert "$visionState.Pass -and $scanState.Pass" in source
    assert "http://127.0.0.1:8030/vision-console" in source
    assert "Test-ExpectedProcess" in source
    assert "Format-PortOwnerDetails" in source
    assert "run_offline_preflight.py" in source
    assert "--require-local-preflight" in source
    assert "runtime_network_required" in source
    assert "checkpoint_key_audit" in source
