from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
REPO = COMPONENT.parents[3]


def scrub_c_literals_and_comments(source: str) -> str:
    pattern = re.compile(
        r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
        re.S,
    )
    return pattern.sub(lambda match: " " * len(match.group(0)), source)


def extract_c_functions(source: str):
    scrubbed = scrub_c_literals_and_comments(source)
    definitions = re.compile(
        r"(?m)^[ \t]*(?P<signature>"
        r"(?:static[ \t]+)?[A-Za-z_][A-Za-z0-9_ \t*]*?"
        r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*"
        r"\([^;{}]*\))[ \t\r\n]*\{"
    )
    functions = []
    for match in definitions.finditer(scrubbed):
        open_brace = scrubbed.find("{", match.start(), match.end())
        depth = 0
        end = None
        for index in range(open_brace, len(scrubbed)):
            if scrubbed[index] == "{":
                depth += 1
            elif scrubbed[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        assert end is not None, match.group("name")
        functions.append(
            {
                "name": match.group("name"),
                "signature": source[match.start("signature") : open_brace],
                "body": source[open_brace:end],
                "scrubbed_body": scrubbed[open_brace:end],
            }
        )
    return functions


def macro_arguments(source: str, macro_name: str):
    scrubbed = scrub_c_literals_and_comments(source)
    results = []
    for match in re.finditer(rf"\b{re.escape(macro_name)}\s*\(", scrubbed):
        open_paren = scrubbed.find("(", match.start(), match.end())
        depth = 0
        commas = []
        close_paren = None
        for index in range(open_paren, len(scrubbed)):
            character = scrubbed[index]
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    close_paren = index
                    break
            elif character == "," and depth == 1:
                commas.append(index)
        assert close_paren is not None, macro_name
        boundaries = [open_paren] + commas + [close_paren]
        results.append(
            [
                source[boundaries[index] + 1 : boundaries[index + 1]].strip()
                for index in range(len(boundaries) - 1)
            ]
        )
    return results


def match_message(text: str):
    prefix = "最大音量播报："
    if text.startswith(prefix):
        return "speak", text[len(prefix) :]
    if text == "停止播报":
        return "stop", ""
    return None


@dataclass
class Model:
    generation: int = 0
    desired: bool = False
    state: str = "IDLE"
    last_error: str = ""

    def speak(self):
        self.generation += 1
        self.desired = True
        self.state = "REQUESTING_TTS"
        self.last_error = ""
        return self.generation

    def stop(self):
        self.generation += 1
        self.desired = False
        self.state = "IDLE"
        return self.generation

    def tts_ready(self, generation: int, valid=True):
        if not self.desired or generation != self.generation:
            return False
        self.state = "OPENING_STREAM" if valid else "IDLE"
        if not valid:
            self.desired = False
            self.last_error = "tts_service_unavailable"
        return valid

    def player_event(self, generation: int, event: str):
        if not self.desired or generation != self.generation:
            return False
        if event == "started":
            self.state = "PLAYING"
        elif event == "error":
            self.state = "ERROR"
            self.desired = False
            self.last_error = "player_error"
        else:
            self.state = "IDLE"
            self.desired = False
        return True


def test_wechat_matching_and_exact_remainder():
    assert match_message("普通微信消息") is None
    assert match_message("最大音量播报：你好") == ("speak", "你好")
    assert match_message("最大音量播报：  你好") == ("speak", "  你好")
    assert match_message("最大音量播报：") == ("speak", "")
    assert match_message("停止播报") == ("stop", "")
    assert match_message("停止播报一下") is None
    router = (
        REPO
        / "components"
        / "claw_modules"
        / "claw_event_router"
        / "src"
        / "claw_event_router.c"
    ).read_text(encoding="utf-8")
    assert "prefix_has_explicit_delimiter" in router
    assert "fullwidth_colon" in router


def test_latest_wins_and_late_callbacks_are_ignored():
    model = Model()
    old = model.speak()
    new = model.speak()
    assert old != new
    assert model.tts_ready(old) is False
    assert model.tts_ready(new) is True
    assert model.player_event(new, "started") is True
    assert model.player_event(old, "finished") is False
    assert model.state == "PLAYING"


def test_stop_invalidates_old_generation():
    model = Model()
    old = model.speak()
    stopped = model.stop()
    assert stopped > old
    assert model.tts_ready(old) is False
    assert model.player_event(old, "started") is False
    assert model.state == "IDLE"


def test_tts_failure_returns_idle_but_player_error_retains_error_state():
    model = Model()
    generation = model.speak()
    assert model.tts_ready(generation, valid=False) is False
    assert model.state == "IDLE"
    assert model.desired is False
    assert model.last_error == "tts_service_unavailable"

    next_generation = model.speak()
    assert model.last_error == ""
    assert model.tts_ready(next_generation) is True
    assert model.player_event(next_generation, "error") is True
    assert model.state == "ERROR"
    assert model.desired is False
    assert model.last_error == "player_error"

    model.stop()
    assert model.state == "IDLE"
    assert model.last_error == "player_error"


def test_router_provision_is_update_first_without_get_or_large_stack_buffer():
    source = (
        COMPONENT / "src" / "rehab_audio_capabilities.c"
    ).read_text(encoding="utf-8")
    functions = {
        function["name"]: function for function in extract_c_functions(source)
    }
    provision = functions["provision_rule"]
    body = provision["scrubbed_body"]

    assert "claw_event_router_get_rule_json" not in source
    assert not re.search(r"\bchar\s+existing\s*\[", body)
    assert re.findall(
        r"\bclaw_event_router_(update|add)_rule_json\s*\(",
        body,
    ) == ["update", "add"]
    assert re.search(
        r"esp_err_t\s+ret\s*=\s*"
        r"claw_event_router_update_rule_json\s*\(\s*rule_json\s*\)\s*;",
        body,
    )
    not_found = re.search(
        r"if\s*\(\s*ret\s*==\s*ESP_ERR_NOT_FOUND\s*\)"
        r"\s*\{\s*ret\s*=\s*claw_event_router_add_rule_json"
        r"\s*\(\s*rule_json\s*\)\s*;\s*\}",
        body,
        re.S,
    )
    assert not_found
    assert re.search(r"\breturn\s+ret\s*;", body)


def test_router_provision_error_flow_and_idempotent_merge_model():
    source = (
        COMPONENT / "src" / "rehab_audio_capabilities.c"
    ).read_text(encoding="utf-8")
    ok = "ESP_OK"
    not_found = "ESP_ERR_NOT_FOUND"
    other_error = "ESP_ERR_INVALID_RESPONSE"

    def provision_result(update_result, add_result=ok):
        calls = ["update"]
        result = update_result
        if result == not_found:
            calls.append("add")
            result = add_result
        return result, calls

    assert provision_result(ok) == (ok, ["update"])
    assert provision_result(other_error) == (other_error, ["update"])
    assert provision_result(not_found) == (ok, ["update", "add"])
    assert provision_result(not_found, other_error) == (
        other_error,
        ["update", "add"],
    )

    def provision_into(rules, rule):
        result, calls = provision_result(
            ok if any(item["id"] == rule["id"] for item in rules)
            else not_found
        )
        if calls == ["update"]:
            index = next(
                i for i, item in enumerate(rules) if item["id"] == rule["id"]
            )
            rules[index] = rule
        else:
            rules.insert(0, rule)
        return result, calls

    assert "p4_rehab_audio_speak_v1" in source
    assert "p4_rehab_audio_stop_v1" in source
    assert "{{match.remainder}}" in source
    assert "{{event.message_id}}" in source

    existing = [
        {"id": "unrelated-a", "value": 1},
        {"id": "p4_rehab_audio_speak_v1", "old": True},
        {"id": "unrelated-b", "value": 2},
    ]
    additions = [
        {"id": "p4_rehab_audio_speak_v1", "new": True},
        {"id": "p4_rehab_audio_stop_v1", "new": True},
    ]
    merged = list(existing)
    for rule in additions:
        assert provision_into(merged, rule)[0] == ok
    for rule in additions:
        result, calls = provision_into(merged, rule)
        assert result == ok
        assert calls == ["update"]
    assert [item for item in merged if item["id"] == "unrelated-a"] == [
        {"id": "unrelated-a", "value": 1}
    ]
    assert [item for item in merged if item["id"] == "unrelated-b"] == [
        {"id": "unrelated-b", "value": 2}
    ]
    assert sum(item["id"] == "p4_rehab_audio_speak_v1" for item in merged) == 1
    assert sum(item["id"] == "p4_rehab_audio_stop_v1" for item in merged) == 1


def test_embedded_router_rules_are_valid_and_frozen():
    source = (
        COMPONENT / "src" / "rehab_audio_capabilities.c"
    ).read_text(encoding="utf-8")

    def extract(name: str, next_marker: str):
        block = re.search(
            rf"static const char {name}\[\] =(?P<body>.*?){next_marker}",
            source,
            re.S,
        )
        assert block
        fragments = re.findall(r'"(?:\\.|[^"\\])*"', block.group("body"))
        return json.loads("".join(json.loads(fragment) for fragment in fragments))

    speak = extract("speak_rule", r"static const char stop_rule")
    stop = extract("stop_rule", r"esp_err_t err = provision_rule")
    assert speak["id"] == "p4_rehab_audio_speak_v1"
    assert speak["consume_on_match"] is True
    assert speak["match"] == {
        "event_type": "message",
        "event_key": "text",
        "source_cap": "wechat_gateway",
        "source_channel": "wechat",
        "content_type": "text",
        "text": "最大音量播报：",
        "text_match_rule": "prefix",
    }
    assert speak["actions"][0]["cap"] == "audio_speak_text"
    assert speak["actions"][0]["input"]["text"] == "{{match.remainder}}"
    assert all(action["type"] != "run_agent" for action in speak["actions"])
    assert stop["id"] == "p4_rehab_audio_stop_v1"
    assert stop["match"]["text"] == "停止播报"
    assert stop["match"]["text_match_rule"] == "exact"
    assert stop["actions"][0]["cap"] == "audio_stop"


def test_player_reuses_formal_stack_and_forbidden_layers_untouched():
    player = (
        COMPONENT / "src" / "rehab_audio_player_adapter.c"
    ).read_text(encoding="utf-8")
    cmake = (COMPONENT / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "esp_audio_simple_player_run" in player
    assert "esp_asrc_process" in player
    assert "ESP_BOARD_DEVICE_NAME_AUDIO_DAC" in player
    assert "esp_codec_dev_set_out_vol" in player
    assert "esp_audio_simple_player" in cmake
    assert "esp_asrc" in cmake

    changed_paths = {
        "boards",
        "partitions",
        "sdkconfig",
        "rehab_sensor_recording",
        "rehab_score_bridge",
        "rehab_person_follow",
    }
    component_paths = {part.lower() for path in COMPONENT.rglob("*") for part in path.parts}
    assert not changed_paths.intersection(component_paths)


def test_http_client_rejects_wrong_mime_and_foreign_url_by_source_contract():
    source = (
        COMPONENT / "src" / "rehab_audio_tts_client.c"
    ).read_text(encoding="utf-8")
    assert 'strcmp(mime_value, "audio/mpeg") == 0' in source
    assert "result_url_is_allowed" in source
    assert '"/api/v1/tts/audio/"' in source
    assert "ESP_ERR_INVALID_RESPONSE" in source


def test_snprintf_has_required_standard_declaration_and_no_extra_include():
    source = (
        COMPONENT / "src" / "rehab_audio_tts_client.c"
    ).read_text(encoding="utf-8")
    calls = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", source))
    system_headers = set(re.findall(r'^\s*#include\s*<([^>]+)>', source, re.M))
    assert "snprintf" in calls
    assert "stdio.h" in system_headers
    assert system_headers == {"stdio.h", "stdlib.h", "string.h"}


def test_tts_synthesize_uses_single_ret_for_esp_goto_contract():
    source = (
        COMPONENT / "src" / "rehab_audio_tts_client.c"
    ).read_text(encoding="utf-8")
    functions = {
        function["name"]: function for function in extract_c_functions(source)
    }
    function = functions["rehab_audio_tts_synthesize"]
    body = function["scrubbed_body"]

    assert body.count("ESP_GOTO_ON_ERROR") == 2
    assert re.search(r"\besp_err_t\s+ret\s*=\s*ESP_OK\s*;", body)
    assert not re.search(r"\besp_err_t\s+err\s*=", body)
    assert re.search(
        r"\bret\s*=\s*esp_http_client_perform\s*\(\s*client\s*\)\s*;",
        body,
    )
    assert re.search(r"\bif\s*\(\s*ret\s*!=\s*ESP_OK\s*\)", body)
    assert re.search(
        r"\bret\s*=\s*status\s*>=\s*500\s*\?"
        r"\s*ESP_ERR_NOT_FINISHED\s*:\s*ESP_ERR_INVALID_RESPONSE\s*;",
        body,
    )
    assert re.search(
        r"\bret\s*=\s*parse_response\s*"
        r"\(\s*base_url\s*,\s*response\.data\s*,\s*out_result\s*\)\s*;",
        body,
    )
    assert re.search(r"\bcleanup\s*:\s*.*\breturn\s+ret\s*;", body, re.S)


def test_all_esp_check_macros_meet_local_function_contracts():
    macro_names = (
        "ESP_GOTO_ON_ERROR",
        "ESP_GOTO_ON_FALSE",
        "ESP_RETURN_ON_ERROR",
        "ESP_RETURN_ON_FALSE",
    )
    goto_argument = {
        "ESP_GOTO_ON_ERROR": 1,
        "ESP_GOTO_ON_FALSE": 2,
    }
    seen = {name: 0 for name in macro_names}

    for path in sorted((COMPONENT / "src").glob("*.[ch]")):
        source = path.read_text(encoding="utf-8")
        for function in extract_c_functions(source):
            body = function["body"]
            scrubbed_body = function["scrubbed_body"]
            for macro_name in macro_names:
                invocations = macro_arguments(body, macro_name)
                seen[macro_name] += len(invocations)
                if not invocations:
                    continue
                if macro_name.startswith("ESP_GOTO_"):
                    assert re.search(
                        r"\besp_err_t\s+ret\s*(?:=\s*[^;]+)?\s*;",
                        scrubbed_body,
                    ), (path, function["name"], macro_name)
                    assert re.search(r"\breturn\s+ret\s*;", scrubbed_body), (
                        path,
                        function["name"],
                        macro_name,
                    )
                    for arguments in invocations:
                        label = arguments[goto_argument[macro_name]]
                        assert re.fullmatch(
                            r"[A-Za-z_][A-Za-z0-9_]*", label
                        ), (path, function["name"], macro_name, label)
                        assert re.search(
                            rf"(?m)^[ \t]*{re.escape(label)}[ \t]*:",
                            scrubbed_body,
                        ), (path, function["name"], macro_name, label)
                else:
                    assert function["signature"].lstrip().startswith(
                        ("esp_err_t ", "static esp_err_t ")
                    ), (path, function["name"], macro_name)

    assert seen == {
        "ESP_GOTO_ON_ERROR": 2,
        "ESP_GOTO_ON_FALSE": 0,
        "ESP_RETURN_ON_ERROR": 1,
        "ESP_RETURN_ON_FALSE": 11,
    }


def test_latest_wins_and_error_documentation_matches_synchronous_worker():
    documents = [
        (REPO / "pc" / "rehab_tts_service" / "README.md").read_text(
            encoding="utf-8"
        ),
        (
            REPO
            / "tools"
            / "P4_Rehab_TTS_Launcher"
            / "README.md"
        ).read_text(encoding="utf-8"),
    ]
    for document in documents:
        normalized = " ".join(document.split())
        assert "immediately invalidates" in normalized
        assert (
            "synchronous TTS/HTTP" in normalized
            or "synchronous HTTP" in normalized
        )
        assert "returns or times out" in normalized
        assert "never played" in normalized
        assert "stream-open failures" in normalized
        assert "player runtime error enters `ERROR`" in normalized
        assert (
            "retaining `last_error`" in normalized
            or "retains `last_error`" in normalized
        )
        assert "stop" in normalized.lower() and "`IDLE`" in normalized


def test_error_state_source_contract_distinguishes_failure_paths():
    source = (
        COMPONENT / "src" / "rehab_audio_output_service.c"
    ).read_text(encoding="utf-8")
    assert re.search(
        r"if \(err != ESP_OK\).*?"
        r"handle->state = REHAB_AUDIO_OUTPUT_IDLE;.*?"
        r'"tts_service_unavailable"',
        source,
        re.S,
    )
    assert re.search(
        r"rehab_audio_player_adapter_play\(.*?"
        r"if \(err != ESP_OK && current\).*?"
        r"handle->state = REHAB_AUDIO_OUTPUT_IDLE;.*?"
        r'"stream_open_failed"',
        source,
        re.S,
    )
    assert re.search(
        r"case REHAB_AUDIO_PLAYER_ERROR:.*?"
        r"handle->state = REHAB_AUDIO_OUTPUT_ERROR;.*?"
        r'"player_error"',
        source,
        re.S,
    )
    speak_body = source.split("esp_err_t rehab_audio_output_speak_text", 1)[1].split(
        "esp_err_t rehab_audio_output_stop", 1
    )[0]
    submit_body = source.split("static esp_err_t submit_command", 1)[1].split(
        "static void player_event", 1
    )[0]
    worker_body = source.split("static void audio_worker", 1)[1].split(
        "esp_err_t rehab_audio_output_create", 1
    )[0]
    stop_body = source.split("esp_err_t rehab_audio_output_stop", 1)[1].split(
        "esp_err_t rehab_audio_output_get_status", 1
    )[0]
    assert "submit_command(" in speak_body
    assert "handle->last_error = ESP_OK;" in submit_body
    assert "handle->last_error_reason[0] = '\\0';" in submit_body
    assert "REHAB_AUDIO_COMMAND_STOP" in stop_body
    assert "handle->state = REHAB_AUDIO_OUTPUT_IDLE;" in worker_body
    assert "handle->last_error = ESP_OK;" not in stop_body


def test_unicode_limit_is_codepoint_based_not_byte_based():
    source = (
        COMPONENT / "src" / "rehab_audio_output_service.c"
    ).read_text(encoding="utf-8")
    assert "count_utf8_codepoints" in source
    assert "codepoints <= REHAB_AUDIO_OUTPUT_MAX_CODEPOINTS" in source
    assert "strlen(text)" in source


def test_command_payload_is_component_owned_and_prompt_submit_is_stack_light():
    private_header = (
        COMPONENT / "src" / "rehab_audio_output_priv.h"
    ).read_text(encoding="utf-8")
    source = (
        COMPONENT / "src" / "rehab_audio_output_service.c"
    ).read_text(encoding="utf-8")
    functions = {
        function["name"]: function for function in extract_c_functions(source)
    }

    assert "const char *text;" in private_header
    assert "rehab_audio_output_prompt_t prompt;" in private_header
    assert "pending_command" in private_header
    assert "active_command" in private_header
    assert "pending_text[REHAB_AUDIO_OUTPUT_MAX_TEXT_BYTES + 1U]" in private_header
    assert "active_text[REHAB_AUDIO_OUTPUT_MAX_TEXT_BYTES + 1U]" in private_header
    assert "xQueueCreate(1, sizeof(uint32_t))" in source
    assert "rehab_audio_command_t command" not in functions[
        "rehab_audio_output_play_prompt"
    ]["body"]
    assert "rehab_audio_command_t command" not in functions[
        "rehab_audio_output_speak_text"
    ]["body"]
    assert "rehab_audio_player_adapter_stop" not in functions[
        "rehab_audio_output_speak_text"
    ]["body"]
    assert "rehab_audio_command_t command" not in functions["audio_worker"]["body"]
    assert "rehab_audio_player_adapter_stop" in functions["audio_worker"]["body"]
    assert "rehab_audio_tts_synthesize" in functions["audio_worker"]["body"]
    assert "rehab_audio_player_adapter_play" in functions["audio_worker"]["body"]


def test_stage2c_fixed_prompts_use_preloaded_memory_not_runtime_sd_uri():
    main_source = (
        REPO / "application" / "edge_agent" / "main" / "main.c"
    ).read_text(encoding="utf-8")
    service = (
        COMPONENT / "src" / "rehab_audio_output_service.c"
    ).read_text(encoding="utf-8")
    prompt_store = (
        COMPONENT / "src" / "rehab_audio_prompt_store.c"
    ).read_text(encoding="utf-8")

    assert "rehab_audio_player_adapter_play_memory" in service
    assert "source=memory/raw" in service
    assert "file://sdcard/rehab/audio/" not in service
    for filename in (
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
    ):
        assert f'"{filename}"' in prompt_store
    assert "TEMPORARY STAGE 2A LOCAL AUDIO TEST" not in main_source
    assert "stage2a_local_audio_test_task" not in main_source
    assert "audio_local_test.mp3" not in service
