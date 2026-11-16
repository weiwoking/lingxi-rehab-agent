from pathlib import Path
import re


APP = Path(__file__).resolve().parents[3]
UI = APP / "fatfs_image" / "system" / "ui" / "rehab"
BRIDGE = (
    APP / "components" / "rehab_ui" / "src"
    / "rehab_ui_task_decision_lua.c"
).read_text(encoding="utf-8")
CONTROLLER = (UI / "esp_claw_rehab_ui.lua").read_text(encoding="utf-8")
STATE = (UI / "rehab_ui_state.lua").read_text(encoding="utf-8")
HOME = (UI / "page_home.lua").read_text(encoding="utf-8")
RESULT = (UI / "page_task_decision_result.lua").read_text(encoding="utf-8")
VIEWER = (UI / "page_task_decision_reasoning.lua").read_text(
    encoding="utf-8"
)


def test_reader_uses_fixed_data_root_schema_and_bounded_file() -> None:
    assert "CLAW_PATH_DATA, REASONING_ARTIFACT_SUBPATH" in BRIDGE
    assert '"rehab/latest_decision_reasoning.json"' in BRIDGE
    assert '"rehab_decision_reasoning.v1"' in BRIDGE
    assert "REASONING_ARTIFACT_MAX_BYTES (128U * 1024U)" in BRIDGE
    assert 'bytes->valuedouble == (double)strlen(content->valuestring)' in BRIDGE
    assert '{"reasoning_available", lua_task_decision_reasoning_available}' in BRIDGE
    assert '{"reasoning", lua_task_decision_reasoning}' in BRIDGE


def test_home_and_success_result_keep_details_and_add_reasoning_entry() -> None:
    assert 'text = "决策详情"' in HOME
    assert 'text = "查看AI分析"' in HOME
    assert 'app.open_task_decision_reasoning("home")' in HOME
    assert 'text = "查看详情"' in RESULT
    assert 'text = "查看AI分析"' in RESULT
    assert 'app.open_task_decision_reasoning("result")' in RESULT
    assert "data.selected and" in RESULT
    assert 'data.decision_id ~= ""' in RESULT
    assert "reasoning_available() == true" in HOME
    assert "reasoning_available(data.decision_id) == true" in RESULT


def test_route_state_preserves_reasoning_page_and_source_return() -> None:
    assert 'task_decision_reasoning = task_decision_reasoning_page' in CONTROLLER
    assert 'app.navigate("task_decision_reasoning")' in CONTROLLER
    assert 'app.reasoning_source = source == "result"' in CONTROLLER
    assert "app.reasoning_expected_decision_id = expected_decision_id" in CONTROLLER
    assert 'and "task_decision_result" or "home"' in CONTROLLER
    assert 'task_decision_reasoning = true' in STATE
    assert 'state.page == "task_decision_reasoning"' in STATE
    assert 'return "task_decision_reasoning"' in STATE


def test_missing_file_is_blocked_and_viewer_has_safe_fallback() -> None:
    assert (
        "task_decision.reasoning_available(expected_decision_id) ~= true"
        in CONTROLLER
    )
    assert 'source == "result" and expected_decision_id == ""' in CONTROLLER
    assert 'app.state.home_hint = "暂无AI分析记录"' in CONTROLLER
    assert '"暂无AI分析记录"' in VIEWER
    assert 'data.available == true' in VIEWER
    assert 'strcmp(decision_id->valuestring, expected_decision_id)' in BRIDGE


def test_viewer_shows_real_content_with_bounded_utf8_pages_and_scroll() -> None:
    assert 'label(root, "AI决策分析"' in VIEWER
    assert '"任务：" .. task_name' in VIEWER
    assert '"决策耗时：%.0fs"' in VIEWER
    assert '"模型：DeepSeek"' in VIEWER
    assert "task_decision.reasoning(app.reasoning_expected_decision_id)" in VIEWER
    assert "view.pages = build_pages(data.reasoning_content)" in VIEWER
    assert 'text = text' in VIEWER
    assert 'set_scroll({ dir = "ver", scrollbar = "auto" })' in VIEWER
    assert "continuation_byte(string.byte(text, finish_index + 1))" in VIEWER
    assert "start_index = finish_index + 1" in VIEWER
    page_bytes = int(re.search(r"local PAGE_BYTES = (\d+)", VIEWER).group(1))
    assert 256 <= page_bytes <= 1024
    for forbidden in ("sanitize", "summary", "summarize", "二次生成"):
        assert forbidden not in VIEWER


def test_viewer_keeps_navigation_and_adds_touch_page_selector() -> None:
    assert 'text = "上一页"' in VIEWER
    assert 'text = "下一页"' in VIEWER
    assert 'text = "返回"' in VIEWER
    assert 'text = "跳转"' in VIEWER
    assert '"第 %d / %d 页"' in VIEWER
    assert "local SELECTOR_PAGE_COUNT = 15" in VIEWER
    assert "local SELECTOR_COLUMNS = 5" in VIEWER
    assert '"跳转到第几页"' in VIEWER
    assert 'text = "上一组"' in VIEWER
    assert 'text = "下一组"' in VIEWER
    assert 'text = "取消"' in VIEWER
    assert "view.page_index = page_number" in VIEWER
    assert "math.floor((view.page_index - 1) / SELECTOR_PAGE_COUNT)" in VIEWER
    assert "view.selector_range + SELECTOR_PAGE_COUNT <= #view.pages" in VIEWER


def test_page_selector_group_model_reaches_every_page_directly() -> None:
    selector_page_count = int(re.search(
        r"local SELECTOR_PAGE_COUNT = (\d+)", VIEWER
    ).group(1))
    assert selector_page_count == 15
    for total_pages in (1, 10, 15, 16, 20, 31, 47):
        offered: list[int] = []
        first = 1
        while first <= total_pages:
            last = min(total_pages, first + selector_page_count - 1)
            offered.extend(range(first, last + 1))
            first += selector_page_count
        assert offered == list(range(1, total_pages + 1))


def test_schema_projection_excludes_sensitive_source_material() -> None:
    start = BRIDGE.index("static int lua_task_decision_reasoning(lua_State *L)")
    end = BRIDGE.index("static void push_status", start)
    projection = BRIDGE[start:end]
    for forbidden in (
        '"prompt"', '"system_prompt"', '"tools"',
        '"tool_arguments"', '"raw_message_json"', '"session_history"',
        '"patient_raw_text"', '"scan_raw_data"',
    ):
        assert forbidden not in projection


def test_long_text_window_model_reconstructs_original_bytes() -> None:
    payload = ("分析步骤：杯子目标。\n" * 900).encode("utf-8")
    page_bytes = int(re.search(r"local PAGE_BYTES = (\d+)", VIEWER).group(1))
    pages: list[bytes] = []
    start = 0
    while start < len(payload):
        finish = min(len(payload), start + page_bytes)
        while finish < len(payload) and 0x80 <= payload[finish] < 0xC0:
            finish -= 1
        assert finish > start
        pages.append(payload[start:finish])
        start = finish
    assert len(payload) > 10 * 1024
    assert len(pages) > 10
    assert all(len(page) <= 1024 for page in pages)
    assert b"".join(pages) == payload
    assert all(page.decode("utf-8") for page in pages)
