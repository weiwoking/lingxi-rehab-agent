import json
from pathlib import Path


EDGE_AGENT = Path(__file__).resolve().parents[3]
COMPONENTS = EDGE_AGENT / "components"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_formal_task_ids_are_all_mapped_by_id():
    rules = json.loads(read(
        COMPONENTS / "rehab_task_decision" / "skills" /
        "rehab-task-decision" / "decision_rules.json"
    ))
    source = read(
        COMPONENTS / "rehab_business_audio" / "src" /
        "rehab_business_audio.c"
    )
    task_ids = [task["task_id"] for task in rules["tasks"]]
    assert len(task_ids) == 5
    for task_id in task_ids:
        assert source.count(f'{{"{task_id}",') == 1
    assert "REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_GENERIC" in source


def test_only_core_commit_owners_submit_business_audio():
    owners = {
        "rehab_person_follow/src/rehab_person_follow.c": (
            "REHAB_BUSINESS_AUDIO_PERSON_FOLLOW_STARTED",
            "REHAB_BUSINESS_AUDIO_PERSON_FOLLOW_ARRIVED",
        ),
        "rehab_environment_scan/src/rehab_environment_scan.c": (
            "REHAB_BUSINESS_AUDIO_ENV_SCAN_STARTED",
            "REHAB_BUSINESS_AUDIO_ENV_SCAN_COMPLETED",
        ),
        "rehab_task_decision/src/rehab_task_decision_coordinator.c": (
            "REHAB_BUSINESS_AUDIO_DECISION_STARTED",
        ),
        "rehab_integration/src/rehab_capabilities.c": (
            "rehab_business_audio_training_started(NULL)",
            "rehab_business_audio_training_started(selected.task_id)",
        ),
        "rehab_sensor_recording/src/rehab_training_recorder.c": (
            "REHAB_BUSINESS_AUDIO_TRAINING_COMPLETED",
        ),
    }
    for relative, tokens in owners.items():
        source = read(COMPONENTS / relative)
        for token in tokens:
            assert source.count(token) == 1

    entry_sources = [
        *COMPONENTS.glob("rehab_ui/**/*.c"),
        *COMPONENTS.glob("rehab_person_follow/**/*capabilities.c"),
        *COMPONENTS.glob("rehab_environment_scan/**/*capabilities.c"),
        *COMPONENTS.glob("rehab_task_decision/**/*capabilities.c"),
        *COMPONENTS.glob("rehab_task_decision/**/*console.c"),
    ]
    for path in entry_sources:
        assert "rehab_business_audio" not in read(path), path


def test_audio_is_post_commit_best_effort_and_stop_waiter_is_not_delayed():
    integration = read(
        COMPONENTS / "rehab_integration" / "src" / "rehab_capabilities.c"
    )
    recorder = read(
        COMPONENTS / "rehab_sensor_recording" / "src" /
        "rehab_training_recorder.c"
    )
    selected_start = integration.index(
        "operation_err = rehab_training_start(selected.task_name, result);"
    )
    selected_success = integration.index("if (training_started) {", selected_start)
    selected_prompt = integration.index(
        "rehab_business_audio_training_started(selected.task_id)",
        selected_success,
    )
    assert selected_start < selected_success < selected_prompt

    generic_start = integration.index("err = rehab_training_start(task_name, result);")
    generic_success = integration.index(
        "if (err == ESP_OK && result->accepted &&", generic_start
    )
    generic_prompt = integration.index(
        "rehab_business_audio_training_started(NULL)", generic_success
    )
    assert generic_start < generic_success < generic_prompt

    callback = recorder.index("finalized_callback(session_id")
    waiter = recorder.index("xTaskNotifyGive(waiter)", callback)
    completed = recorder.index("REHAB_BUSINESS_AUDIO_TRAINING_COMPLETED", waiter)
    assert callback < waiter < completed
    assert "rehab_business_audio_training_started" not in recorder
    assert "(void)rehab_business_audio_notify(" in recorder


def test_formal_selected_start_passes_task_id_and_legacy_start_remains():
    integration = read(
        COMPONENTS / "rehab_integration" / "src" / "rehab_capabilities.c"
    )
    recorder = read(
        COMPONENTS / "rehab_sensor_recording" / "src" /
        "rehab_training_recorder.c"
    )
    audio = read(
        COMPONENTS / "rehab_audio_output" / "src" /
        "rehab_audio_output_service.c"
    )
    assert "rehab_training_start(selected.task_name, result)" in integration
    assert "rehab_training_start_task" not in integration
    assert "rehab_training_start_task" not in recorder
    assert "rehab_audio_output_speak_text" in audio


def test_facade_hides_transport_and_sd_details_from_business_owners():
    header = read(
        COMPONENTS / "rehab_business_audio" / "include" /
        "rehab_business_audio.h"
    )
    for forbidden in ("file://", "/sdcard", "8765", "edge-tts", "mp3"):
        assert forbidden not in header.lower()
