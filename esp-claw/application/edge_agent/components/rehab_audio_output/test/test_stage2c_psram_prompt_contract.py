from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


COMPONENT = Path(__file__).resolve().parents[1]
SRC = COMPONENT / "src"


PROMPTS = (
    "person_follow_started.mp3",
    "person_follow_arrived.mp3",
    "env_scan_started.mp3",
    "env_scan_completed.mp3",
    "decision_started.mp3",
    "training_ot_cup_transfer_l1.mp3",
    "training_ot_cup_drink_l2.mp3",
    "training_ot_cup_drink_l3.mp3",
    "training_ot_ball_transfer_l1.mp3",
    "training_ot_ball_transfer_l2.mp3",
    "training_generic.mp3",
    "training_completed.mp3",
)


def read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_all_fixed_prompts_are_preloaded_to_psram_without_runtime_file_uri():
    store = read("rehab_audio_prompt_store.c")
    service = read("rehab_audio_output_service.c")
    private = read("rehab_audio_output_priv.h")
    assert "REHAB_AUDIO_PROMPT_COUNT     12U" in read(
        "rehab_audio_prompt_store.h"
    )
    for filename in PROMPTS:
        assert store.count(f'"{filename}"') == 1
    assert store.count('"audio/mpeg"') == 12
    assert "MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT" in store
    assert "BUSINESS_PROMPT_PRELOAD_SUMMARY" in store
    assert "file://sdcard/rehab/audio" not in service
    assert "file://sdcard/rehab/audio" not in private
    assert "rehab_audio_player_adapter_play_memory" in service
    assert "source=memory/raw" in service


def test_preload_failure_is_nonfatal_and_has_no_runtime_sd_fallback():
    store = read("rehab_audio_prompt_store.c")
    service = read("rehab_audio_output_service.c")
    assert "BUSINESS_PROMPT_PRELOAD_FAILED" in store
    assert "entry->available = true" in store
    assert "rehab_audio_prompt_store_preload(&handle->prompt_store);" in service
    create_tail = service.split(
        "rehab_audio_prompt_store_preload(&handle->prompt_store);", 1
    )[1]
    assert "rehab_audio_player_adapter_create" in create_tail
    prompt_branch = service.split(
        "command->type == REHAB_AUDIO_COMMAND_PROMPT", 1
    )[1].split("} else {", 1)[0]
    assert "prompt_entry->available" in prompt_branch
    assert "fopen" not in prompt_branch
    assert "file://" not in prompt_branch


def test_submit_mutex_never_covers_player_or_tts_lifecycle():
    service = read("rehab_audio_output_service.c")
    worker = service.split("static void audio_worker", 1)[1].split(
        "esp_err_t rehab_audio_output_create", 1
    )[0]
    lifecycle = (
        "rehab_audio_player_adapter_stop",
        "rehab_audio_tts_synthesize",
        "rehab_audio_player_adapter_play(",
        "rehab_audio_player_adapter_play_memory",
    )
    active_command = worker.index(
        "rehab_audio_command_t *command = &handle->active_command;"
    )
    submit_scope = worker[:active_command]
    lifecycle_scope = worker[active_command:]
    assert "xSemaphoreTake(handle->submit_mutex" in submit_scope
    assert "xSemaphoreGive(handle->submit_mutex" in submit_scope
    assert "submit_mutex" not in lifecycle_scope
    for token in lifecycle:
        assert token in lifecycle_scope
    stop = service.split("esp_err_t rehab_audio_output_stop", 1)[1].split(
        "esp_err_t rehab_audio_output_get_status", 1
    )[0]
    assert "submit_command(" in stop
    assert "rehab_audio_player_adapter_stop" not in stop


def test_text_tts_latest_wins_and_audio_stop_remain_in_single_worker():
    private = read("rehab_audio_output_priv.h")
    service = read("rehab_audio_output_service.c")
    assert "REHAB_AUDIO_COMMAND_TEXT" in private
    assert "REHAB_AUDIO_COMMAND_PROMPT" in private
    assert "REHAB_AUDIO_COMMAND_STOP" in private
    assert "rehab_audio_tts_synthesize" in service
    assert "xQueueCreate(1, sizeof(uint32_t))" in service
    assert "xQueueOverwrite" in service
    assert "generation_is_current_locked" in service
    assert service.count('xTaskCreate(audio_worker, "rehab_audio"') == 1


def test_memory_cursor_host_contract():
    compiler = shutil.which("gcc")
    if compiler is None:
        candidate = Path(r"C:\msys64\ucrt64\bin\gcc.exe")
        compiler = str(candidate) if candidate.exists() else None
    if compiler is None:
        pytest.skip("host gcc is unavailable")
    with tempfile.TemporaryDirectory() as temporary:
        executable = Path(temporary) / "memory_source_host.exe"
        env = os.environ.copy()
        env["PATH"] = str(Path(compiler).parent) + os.pathsep + env["PATH"]
        completed = subprocess.run(
            [
                compiler,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(SRC),
                str(SRC / "rehab_audio_memory_source.c"),
                str(COMPONENT / "test" / "rehab_audio_memory_source_host.c"),
                "-o",
                str(executable),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        assert completed.returncode == 0, completed.stderr
        run = subprocess.run(
            [str(executable)], check=False, capture_output=True, text=True
        )
        assert run.returncode == 0, run.stderr
