from __future__ import annotations

import pathlib
import os
import shutil
import subprocess
import tempfile


REPO = pathlib.Path(__file__).resolve().parents[5]
APP = REPO / "application" / "edge_agent"
COMPONENT = APP / "components" / "rehab_task_decision"
CORE = REPO / "components" / "claw_modules" / "claw_core"
MANAGER = REPO / "components" / "claw_modules" / "claw_manager"
IDF_CJSON = pathlib.Path(
    r"C:\esp\v5.5.4\frameworks\esp-idf-v5.5.4\components\json\cJSON"
)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_exact_final_request_serialization_host() -> None:
    gcc = shutil.which("gcc")
    assert gcc, "host gcc is required"
    assert (IDF_CJSON / "cJSON.c").is_file(), "IDF cJSON source is required"
    serializer = (
        CORE
        / "src"
        / "llm"
        / "backends"
        / "claw_llm_openai_compatible_request.c"
    )
    host_test = COMPONENT / "test" / "test_reasoning_request_serializer_host.c"
    stubs = COMPONENT / "test" / "reasoning_serializer_host_stubs"
    with tempfile.TemporaryDirectory(prefix="rehab_reasoning_serializer_") as tmp:
        exe = pathlib.Path(tmp) / "reasoning_serializer.exe"
        compile_env = os.environ.copy()
        compile_env["PATH"] = rf"C:\msys64\ucrt64\bin;{compile_env['PATH']}"
        compile_result = subprocess.run(
            [
                gcc,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                f"-I{stubs}",
                f"-I{CORE / 'src'}",
                f"-I{IDF_CJSON}",
                str(serializer),
                str(IDF_CJSON / "cJSON.c"),
                str(host_test),
                "-o",
                str(exe),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=compile_env,
        )
        assert compile_result.returncode == 0, (
            compile_result.stdout + compile_result.stderr
        )
        run_result = subprocess.run(
            [str(exe)], capture_output=True, text=True, check=False
        )
        assert run_result.returncode == 0, run_result.stderr
        assert "REQUEST_SERIALIZATION_PASS" in run_result.stdout


def test_formal_override_is_request_scoped_and_reused_by_all_iterations() -> None:
    coordinator = read(COMPONENT / "src" / "rehab_task_decision_coordinator.c")
    agent_loop = read(CORE / "src" / "claw_core_agent_loop.c")
    messages = read(CORE / "src" / "claw_core_messages.c")
    manager = read(MANAGER / "src" / "claw_agent_mgr.c")

    assert '.scope = "rehab_formal"' in coordinator
    assert "claw_agent_mgr_submit_root_text_with_llm_options" in coordinator
    assert "&request.view.llm_options" in agent_loop
    assert "while (true)" in agent_loop
    assert "item.view.llm_options = request->llm_options" in messages
    assert "request.llm_options = *llm_options" in manager
    assert "text, session_id, flags, NULL, timeout_ms" in manager


def test_only_verified_user_modes_are_exposed() -> None:
    kconfig = read(COMPONENT / "Kconfig")

    assert "REHAB_DECISION_THINKING_ENABLED_HIGH" in kconfig
    assert "REHAB_DECISION_THINKING_DISABLED" in kconfig
    assert "default REHAB_DECISION_THINKING_ENABLED_HIGH" in kconfig
    assert "MEDIUM" not in kconfig.upper()
    assert "config REHAB_DECISION_THINKING_ENABLED_MAX" not in kconfig


def test_memory_and_generic_requests_keep_legacy_omitted_behavior() -> None:
    memory = read(
        REPO
        / "components"
        / "claw_modules"
        / "claw_memory"
        / "src"
        / "claw_memory_extract.c"
    )
    manager = read(MANAGER / "src" / "claw_agent_mgr.c")
    serializer = read(
        CORE
        / "src"
        / "llm"
        / "backends"
        / "claw_llm_openai_compatible_request.c"
    )

    memory_request = memory[memory.index("claw_llm_runtime_chat(") :]
    memory_request = memory_request[: memory_request.index("&response")]
    assert '.purpose = "memory_extract"' in memory_request
    assert ".llm_options" not in memory_request
    assert "text, session_id, flags, NULL, timeout_ms" in manager
    assert "CLAW_CORE_LLM_THINKING_NOT_SET" in serializer
    assert "CLAW_CORE_LLM_REASONING_EFFORT_NOT_SET" in serializer


def test_business_contract_and_timeout_sources_are_not_reasoning_controls() -> None:
    coordinator = read(COMPONENT / "src" / "rehab_task_decision_coordinator.c")
    protected = [
        COMPONENT / "src" / "rehab_task_decision_preparation.c",
        COMPONENT / "src" / "rehab_task_decision_llm_input_adapter.c",
        COMPONENT / "src" / "rehab_task_decision_validate.c",
        COMPONENT / "src" / "rehab_task_decision_selected_task.c",
    ]

    assert "#define FORMAL_ROOT_COMPLETION_TIMEOUT_MS 100000U" in coordinator
    for path in protected:
        body = read(path)
        assert "reasoning_effort" not in body
        assert '"thinking"' not in body
