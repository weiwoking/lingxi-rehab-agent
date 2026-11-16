from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
ARTIFACT = (
    COMPONENT / "src" / "rehab_task_decision_reasoning_artifact.c"
).read_text(encoding="utf-8")
CAPABILITY = (
    COMPONENT / "src" / "rehab_task_decision_coordinator_capabilities.c"
).read_text(encoding="utf-8")
REPO = COMPONENT.parents[3]
CORE_CONTEXT = (
    REPO / "components" / "claw_modules" / "claw_core" / "src"
    / "claw_core_context.c"
).read_text(encoding="utf-8")
CAP_BRIDGE = (
    REPO / "components" / "claw_modules" / "claw_cap" / "src"
    / "claw_cap.c"
).read_text(encoding="utf-8")


def test_fixed_data_root_artifact_and_exact_schema_fields() -> None:
    assert 'CLAW_PATH_DATA, REASONING_ARTIFACT_SUBPATH' in ARTIFACT
    assert '"rehab/latest_decision_reasoning.json"' in ARTIFACT

    required = {
        "schema_version",
        "decision_id",
        "generation",
        "task_id",
        "task_name",
        "latency_sec",
        "reasoning_tokens",
        "reasoning_bytes",
        "reasoning_content",
        "created_at",
    }
    for field in required:
        assert f'"{field}"' in ARTIFACT
    for forbidden in {
        "prompt",
        "system_prompt",
        "tool_arguments",
        "raw_message_json",
        "session_history",
        "patient_raw_text",
        "scan_raw_data",
    }:
        assert f'"{forbidden}"' not in ARTIFACT


def test_save_is_after_success_and_is_non_fatal() -> None:
    commit = CAPABILITY[
        CAPABILITY.index("static esp_err_t commit_execute") :
        CAPABILITY.index("static claw_cap_descriptor_t s_descriptors")
    ]
    formal_commit = commit.index("rehab_task_decision_formal_commit")
    status = commit.index("rehab_task_decision_coordinator_get_status")
    finish = commit.index('finish(status_json(&status, true, "selected")')
    save = commit.index(
        "rehab_task_decision_reasoning_artifact_save_latest"
    )
    assert formal_commit < status < finish < save
    assert "if (artifact_err != ESP_OK)" in commit
    assert 'ESP_LOGW(TAG, "reasoning artifact save failed: %s"' in commit
    assert commit.rstrip().endswith("return ESP_OK;\n}")


def test_failed_or_empty_reasoning_cannot_replace_previous_artifact() -> None:
    validate = ARTIFACT.index("if (status == NULL")
    serialize = ARTIFACT.index("cJSON_PrintUnformatted")
    transaction = ARTIFACT.index("write_latest(json")
    assert validate < serialize < transaction
    assert "reasoning_content[0] == '\\0'" in ARTIFACT
    assert "rename(final_path, rollback_path)" in ARTIFACT
    assert "rename(rollback_path, final_path)" in ARTIFACT


def test_reasoning_is_borrowed_from_current_response_only() -> None:
    assert (
        "tool_request.llm_reasoning_content = response->reasoning_content;"
        in CORE_CONTEXT
    )
    assert "&tool_request," in CORE_CONTEXT
    assert (
        "ctx.llm_reasoning_content = request->llm_reasoning_content;"
        in CAP_BRIDGE
    )
    assert "ctx->llm_reasoning_content" in CAPABILITY
