import re
from pathlib import Path


EDGE_AGENT = Path(__file__).resolve().parents[3]
COMPONENTS = EDGE_AGENT / "components"
REPO = EDGE_AGENT.parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def function_body(source: str, signature: str) -> str:
    match = re.search(
        rf"{re.escape(signature)}.*?^\}}",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, signature
    return match.group(0)


def test_start_audio_runs_only_after_baseline_recorder_call_returns():
    integration = read(
        COMPONENTS / "rehab_integration" / "src" / "rehab_capabilities.c"
    )
    selected = function_body(
        integration, "static esp_err_t rehab_training_start_selected_execute("
    )
    generic = function_body(
        integration, "static esp_err_t rehab_training_start_execute("
    )

    selected_call = selected.index(
        "rehab_training_start(selected.task_name, result)"
    )
    selected_success = selected.index("if (training_started) {", selected_call)
    selected_audio = selected.index(
        "rehab_business_audio_training_started(selected.task_id)",
        selected_success,
    )
    assert selected_call < selected_success < selected_audio

    generic_call = generic.index("rehab_training_start(task_name, result)")
    generic_success = generic.index(
        "if (err == ESP_OK && result->accepted &&", generic_call
    )
    generic_audio = generic.index(
        "rehab_business_audio_training_started(NULL)", generic_success
    )
    assert generic_call < generic_success < generic_audio


def test_recorder_start_has_baseline_api_and_no_start_audio_dependency():
    header = read(
        COMPONENTS / "rehab_sensor_recording" / "include" /
        "rehab_sensor_recording.h"
    )
    recorder = read(
        COMPONENTS / "rehab_sensor_recording" / "src" /
        "rehab_training_recorder.c"
    )
    start = function_body(recorder, "esp_err_t rehab_training_start(")

    assert "rehab_training_start_task" not in header
    assert "rehab_training_start_task" not in recorder
    assert "rehab_business_audio_training_started" not in start
    assert "g_rehab_sensor.state = REHAB_TRAINING_STATE_RECORDING" in start


def test_failure_paths_do_not_submit_start_audio_and_completed_order_is_kept():
    integration = read(
        COMPONENTS / "rehab_integration" / "src" / "rehab_capabilities.c"
    )
    recorder = read(
        COMPONENTS / "rehab_sensor_recording" / "src" /
        "rehab_training_recorder.c"
    )
    assert integration.count("rehab_business_audio_training_started(NULL)") == 1
    assert integration.count(
        "rehab_business_audio_training_started(selected.task_id)"
    ) == 1

    callback = recorder.index("finalized_callback(session_id")
    waiter = recorder.index("xTaskNotifyGive(waiter)", callback)
    completed = recorder.index("REHAB_BUSINESS_AUDIO_TRAINING_COMPLETED", waiter)
    assert callback < waiter < completed


def test_cap_lua_async_stack_configuration_is_unchanged():
    cap_lua_async = read(
        REPO / "components" / "claw_capabilities" / "cap_lua" / "src" /
        "cap_lua_async.c"
    )
    assert re.search(r"\.stack_size\s*=\s*12\s*\*\s*1024", cap_lua_async)
