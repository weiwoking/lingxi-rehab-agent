from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
ARTIFACT = (
    COMPONENT / "src" / "rehab_task_decision_formal_input_artifact.c"
).read_text(encoding="utf-8")
COORDINATOR = (
    COMPONENT / "src" / "rehab_task_decision_coordinator.c"
).read_text(encoding="utf-8")
CMAKE = (COMPONENT / "CMakeLists.txt").read_text(encoding="utf-8")


def function_body(source: str, signature: str, next_signature: str) -> str:
    return source[source.index(signature) : source.index(next_signature)]


def test_artifact_is_the_exact_formal_input_bytes() -> None:
    assert '"rehab/latest_formal_decision_input.json"' in ARTIFACT
    assert "CLAW_PATH_DATA, FORMAL_INPUT_ARTIFACT_SUBPATH" in ARTIFACT
    assert (
        "fwrite(formal_input, 1, formal_input_bytes, file)"
        in ARTIFACT
    )
    assert "cJSON" not in ARTIFACT
    assert "formal_input_artifact.c" in CMAKE


def test_save_occurs_after_success_log_and_is_non_fatal() -> None:
    render = function_body(
        COORDINATOR,
        "static esp_err_t formal_input_render(",
        "esp_err_t rehab_task_decision_formal_input_render(",
    )
    copied = render.index("memcpy(output, package->input, input_length + 1U)")
    success = render.index('"FORMAL_INPUT_GET request_id=%"')
    save = render.index(
        "rehab_task_decision_formal_input_artifact_save_latest("
    )
    failure = render.index('"FORMAL_INPUT_ARTIFACT_SAVE_FAILED err=%s"')
    decision_continues = render.rindex("return ESP_OK;")
    assert copied < success < save < failure < decision_continues
    assert "output, input_length" in render[save:failure]


def test_overwrite_transaction_retains_only_latest_artifact() -> None:
    assert '"rehab/latest_formal_decision_input.json.next"' in ARTIFACT
    assert '"rehab/latest_formal_decision_input.json.rollback"' in ARTIFACT
    assert "rename(final_path, rollback_path)" in ARTIFACT
    assert "rename(next_path, final_path)" in ARTIFACT
    assert "rename(rollback_path, final_path)" in ARTIFACT
    assert "input_001" not in ARTIFACT
    assert "input_002" not in ARTIFACT


def test_artifact_writer_has_no_secret_or_network_inputs() -> None:
    for forbidden in {
        "api_key",
        "authorization",
        "bearer",
        "network_config",
        "wifi_password",
    }:
        assert forbidden not in ARTIFACT.lower()
