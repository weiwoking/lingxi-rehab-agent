-- Host-only state transition tests; this file is not copied into SYSTEM.
local calls = {}
local replies = {}
local local_scan_calls = {}
local local_scan_replies = {}
local local_decision_calls = {}
local local_decision_replies = {}

package.preload.capability = function()
  return {
    call = function(name, args, opts)
      calls[#calls + 1] = { name = name, args = args, opts = opts }
      local reply = table.remove(replies, 1)
      assert(reply, "missing mock reply for " .. tostring(name))
      return reply.ok, reply.out, reply.err
    end,
  }
end

package.preload.json = function()
  return { decode = function(value) return value end }
end

package.preload.rehab_environment_scan_ui = function()
  local function invoke(name)
    local_scan_calls[#local_scan_calls + 1] = name
    local reply = table.remove(local_scan_replies, 1)
    assert(reply, "missing local scan reply for " .. name)
    return reply
  end
  return {
    start = function() return invoke("start") end,
    status = function() return invoke("status") end,
    cancel = function() return invoke("cancel") end,
  }
end

package.preload.rehab_task_decision_ui = function()
  local function invoke(name)
    local_decision_calls[#local_decision_calls + 1] = name
    local reply = table.remove(local_decision_replies, 1)
    if reply then return reply end
    return {
      ok = true, state = "IDLE", active = false, generation = 0,
      decision_id = "", task_id = "", task_name = "", reason = "",
      score = 0, target_available = false, target_bbox_xyxy = {},
      last_error = "none",
    }
  end
  return {
    start = function() return invoke("start") end,
    status = function() return invoke("status") end,
    scene_status = function() return invoke("scene_status") end,
    target_image = function() return nil, "not_used" end,
  }
end

local ui_dir = (arg and arg[1] and arg[1] ~= "") and arg[1] or "."
package.path = ui_dir .. "/?.lua;" .. package.path
local state_module = require("rehab_ui_state")

local function expect(value, message)
  if not value then error(message or "expectation failed", 2) end
end

local function reply(ok, out, err)
  replies[#replies + 1] = { ok = ok, out = out, err = err }
end

local function scan_reply(value)
  local_scan_replies[#local_scan_replies + 1] = value
end

local function scan_status(state, overrides)
  local active = state ~= "IDLE" and state ~= "COMPLETED" and
    state ~= "FAILED" and state ~= "CANCELLED"
  local result = {
    ok = true,
    state = state,
    active = active,
    cancel_requested = state == "CANCELLING",
    scan_id = active and "ENV20260724_001" or "",
    direction_index = 0,
    directions_completed = 0,
    directions_total = 11,
    warmup_completed = 0,
    warmup_total = 5,
    upload_state = "IDLE",
    retryable = false,
    last_error = "",
    upload_error = "",
  }
  for key, value in pairs(overrides or {}) do result[key] = value end
  return result
end

local function active_status(session_id, state)
  return {
    state = state or "recording",
    sd_ready = true,
    required_sensors_ok = true,
    current_session = {
      active = true,
      state = state or "recording",
      session_path = "/sdcard/rehab/sessions/" .. session_id,
      task_name = "水杯横向取放训练（一级）",
      rows_written = 42,
      record_queue_drop_count = 0,
      sd_write_error_count = 0,
    },
    last_completed_session = { exists = false },
    node1 = { online = true, packet_updating = true },
    node2 = { online = true, packet_updating = true },
  }
end

local function idle_status(completed_id)
  local completed = { exists = completed_id ~= nil }
  if completed_id then
    completed.session_path = "/sdcard/rehab/sessions/" .. completed_id
    completed.task_name = "水杯横向取放训练（一级）"
  end
  return {
    state = "idle",
    current_session = { active = false },
    last_completed_session = completed,
  }
end

-- 1. No selected task -> Home start disabled.
do
  local state = state_module.new()
  state_module.apply_selected(state, { available = false })
  expect(state_module.route_for_state(state) == "home", "no selected must stay Home")
  expect(not state_module.is_start_enabled(state), "no selected must disable start")
end

-- 2. Selected + idle -> Home start enabled.
do
  local state = state_module.new()
  state_module.apply_selected(state, {
    available = true, task_id = "TASK006", task_name = "水杯横向取放训练（一级）",
    suggested_distance_cm = 73, decision_generation = 9,
  })
  state_module.apply_training_status(state, idle_status())
  expect(state_module.is_start_enabled(state), "selected idle must enable start")
end

-- 3. UI start calls only rehab_training_start_selected and binds its Session.
do
  local state = state_module.new()
  state.selected_available = true
  reply(true, {
    accepted = true, training_started = true, state = "recording",
    task_id = "TASK006", task_name = "水杯横向取放训练（一级）",
    session_id = "20260721_TASK006", rows_written = 0,
  })
  reply(true, active_status("20260721_TASK006"))
  local ok = state_module.start_training(state, 1000)
  expect(ok, "UI start should succeed")
  expect(calls[#calls - 1].name == "rehab_training_start_selected",
    "UI start must use start_selected")
  expect(state.bound_training_session_id == "20260721_TASK006",
    "UI start must bind returned Session")
end

-- 4. WeChat start observed from status automatically routes to Training.
do
  local state = state_module.new()
  state_module.apply_training_status(state, idle_status())
  state_module.apply_training_status(state, active_status("20260721_TASK007"))
  expect(state_module.route_for_state(state) == "training",
    "external start must route Training")
  expect(state.bound_training_session_id == "20260721_TASK007",
    "external start must bind current Session")
end

-- 5. UI stop binds the finalized Session and opens Result.
do
  local state = state_module.new()
  state_module.apply_training_status(state, active_status("20260721_TASK008"))
  reply(true, {
    accepted = true, state = "idle", task_name = "水杯横向取放训练（一级）",
    session_path = "/sdcard/rehab/sessions/20260721_TASK008",
    rows_written = 80, queue_drop_count = 0, write_error_count = 0,
  })
  reply(true, idle_status("20260721_TASK008"))
  local ok = state_module.stop_training(state, 2000)
  expect(ok, "UI stop should succeed")
  expect(state.bound_result_session_id == "20260721_TASK008",
    "UI stop must bind finalized Session")
  expect(state_module.route_for_state(state) == "result",
    "UI stop must route Result")
end

-- 6. WeChat stop is detected by recording -> idle + matching completed Session.
do
  local state = state_module.new()
  state_module.apply_training_status(state, idle_status())
  state_module.apply_training_status(state, active_status("20260721_TASK009"))
  state_module.apply_training_status(state, idle_status("20260721_TASK009"))
  expect(state.bound_result_session_id == "20260721_TASK009",
    "external stop must bind matching completed Session")
end

-- 7. Score pending remains a waiting Result state.
do
  local state = state_module.new()
  state.bound_result_session_id = "20260721_TASK010"
  state_module.apply_score(state, {
    ok = true, session_id = "20260721_TASK010", state = "scoring",
    result_available = false,
  })
  expect(state_module.is_score_pending(state), "scoring must remain pending")
end


-- 8. Score succeeded exposes only the bound Session's real compact result.
do
  local state = state_module.new()
  state.bound_result_session_id = "20260721_TASK011"
  state_module.apply_score(state, {
    ok = true, session_id = "20260721_TASK011", state = "succeeded",
    task_name = "水杯横向取放训练（一级）", result_available = true,
    result = { valid = true, average_score = 88.5, max_score = 92,
      min_score = 84, consistency_score = 91, repetition_count = 4,
      target_repetition_count = 4, data_quality_level = "good",
      warnings = {}, compensation_detected = false,
      compensation_action_count = 0 },
  })
  expect(state.score_state == "succeeded", "score should succeed")
  expect(state.score_result.average_score == 88.5, "real average must be kept")
end

-- 9. Score failed keeps the current Session and the real failure reason.
do
  local state = state_module.new()
  state.bound_result_session_id = "20260721_TASK012"
  state_module.apply_score(state, {
    ok = true, session_id = "20260721_TASK012", state = "failed",
    last_error = "pc_score_failed", result_available = false,
  })
  expect(state.score_state == "failed", "score failure must be visible")
  expect(state.score_error == "pc_score_failed", "failure reason must be preserved")
end

-- 10. Score query always supplies the exact bound Session; never latest.
do
  local state = state_module.new()
  state.bound_result_session_id = "20260721_TASK013"
  reply(true, { ok = true, session_id = "20260721_TASK013",
    state = "pending", result_available = false })
  state_module.refresh_score(state, 3000, true)
  local call = calls[#calls]
  expect(call.name == "rehab_score_query", "must use score_query")
  expect(call.args.session_id == "20260721_TASK013",
    "score_query must include bound Session")
end

-- 11. Duplicate action guard issues no second Capability call.
do
  local state = state_module.new()
  state.action_inflight = true
  local before = #calls
  local ok = state_module.start_training(state, 4000)
  expect(not ok and #calls == before, "duplicate start must not call Capability")
end

-- 12. Local UI calls never invoke Agent/LLM/activate_skill.
do
  for _, call in ipairs(calls) do
    expect(call.name ~= "activate_skill", "activate_skill is forbidden")
    expect(call.name ~= "ask" and call.name ~= "agent_run",
      "LLM/Agent calls are forbidden")
  end
end

-- Environment scan start is a local module call and binds the shared scan ID.
do
  local state = state_module.new()
  state_module.open_environment_scan(state)
  local before_capabilities = #calls
  scan_reply(scan_status("WARMING_CAMERA", {
    scan_id = "ENV20260724_SHARED",
    warmup_completed = 1,
  }))
  local ok = state_module.start_environment_scan(state, 1000)
  expect(ok, "local environment scan start should succeed")
  expect(local_scan_calls[#local_scan_calls] == "start",
    "environment scan start must use the local C module")
  expect(#calls == before_capabilities,
    "environment scan start must not call a Capability")
  expect(state.environment_scan_status.scan_id == "ENV20260724_SHARED",
    "UI must retain the coordinator scan ID")
  expect(state_module.route_for_state(state) == "environment_scan",
    "active environment scan must route to its status page")
end

-- A busy start reuses the coordinator snapshot instead of creating UI state.
do
  local state = state_module.new()
  scan_reply(scan_status("CAPTURING", {
    busy = true,
    scan_id = "ENV20260724_EXISTING",
    direction_index = 3,
    directions_completed = 3,
  }))
  local ok, result = state_module.start_environment_scan(state, 2000)
  expect(ok and result.busy == true,
    "busy coordinator response must be treated as the existing scan")
  expect(state.environment_scan_status.scan_id == "ENV20260724_EXISTING",
    "busy start must expose the existing scan ID")
end

-- Console-started work is represented by the same coordinator snapshot.
do
  local state = state_module.new()
  state_module.apply_environment_scan_status(state,
    scan_status("WRITING", {
      scan_id = "ENV_CONSOLE_SHARED",
      direction_index = 5,
      directions_completed = 5,
    }))
  expect(state_module.route_for_state(state) == "environment_scan",
    "console-started active scan must open the UI status page")
  expect(state.environment_scan_status.scan_id == "ENV_CONSOLE_SHARED",
    "console and UI must share the same scan ID")
end

-- Polling is 1000ms idle and 500ms active, with no Capability traffic.
do
  local state = state_module.new()
  local before_capabilities = #calls
  scan_reply(scan_status("IDLE"))
  state_module.refresh_environment_scan_status(state, 0, true)
  local after_idle = #local_scan_calls
  state_module.refresh_environment_scan_status(state, 999, false)
  expect(#local_scan_calls == after_idle,
    "idle environment status must be throttled for 1000ms")
  scan_reply(scan_status("WARMING_CAMERA"))
  state_module.refresh_environment_scan_status(state, 1000, false)
  local after_active = #local_scan_calls
  state_module.refresh_environment_scan_status(state, 1499, false)
  expect(#local_scan_calls == after_active,
    "active environment status must be throttled for 500ms")
  scan_reply(scan_status("CAPTURING"))
  state_module.refresh_environment_scan_status(state, 1500, false)
  expect(#local_scan_calls == after_active + 1,
    "active environment status must poll at 500ms")
  expect(#calls == before_capabilities,
    "environment polling must not call a Capability")
end

-- Stop invokes only the coordinator cancel API and preserves its readback.
do
  local state = state_module.new()
  state_module.apply_environment_scan_status(state,
    scan_status("CAPTURING", { scan_id = "ENV_CANCEL_SHARED" }))
  local before_capabilities = #calls
  scan_reply(scan_status("CANCELLING", {
    scan_id = "ENV_CANCEL_SHARED",
    cancel_requested = true,
  }))
  local ok = state_module.cancel_environment_scan(state, 3000)
  expect(ok and local_scan_calls[#local_scan_calls] == "cancel",
    "stop must invoke the local coordinator cancel API")
  expect(#calls == before_capabilities,
    "stop must not invoke a Capability")
end

-- Every coordinator state maps to bounded user-facing scan text.
do
  local expected_titles = {
    IDLE = "准备中",
    WARMING_CAMERA = "相机预热",
    CAPTURING = "扫描中",
    WRITING = "扫描中",
    ROTATING = "扫描中",
    SETTLING = "扫描中",
    UPLOADING = "正在上传环境数据",
    COMPLETED = "准备中",
    FAILED = "环境分析失败",
    CANCELLED = "已取消",
  }
  for _, scan_state in ipairs({
      "IDLE", "WARMING_CAMERA", "CAPTURING", "WRITING", "ROTATING",
      "STOPPING", "SETTLING", "COMMITTING_SCAN", "UPLOADING",
      "COMPLETED", "FAILED", "CANCELLING", "CANCELLED",
    }) do
    local state = state_module.new()
    state_module.apply_environment_scan_status(state,
      scan_status(scan_state, {
        scan_id = "ENV_STATE_MAP",
        directions_completed = 6,
        warmup_completed = 3,
        last_error = scan_state == "FAILED" and "camera_timeout" or "",
      }))
    local mapped = state_module.get_environment_scan_view(state)
    expect(type(mapped.title) == "string" and mapped.title ~= "" and
      type(mapped.detail) == "string" and mapped.detail ~= "",
      scan_state .. " must have user-facing text")
    if expected_titles[scan_state] then
      expect(string.find(mapped.title, expected_titles[scan_state], 1, true),
        scan_state .. " title mapping is incorrect")
    end
  end
end

local function person_follow_status(running, state, overrides)
  local result = {
    initialized = true,
    running = running,
    state = state,
    generation = running and 7 or 8,
    cancel_requested = false,
    worker_alive = running,
    last_target_found = running,
    last_zone = running and "CENTER" or "NO_TARGET",
    last_action = running and "FORWARD" or "NONE",
    last_error = "none",
    snapshot_seq = running and 11 or 0,
    snapshot_valid = running,
    image_current = running,
    image_snapshot_seq = running and 11 or 0,
    target_found = running,
    raw_count = running and 1 or 0,
    dedup_count = running and 1 or 0,
    selected_valid = running,
    selected_bbox_x1 = 200,
    selected_bbox_y1 = 60,
    selected_bbox_x2 = 360,
    selected_bbox_y2 = 330,
    selected_center_x = 0.4375,
    selected_center_y = 0.5417,
    confidence = 0.91,
    bbox_width_ratio = 0.25,
    person_center_x_ratio = 0.4375,
    left_background_ratio = 0.3125,
    right_background_ratio = 0.4375,
    approach_phase = "NORMAL_FOLLOW",
    first_align_completed = false,
    active_align_gate = "NONE",
    align_stable_count = 0,
    align_stable_required = 2,
    align_turn_hold_ms = 0,
    align_last_turn_direction = "NONE",
    align_overshoot_count = 0,
    arrived = false,
    arrival_reason = "none",
    zone = running and "CENTER" or "NO_TARGET",
    planned_action = running and "FORWARD" or "NONE",
    actual_action = running and "FORWARD" or "NONE",
    hold_ms = running and 500 or 0,
    next_turn_hold_ms = 250,
    startup_observation_active = false,
    startup_observation_remaining_ms = 0,
    startup_observation_exit_reason = "NONE",
    action_reason = running and "target_center_forward" or "--",
    snapshot_owner_acquired = running,
    snapshot_car_result = "ESP_OK",
    snapshot_error = "none",
    error_stage = "none",
    runtime_ms = running and 1234 or 0,
  }
  for key, value in pairs(overrides or {}) do result[key] = value end
  return result
end

-- Startup-observation progress participates in the person-follow UI signature.
do
  local state = state_module.new()
  state.page = "person_follow"
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "SENSE", {
      snapshot_seq = 20, startup_observation_active = true,
      startup_observation_remaining_ms = 800,
    }))
  local first = state_module.build_ui_signature(state, "person_follow")
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "SENSE", {
      snapshot_seq = 20, startup_observation_active = true,
      startup_observation_remaining_ms = 300,
    }))
  local remaining_changed = state_module.build_ui_signature(
    state, "person_follow")
  expect(first ~= remaining_changed,
    "startup-observation remaining time must refresh the person-follow UI")
end

-- Target-turn timing changes participate in the person-follow UI signature.
do
  local state = state_module.new()
  state.page = "person_follow"
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "ALIGN", {
      snapshot_seq = 20, hold_ms = 250, next_turn_hold_ms = 225,
    }))
  local first = state_module.build_ui_signature(state, "person_follow")
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "ALIGN", {
      snapshot_seq = 20, hold_ms = 225, next_turn_hold_ms = 200,
    }))
  local advanced = state_module.build_ui_signature(state, "person_follow")
  expect(first ~= advanced,
    "actual and next target-turn holds must refresh the person-follow UI")
end

-- Handheld debug uses one restricted local Capability and updates only after
-- the local UART-write result is returned.
do
  local state = state_module.new()
  reply(true, {
    accepted = true, requested_mode = "BLE",
    confirmation = "local_uart_write_only_no_ack",
  })
  local ok = state_module.set_handheld_debug(state, "BLE")
  expect(ok, "handheld enable should accept the local write result")
  expect(calls[#calls].name == "car_handheld_debug_set" and
    calls[#calls].args.mode == "BLE" and
    calls[#calls].args.enabled == nil,
    "handheld enable must call only its restricted local Capability")
  expect(state.handheld_requested_mode == "BLE",
    "successful enable must update requested mode")
  expect(state.handheld_mode_confirmation ==
    "local_uart_write_only_no_ack",
    "UI state must preserve no-ACK semantics")

  reply(false, { code = "busy_or_demo_active" }, "ESP_ERR_INVALID_STATE")
  ok = state_module.set_handheld_debug(state, "SERIAL")
  expect(calls[#calls].name == "car_handheld_debug_set" and
    calls[#calls].args.mode == "SERIAL" and
    calls[#calls].args.enabled == nil,
    "handheld restore must use the SERIAL string contract")
  expect(not ok and state.handheld_requested_mode == "BLE",
    "failed restore must keep the prior requested mode")
  expect(state.handheld_mode_error == "busy_or_demo_active",
    "failed restore must expose a bounded real error")
end

-- A new snapshot sequence changes the signature while repeated metadata does not.
do
  local state = state_module.new()
  state.page = "person_follow"
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "SEARCH", { snapshot_seq = 20 }))
  local first = state_module.build_ui_signature(state, "person_follow")
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "SEARCH", { snapshot_seq = 20 }))
  local repeated = state_module.build_ui_signature(state, "person_follow")
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "SEARCH", { snapshot_seq = 21 }))
  local next_frame = state_module.build_ui_signature(state, "person_follow")
  expect(first == repeated and first ~= next_frame,
    "only a new snapshot sequence should force a new frame refresh")
end

-- The manual confirmation route remains stable until Home is explicitly chosen.
do
  local state = state_module.new()
  state_module.open_person_follow_confirm(state)
  state.page = "person_follow"
  expect(state_module.route_for_state(state) == "person_follow",
    "manual person-follow confirmation must not bounce to Home")
  state_module.close_person_follow_confirm(state)
  state.page = "home"
  expect(state_module.route_for_state(state) == "home",
    "returning Home must restore the normal route")
end

-- Person-follow start uses the local Capability and requires status readback.
do
  local state = state_module.new()
  state.page = "person_follow"
  state_module.open_person_follow_confirm(state)
  reply(true, { accepted = true, code = "started", state = "STARTING" })
  reply(true, person_follow_status(true, "STARTING"))
  local ok = state_module.start_person_follow(state, 5000)
  expect(ok, "confirmed person-follow start must succeed after readback")
  expect(calls[#calls - 1].name == "person_follow_start" and
    next(calls[#calls - 1].args) == nil,
    "start must call person_follow_start with an empty object")
  expect(calls[#calls].name == "person_follow_status",
    "start must immediately read person_follow_status")
  expect(state_module.route_for_state(state) == "person_follow" and
    not state.person_follow_manual_confirm,
    "running readback must switch confirmation to running page")
end

-- A rejected start stays on confirmation and exposes the real short error.
do
  local state = state_module.new()
  state.page = "person_follow"
  state_module.open_person_follow_confirm(state)
  reply(false, { accepted = false, code = "start_rejected" },
    "ESP_ERR_INVALID_STATE")
  reply(true, person_follow_status(false, "IDLE"))
  local ok = state_module.start_person_follow(state, 6000)
  expect(not ok and state.person_follow_manual_confirm,
    "failed start must remain on confirmation")
  expect(state.person_follow_last_ui_error == "start_rejected",
    "failed start must display the Capability error")
end

-- Stop uses the local Capability and returns Home only after IDLE readback.
do
  local state = state_module.new()
  state.page = "person_follow"
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "FOLLOW_FORWARD"))
  reply(true, { accepted = true, code = "stopped", state = "IDLE" })
  reply(true, person_follow_status(false, "IDLE"))
  local ok = state_module.stop_person_follow(state, 7000)
  expect(ok, "stop must succeed after IDLE readback")
  expect(calls[#calls - 1].name == "person_follow_stop" and
    next(calls[#calls - 1].args) == nil,
    "stop must call person_follow_stop with an empty object")
  expect(calls[#calls].name == "person_follow_status",
    "stop must immediately read person_follow_status")
  expect(state_module.route_for_state(state) == "home",
    "IDLE readback must return Home")
end

-- Failed stop and failed status polling never hide a still-running worker.
do
  local state = state_module.new()
  state.page = "person_follow"
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "SEARCH"))
  reply(false, { accepted = false, code = "stop_failed" },
    "ESP_ERR_TIMEOUT")
  reply(true, person_follow_status(true, "STOPPING"))
  local ok = state_module.stop_person_follow(state, 8000)
  expect(not ok and state_module.route_for_state(state) == "person_follow",
    "failed stop must keep the running page")
  expect(state.person_follow_last_ui_error == "stop_failed",
    "failed stop must expose the real Capability error")

  reply(false, nil, "status_unavailable")
  local changed, polled = state_module.refresh_person_follow_status(
    state, 9000, true)
  expect(changed and polled and
    state_module.route_for_state(state) == "person_follow",
    "one status failure must preserve the last authoritative active route")
end

-- External start and stop drive the page without a UI-private running flag.
do
  local state = state_module.new()
  state.page = "home"
  state_module.apply_person_follow_status(state,
    person_follow_status(false, "IDLE"))
  expect(state_module.route_for_state(state) == "home",
    "external idle must stay Home")
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "SENSE"))
  expect(state_module.route_for_state(state) == "person_follow",
    "external active status must open the running page")
  state.page = "person_follow"
  state_module.apply_person_follow_status(state,
    person_follow_status(false, "CANCELLED"))
  expect(state_module.route_for_state(state) == "home",
    "external stopped status must return Home")
  expect(state.person_follow_running == nil,
    "UI must not create a second person-follow running truth")
end

-- ERROR remains on the running page so the stop control is retained.
do
  local state = state_module.new()
  state.page = "person_follow"
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "RECOVER"))
  state_module.apply_person_follow_status(state,
    person_follow_status(false, "ERROR", { last_error = "runtime_limit" }))
  expect(state_module.route_for_state(state) == "person_follow" and
    state_module.is_person_follow_running_page(state),
    "ERROR must retain the running page and stop control")
end

-- runtime_ms changes never alter the person-follow UI signature.
do
  local state = state_module.new()
  state.page = "person_follow"
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "FOLLOW_FORWARD", { runtime_ms = 100 }))
  local first = state_module.build_ui_signature(state, "person_follow")
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "FOLLOW_FORWARD", { runtime_ms = 9999 }))
  local second = state_module.build_ui_signature(state, "person_follow")
  expect(first == second, "runtime_ms must not trigger a page redraw")
end

-- Person-follow polling is one second idle and 500ms active.
do
  local state = state_module.new()
  reply(true, person_follow_status(false, "IDLE"))
  state_module.refresh_person_follow_status(state, 0, true)
  local after_idle = #calls
  state_module.refresh_person_follow_status(state, 999, false)
  expect(#calls == after_idle, "idle status must be throttled for 1000ms")
  reply(true, person_follow_status(false, "IDLE"))
  state_module.refresh_person_follow_status(state, 1000, false)
  expect(#calls == after_idle + 1, "idle status must poll at 1000ms")

  state_module.apply_person_follow_status(state,
    person_follow_status(true, "SEARCH"))
  state.person_follow_last_poll_ms = 1000
  local after_active = #calls
  state_module.refresh_person_follow_status(state, 1499, false)
  expect(#calls == after_active, "active status must be throttled for 500ms")
  reply(true, person_follow_status(true, "SEARCH"))
  state_module.refresh_person_follow_status(state, 1500, false)
  expect(#calls == after_active + 1, "active status must poll at 500ms")
end

-- Historical completed scores on cold boot must not auto-open Result.
do
  local state = state_module.new()
  state_module.apply_training_status(state, idle_status("20260720_TASK001"))
  expect(state_module.route_for_state(state) == "home",
    "cold boot must not route to historical Result")
end

-- Fatigue overview uses only the authoritative status fields.
do
  local state = state_module.new()
  state_module.apply_fatigue(state, {
    ok = true, available = true, fatigue = 0, fatigue_limit = 30,
    training_allowed = true, count_today = 2,
    latest_session_id = "20260722_TASK007", latest_score = 43.25,
    latest_delta = 5, minutes_until_allowed = 0,
  })
  local summary = state_module.get_fatigue_summary(state)
  expect(summary.available, "fatigue overview must be available")
  expect(summary.fatigue == "0", "fatigue value must match status")
  expect(summary.today == "2", "today count must match status")
  expect(summary.score == "43.25", "latest score must match status")
  expect(summary.load == "+5", "latest load must include positive sign")
end

-- Missing optional fatigue data degrades to stable placeholders.
do
  local state = state_module.new()
  state_module.apply_fatigue(state, {
    ok = true, available = true, training_allowed = true,
    latest_session_id = "", latest_score = nil, latest_delta = 0,
  })
  local summary = state_module.get_fatigue_summary(state)
  expect(summary.fatigue == "--", "missing fatigue must show placeholder")
  expect(summary.today == "0", "missing count_today must show zero")
  expect(summary.score == "--", "missing score must show placeholder")
  expect(summary.load == "--", "zero load without Session must be absent")
end

-- Backend blocking is visible but never becomes a second UI start gate.
do
  local state = state_module.new()
  state.selected_available = true
  state.training_state = "idle"
  state_module.apply_fatigue(state, {
    ok = true, available = true, fatigue = 31,
    training_allowed = false, count_today = 2,
    latest_session_id = "20260722_TASK007", latest_delta = 5,
    minutes_until_allowed = 1,
  })
  local summary = state_module.get_fatigue_summary(state)
  expect(summary.notice == "Rest about 1 min",
    "blocked status must use backend minutes")
  expect(state_module.is_start_enabled(state),
    "fatigue display must not replace the backend start gate")
end

-- Explicit unavailable and malformed field types remain renderable.
do
  local state = state_module.new()
  state_module.apply_fatigue(state, { ok = false, available = false })
  expect(state_module.get_fatigue_summary(state).notice ==
    "Fatigue unavailable", "unavailable must have a compact fallback")

  reply(false, { ok = false, available = false }, "NOT_AVAILABLE")
  local unavailable_ok = pcall(state_module.refresh_fatigue, state, 1, true)
  expect(unavailable_ok and not state.fatigue_available,
    "explicit unavailable must survive call-level failure mapping")

  state_module.apply_fatigue(state, {
    ok = true, available = true, fatigue = "bad", count_today = {},
    training_allowed = "yes", latest_session_id = 99,
    latest_score = false, latest_delta = "5",
    minutes_until_allowed = -1,
  })
  local summary = state_module.get_fatigue_summary(state)
  expect(summary.available, "malformed fields must not crash the panel")
  expect(summary.fatigue == "--" and summary.today == "0" and
    summary.score == "--" and summary.load == "--",
    "malformed fields must use placeholders")
  expect(state.fatigue_error == "FATIGUE_STATUS_INVALID",
    "malformed fields must retain a short UI error")
end

-- Capability errors preserve the previous good data and mark it stale.
do
  local state = state_module.new()
  state_module.apply_fatigue(state, {
    ok = true, available = true, fatigue = 10,
    training_allowed = true, count_today = 3,
    latest_session_id = "20260722_TASK008", latest_score = 51.5,
    latest_delta = 4, minutes_until_allowed = 0,
  })
  reply(false, nil, "transport_error")
  local changed, polled = state_module.refresh_fatigue(state, 5000, true)
  expect(changed and polled, "fatigue call error must be handled locally")
  local summary = state_module.get_fatigue_summary(state)
  expect(summary.fatigue == "10" and summary.today == "3",
    "call error must preserve the last good fatigue data")
  expect(summary.notice == "Data stale" and state.fatigue_stale,
    "call error must mark preserved data stale")

  local empty_state = state_module.new()
  reply(true, 42, nil)
  local ok = pcall(state_module.refresh_fatigue, empty_state, 5000, true)
  expect(ok, "non-table capability output must not escape as a Lua fatal")
  expect(not state_module.get_fatigue_summary(empty_state).available,
    "non-table output without cached data must remain unavailable")
end

-- Fatigue polling is immediate, five-second throttled, and Home-only.
do
  local state = state_module.new()
  local payload = {
    ok = true, available = true, fatigue = 7,
    training_allowed = true, count_today = 1,
    latest_session_id = "20260722_TASK009", latest_score = 40,
    latest_delta = 3, minutes_until_allowed = 0,
  }
  local before = #calls
  reply(true, payload)
  state_module.refresh_fatigue(state, 100, true)
  expect(#calls == before + 1 and calls[#calls].name ==
    "rehab_fatigue_status", "Home entry must poll fatigue immediately")
  state_module.refresh_fatigue(state, 4999, false)
  expect(#calls == before + 1, "Home must not repoll within five seconds")
  reply(true, payload)
  state_module.refresh_fatigue(state, 5100, false)
  expect(#calls == before + 2, "Home must repoll after five seconds")

  state.page = "training"
  state_module.refresh_fatigue(state, 10100, true)
  expect(#calls == before + 2, "non-Home pages must never poll fatigue")
  state.page = "home"
  reply(true, payload)
  state_module.refresh_fatigue(state, 10101, true)
  expect(#calls == before + 3, "returning Home must poll immediately")
end

-- Fatigue updates are isolated from Session, report, and action state.
do
  local state = state_module.new()
  state.training_state = "recording"
  state.current_session_id = "ACTIVE"
  state.bound_training_session_id = "ACTIVE"
  state.bound_result_session_id = "RESULT"
  state.score_cache.RESULT = { state = "succeeded" }
  state.action_inflight = true
  state.capability_call_inflight = true
  state_module.apply_fatigue(state, {
    ok = true, available = true, fatigue = 12,
    training_allowed = true, count_today = 4,
    latest_session_id = "LATEST", latest_score = 60,
    latest_delta = 2, minutes_until_allowed = 0,
  })
  expect(state.training_state == "recording" and
    state.current_session_id == "ACTIVE" and
    state.bound_training_session_id == "ACTIVE" and
    state.bound_result_session_id == "RESULT",
    "fatigue update must not alter Session bindings")
  expect(state.score_cache.RESULT.state == "succeeded" and
    state.action_inflight and state.capability_call_inflight,
    "fatigue update must not alter report or inflight state")
end

-- Home renders the compact overview without shrinking its action buttons.
do
  local objects = {}
  package.preload.lvgl = function()
    local function new_object(kind, parent, props)
      local object = {
        kind = kind,
        parent = parent,
        props = props,
        initial_text = props.text,
        text = props.text,
        valid = true,
      }
      function object:set_style(style) self.style = style end
      function object:set_text(text) self.text = text end
      function object:on(_, callback)
        self.callback = callback
        return 1
      end
      function object:off(_) self.callback = nil end
      function object:is_valid() return self.valid end
      function object:delete() self.valid = false end
      objects[#objects + 1] = object
      return object
    end
    return {
      container = function(parent, props)
        return new_object("container", parent, props)
      end,
      label = function(parent, props)
        return new_object("label", parent, props)
      end,
      button = function(parent, props)
        return new_object("button", parent, props)
      end,
    }
  end

  local home_page = require("page_home")
  local state = state_module.new()
  state_module.apply_fatigue(state, {
    ok = true, available = true, fatigue = 0,
    training_allowed = true, count_today = 2,
    latest_session_id = "20260722_TASK007", latest_score = 43.25,
    latest_delta = 5, minutes_until_allowed = 0,
  })
  local theme = {
    screen_width = 1024, screen_height = 600,
    bg = 0, panel = 1, border = 2, radius = 8,
    accent = 3, muted = 4, text = 5, warning = 6,
    normal = 7, scan_idle = 8, button_dark = 9, scan_active = 10,
    button_style = function(bg, text) return { bg = bg, text = text } end,
  }
  local app = {
    screen = {}, theme = theme, state = state, state_module = state_module,
    queue_start_training = function() end,
    queue_handheld_debug = function(enabled)
      app_queued_handheld = enabled
      return true
    end,
    open_person_follow_confirm = function()
      app_opened_follow_confirm = true
    end,
    open_environment_scan = function()
      app_opened_environment_scan = true
    end,
    open_task_decision_prepare = function()
      app_opened_task_decision = true
    end,
    navigate = function(page) app_navigated_page = page end,
  }
  home_page.create(app)
  home_page.refresh(app)

  local by_initial = {}
  local fatigue_panel = nil
  for _, object in ipairs(objects) do
    if object.initial_text then by_initial[object.initial_text] = object end
    if object.kind == "container" and object.props.x == 736 and
        object.props.y == 216 then
      fatigue_panel = object
    end
  end
  local fatigue_fields = {}
  for _, object in ipairs(objects) do
    if object.parent == fatigue_panel then
      fatigue_fields[object.props.x .. ":" .. object.props.y] = object
    end
  end
  expect(fatigue_fields["14:82"].text == "0 / 100",
    "Home must render fatigue as value over 100")
  expect(fatigue_fields["138:82"].text == "2",
    "Home must render today's count")
  expect(fatigue_fields["14:146"].text == "43.25",
    "Home must render the latest score")
  expect(fatigue_fields["138:146"].text == "+5",
    "Home must render the latest load")
  expect(by_initial["Fatigue unavailable"].text == "",
    "available Home must clear the fallback notice")
  expect(by_initial["启用手柄调试"].props.w == 220 and
    by_initial["追人模式"].props.w == 180 and
    by_initial["刷新状态"].props.w == 200 and
    by_initial["开始训练"].props.w == 306,
    "Home must expose four non-overlapping action touch areas")
  expect(by_initial["扫描环境"].props.x == 18 and
    by_initial["扫描环境"].props.y == 166 and
    by_initial["扫描环境"].props.w == 100 and
    by_initial["扫描环境"].props.h == 50,
    "Home environment scan button must fit inside the status panel")
  expect(by_initial["决策任务"].props.x == 126 and
    by_initial["决策任务"].props.y == 166 and
    by_initial["决策任务"].props.w == 100 and
    by_initial["决策任务"].props.h == 50,
    "Home decision button must remain visible beside environment scan")
  by_initial["扫描环境"].callback()
  expect(app_opened_environment_scan == true,
    "environment scan callback must enter the local scan flow")
  by_initial["决策任务"].callback()
  expect(app_opened_task_decision == true,
    "Home decision callback must use the shared preparation entry")
  by_initial["启用手柄调试"].callback()
  expect(app_queued_handheld == true,
    "handheld callback must only queue an enable request")
  expect(by_initial["启用手柄调试"].text == "处理中",
    "queued handheld action must immediately show bounded processing state")
  by_initial["追人模式"].callback()
  expect(app_opened_follow_confirm == true,
    "follow callback must only open the confirmation route")

  state.handheld_requested_mode = "BLE"
  home_page.refresh(app)
  expect(by_initial["启用手柄调试"].text == "恢复P4控制",
    "BLE requested mode must offer restore P4 control")

  state_module.apply_fatigue(state, {
    ok = true, available = true, fatigue = 31,
    training_allowed = false, count_today = 2,
    latest_session_id = "20260722_TASK007", latest_score = 43.25,
    latest_delta = 5, minutes_until_allowed = 1,
  })
  home_page.refresh(app)
  expect(by_initial["Fatigue unavailable"].text == "Rest about 1 min",
    "Home must render the backend rest estimate")
  home_page.destroy()
end

-- Rolling state, segment count, and stop reason are visible UI state.
do
  local state = state_module.new()
  state.page = "person_follow"
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "FOLLOW_FORWARD", {
      forward_rolling = true, forward_segments_completed = 1,
      forward_lease_remaining_ms = 700,
      rolling_stop_reason = "center_lease_active",
    }))
  local active = state_module.build_ui_signature(state, "person_follow")
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "ALIGN", {
      forward_rolling = false, forward_segments_completed = 1,
      forward_lease_remaining_ms = 0,
      rolling_stop_reason = "target_left",
    }))
  local stopped = state_module.build_ui_signature(state, "person_follow")
  expect(active ~= stopped and
    state.person_follow_status.rolling_stop_reason == "target_left",
    "rolling state and stop reason must reach the person-follow UI")
end

-- The dedicated scan page exposes progress, stop, and terminal Home actions.
do
  local objects = {}
  package.loaded.page_environment_scan = nil
  package.loaded.lvgl = nil
  package.preload.lvgl = function()
    local function new_object(kind, parent, props)
      local object = {
        kind = kind, parent = parent, props = props,
        initial_text = props.text, text = props.text, valid = true,
      }
      function object:set_style(style) self.style = style end
      function object:set_text(text) self.text = text end
      function object:on(_, callback) self.callback = callback return 1 end
      function object:off(_) self.callback = nil end
      function object:is_valid() return self.valid end
      function object:delete() self.valid = false end
      objects[#objects + 1] = object
      return object
    end
    return {
      container = function(parent, props)
        return new_object("container", parent, props)
      end,
      label = function(parent, props)
        return new_object("label", parent, props)
      end,
      button = function(parent, props)
        return new_object("button", parent, props)
      end,
    }
  end
  local page = require("page_environment_scan")
  local state = state_module.new()
  state_module.apply_environment_scan_status(state,
    scan_status("CAPTURING", {
      scan_id = "ENV_PAGE_SHARED",
      direction_index = 4,
      directions_completed = 4,
    }))
  local stop_queued = false
  local home_requested = false
  local theme = {
    screen_width = 1024, screen_height = 600, bg = 0, panel = 1,
    border = 2, radius = 8, accent = 3, muted = 4, text = 5,
    warning = 6, normal = 7, danger = 8, button_dark = 9,
    button_style = function(bg, text) return { bg = bg, text = text } end,
  }
  local app = {
    screen = {}, theme = theme, state = state, state_module = state_module,
    queue_environment_scan_cancel = function() stop_queued = true end,
    return_environment_scan_home = function() home_requested = true end,
  }
  page.create(app)
  page.refresh(app)
  local action = nil
  local title = nil
  local scan_id = nil
  for _, object in ipairs(objects) do
    if object.kind == "button" then action = object end
    if object.initial_text == "准备中" then title = object end
    if object.initial_text == "扫描编号：--" then scan_id = object end
  end
  expect(action and action.text == "停止扫描",
    "active scan page must expose stop")
  expect(title and string.find(title.text, "扫描中", 1, true),
    "active scan page must show scan progress")
  expect(scan_id and scan_id.text == "扫描编号：ENV_PAGE_SHARED",
    "scan page must show the shared coordinator scan ID")
  action.callback()
  expect(stop_queued and not home_requested,
    "active scan action must only queue coordinator cancel")

  state_module.apply_environment_scan_status(state,
    scan_status("COMPLETED", {
      active = false,
      scan_id = "ENV_PAGE_SHARED",
      directions_completed = 11,
    }))
  page.refresh(app)
  expect(action.text == "返回首页",
    "terminal scan page must offer Home")
  action.callback()
  expect(home_requested, "terminal scan action must return Home")
  page.destroy()
end

-- Person-follow confirmation queues start; running page exposes only stop.
do
  local objects = {}
  local snapshot_acquires = 0
  local frame_releases = 0
  package.loaded.page_person_follow = nil
  package.loaded.lvgl = nil
  package.loaded.rehab_person_follow_snapshot = nil
  package.preload.rehab_person_follow_snapshot = function()
    return {
      acquire = function(seq)
        snapshot_acquires = snapshot_acquires + 1
        local frame = { seq = seq }
        function frame:release() frame_releases = frame_releases + 1 end
        return frame
      end,
    }
  end
  package.preload.lvgl = function()
    local function new_object(kind, parent, props)
      local object = { kind = kind, parent = parent, props = props,
        initial_text = props.text, text = props.text, valid = true }
      function object:on(_, callback) self.callback = callback return 1 end
      function object:off(_) self.callback = nil end
      function object:is_valid() return self.valid end
      function object:delete() self.valid = false end
      function object:set_text(text) self.text = text end
      function object:set_style(style) self.style = style end
      function object:set_pos(x, y) self.x, self.y = x, y end
      function object:set_size(w, h) self.w, self.h = w, h end
      function object:fill_bg(color) self.fill = color end
      function object:load_frame(frame)
        self.loaded_seq = frame.seq
        self.load_count = (self.load_count or 0) + 1
      end
      objects[#objects + 1] = object
      return object
    end
    return {
      container = function(parent, props) return new_object("container", parent, props) end,
      label = function(parent, props) return new_object("label", parent, props) end,
      button = function(parent, props) return new_object("button", parent, props) end,
      canvas = function(parent, props) return new_object("canvas", parent, props) end,
    }
  end
  local page = require("page_person_follow")
  local navigated = nil
  local start_queued = false
  local stop_queued = false
  local theme = { screen_width = 1024, screen_height = 600, bg = 0,
    panel = 1, border = 2, radius = 8, accent = 3, text = 4,
    muted = 5, warning = 6, normal = 7, button_dark = 8, danger = 9,
    button_style = function(bg, text) return { bg = bg, text = text } end,
    text_style = function(color, align)
      return { color = color, align = align }
    end }
  local state = state_module.new()
  state.page = "person_follow"
  state_module.open_person_follow_confirm(state)
  state_module.apply_person_follow_status(state,
    person_follow_status(false, "IDLE"))
  local app = { screen = {}, theme = theme, state = state,
    state_module = state_module,
    navigate = function(target) navigated = target end,
    queue_person_follow_start = function()
      start_queued = true
      return true
    end,
    queue_person_follow_stop = function()
      stop_queued = true
      return true
    end }
  local calls_before_create = #calls
  page.create(app)
  page.refresh(app)
  local home, start = nil, nil
  for _, object in ipairs(objects) do
    if object.valid and object.kind == "button" then
      if object.initial_text == "返回首页" then home = object end
      if object.initial_text == "确认开始" then start = object end
    end
  end
  expect(home ~= nil and start ~= nil,
    "confirmation must create Home and confirmed-start buttons")
  expect(#calls == calls_before_create,
    "entering confirmation must not call person-follow Capability")
  start.callback()
  expect(start_queued and #calls == calls_before_create,
    "confirmed-start callback must only queue the action")
  home.callback()
  expect(navigated == "home" and not state.person_follow_manual_confirm,
    "Home callback must close confirmation without starting")
  page.destroy()

  objects = {}
  state.page = "person_follow"
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "FOLLOW_FORWARD"))
  page.create(app)
  page.refresh(app)
  expect(snapshot_acquires == 1 and frame_releases == 1,
    "the first running snapshot must acquire and release exactly once")
  page.refresh(app)
  expect(snapshot_acquires == 1,
    "repeating the same snapshot sequence must not decode again")
  local canvas, selected_box, left_boundary, right_boundary = nil, nil, nil, nil
  local params_label, state_label = nil, nil
  for _, object in ipairs(objects) do
    if object.kind == "canvas" then canvas = object end
    if object.kind == "container" and object.props.border_color == theme.normal then
      selected_box = object
    end
    if object.kind == "container" and object.props.w == 2 and
        object.props.x == 148 then left_boundary = object end
    if object.kind == "container" and object.props.w == 2 and
        object.props.x == 532 then right_boundary = object end
    if object.initial_text == "参数 --" then params_label = object end
    if object.initial_text == "状态 --" then state_label = object end
  end
  expect(canvas and canvas.loaded_seq == 11 and canvas.load_count == 1,
    "new snapshot must bulk-load the matching image frame")
  expect(left_boundary and right_boundary,
    "20/80 percent boundaries must map to x=128/512 inside the image")
  expect(selected_box and selected_box.x == 220 and selected_box.y == 142 and
      selected_box.w == 160 and selected_box.h == 270,
    "selected bbox must preserve the 1:1 640x360 coordinate transform")
  expect(params_label and
      params_label.text:find("动作 500ms", 1, true) and
      params_label.text:find("下次转向 250ms", 1, true),
    "person-follow UI must show actual and next target-turn holds")

  state_module.apply_person_follow_status(state,
    person_follow_status(true, "SENSE", {
      snapshot_seq = 11, startup_observation_active = true,
      startup_observation_remaining_ms = 640,
      target_found = false, selected_valid = false,
      planned_action = "NONE", actual_action = "NONE",
    }))
  page.refresh(app)
  expect(state_label and
      state_label.text:find("启动观察", 1, true) and
      state_label.text:find("剩余 640ms", 1, true),
    "person-follow UI must show startup-observation remaining time")

  state_module.apply_person_follow_status(state,
    person_follow_status(true, "RECOVER", {
      snapshot_seq = 12, image_current = false, image_snapshot_seq = 11,
      target_found = false, selected_valid = false,
      planned_action = "NONE", actual_action = "NONE",
      error_stage = "http", snapshot_error = "http_timeout",
    }))
  page.refresh(app)
  expect(snapshot_acquires == 1 and canvas.loaded_seq == 11,
    "HTTP failure must retain the last image without a false decode")
  state_module.apply_person_follow_status(state,
    person_follow_status(true, "SEARCH", {
      snapshot_seq = 13, image_current = true, image_snapshot_seq = 13,
      target_found = false, raw_count = 0, dedup_count = 0,
      selected_valid = false, zone = "NO_TARGET",
      planned_action = "SEARCH_RIGHT", actual_action = "SEARCH_RIGHT",
      action_reason = "no_target_search_right", error_stage = "none",
      snapshot_error = "none",
    }))
  page.refresh(app)
  local target_label, planned_label = nil, nil
  for _, object in ipairs(objects) do
    if object.initial_text == "目标 --" then target_label = object end
    if object.initial_text == "计划 --" then planned_label = object end
  end
  expect(snapshot_acquires == 2 and canvas.loaded_seq == 13 and
      target_label and target_label.text:find("未检测到人", 1, true) and
      planned_label and planned_label.text:find("SEARCH_RIGHT", 1, true),
    "no-target snapshot must render NO_TARGET/SEARCH_RIGHT on its own frame")
  local buttons = {}
  for _, object in ipairs(objects) do
    if object.valid and object.kind == "button" then
      buttons[#buttons + 1] = object
    end
  end
  expect(#buttons == 1 and buttons[1].initial_text == "暂停追人",
    "running page must expose only the pause/stop button")
  expect(buttons[1].initial_text ~= "返回首页" and
    buttons[1].initial_text ~= "启用手柄调试" and
    buttons[1].initial_text ~= "恢复P4控制",
    "running page must not expose Home or handheld controls")
  buttons[1].callback()
  expect(stop_queued and #calls == calls_before_create,
    "pause callback must only queue person_follow_stop")
  page.destroy()
end

-- Home decision entry is always rendered, but the shared state gate prevents
-- starting DeepSeek before a formal scene is ready.
do
  local state = state_module.new()
  local_decision_replies[#local_decision_replies + 1] = {
    ok = true, available = false, code = "missing",
  }
  expect(not state_module.open_task_decision_prepare(state) and
      state.home_hint == "暂无环境分析结果，请先扫描环境",
    "Home decision entry without a scene must explain that scanning is needed")
  state_module.apply_environment_scan_status(state,
    scan_status("ANALYZING"))
  local_decision_replies[#local_decision_replies + 1] = {
    ok = true, available = false, code = "invalid",
  }
  expect(not state_module.open_task_decision_prepare(state) and
      state.home_hint == "环境结果不可用，请重新扫描",
    "a malformed disk scene must not enter task decision")

  state.selected_available = true
  state.selected_task_id = "OT_CUP_TRANSFER_L1"
  state.selected_task_name = "水杯横向取放训练（一级）"
  state.page = "task_decision_prepare"
  local_decision_replies[#local_decision_replies + 1] = {
    ok = false, outcome = "rejected", state = "IDLE", active = false,
    generation = 0, error = "training_active", last_error = "training_active",
  }
  local ok = state_module.start_task_decision(state, 100)
  expect(not ok and state.selected_available and
      state.task_decision_status.state == "IDLE" and
      state_module.route_for_state(state) == "task_decision_prepare",
    "a pre-start rejection must stay on prepare without synthesizing generation 0")
end

-- The generation observer synchronizes external starts, protects critical
-- pages, consumes pending work once, and allows a later generation.
do
  local state = state_module.new()
  state.page = "home"
  state_module.apply_task_decision_status(state, {
    ok = true, state = "PREPARING", active = true, generation = 1,
    decision_id = "decision-1", last_error = "none",
  })
  expect(state_module.route_for_state(state) == "task_decision_progress",
    "Home plus external PREPARING must route to progress")
  expect(state_module.mark_task_decision_presented(
      state, "task_decision_progress"),
    "progress presentation must consume pending generation once")
  state.page = "task_decision_progress"
  state_module.apply_task_decision_status(state, {
    ok = true, state = "SELECTED", active = false, generation = 1,
    decision_id = "decision-1", task_id = "OT_CUP_DRINK_L2",
    task_name = "水杯拿取与饮水训练（二级）", reason = "目标清晰", score = 88,
    target_available = false, target_bbox_xyxy = {}, last_error = "none",
  })
  expect(state_module.route_for_state(state) == "task_decision_result",
    "the same generation may advance from progress to terminal result")
  expect(state_module.mark_task_decision_presented(
      state, "task_decision_result"),
    "terminal presentation must consume the generation")
  state_module.close_task_decision(state)
  state.page = "home"
  expect(state_module.route_for_state(state) == "home",
    "returning Home must not rebound to the same terminal generation")

  state_module.apply_task_decision_status(state, {
    ok = true, state = "DECIDING", active = true, generation = 2,
    decision_id = "decision-2", last_error = "none",
  })
  expect(state_module.route_for_state(state) == "task_decision_progress",
    "generation 2 must be presented normally")
end

do
  local protected = {
    { page = "training", training_state = "recording", expected = "training" },
    { page = "environment_scan", scan = true, expected = "environment_scan" },
    { page = "person_follow", follow = true, expected = "person_follow" },
  }
  for index, case in ipairs(protected) do
    local state = state_module.new()
    state.page = case.page
    if case.training_state then state.training_state = case.training_state end
    if case.scan then
      state_module.apply_environment_scan_status(state, scan_status("ANALYZING"))
    end
    if case.follow then
      state.person_follow_status.state = "FOLLOW_FORWARD"
    end
    state_module.apply_task_decision_status(state, {
      ok = true, state = "PREPARING", active = true,
      generation = 10 + index, decision_id = "external",
      last_error = "none",
    })
    expect(state.task_decision_pending_generation == 10 + index and
        state_module.route_for_state(state) == case.expected,
      "external decision must remain pending on protected page " .. case.page)
    state.training_state = "idle"
    state.environment_scan_status.active = false
    state.environment_scan_status.state = "COMPLETED"
    state.person_follow_status.state = "IDLE"
    state.page = "home"
    expect(state_module.route_for_state(state) == "task_decision_progress",
      "pending decision must be consumed after returning to a safe page")
  end
end

do
  local state = state_module.new()
  state.page = "home"
  state.end_confirm_open = true
  state_module.apply_task_decision_status(state, {
    ok = true, state = "FAILED", active = false, generation = 30,
    decision_id = "decision-30", last_error = "llm_not_configured",
  })
  expect(state.task_decision_pending_target == "result" and
      state_module.route_for_state(state) == "home",
    "end-confirm modal must not be preempted by an external terminal result")
  state.end_confirm_open = false
  expect(state_module.route_for_state(state) == "task_decision_result",
    "terminal pending result must appear after the modal closes")
end

-- Formal task decision uses one local coordinator and routes through
-- prepare -> progress -> result without calling the training capability.
do
  local state = state_module.new()
  state.selected_available = true
  state.selected_task_id = "OT_CUP_TRANSFER_L1"
  state.selected_task_name = "水杯横向取放训练（一级）"
  state_module.apply_environment_scan_status(state,
    scan_status("DECISION_READY", {
      active = false,
      scan_id = "scan_20260725_114552_000",
      package_sha256 = string.rep("a", 64),
    }))
  local_decision_replies[#local_decision_replies + 1] = {
    ok = true, available = true, code = "ok",
    scan_id = "scan_20260725_114552_000",
    sha256 = string.rep("a", 64),
  }
  expect(state_module.open_task_decision_prepare(state) and
      state_module.route_for_state(state) == "task_decision_prepare",
    "DECISION_READY must open the formal preparation page")
  local_decision_replies[#local_decision_replies + 1] = {
    ok = true, state = "PREPARING", active = true, generation = 9,
    decision_id = "decision-9", task_id = "", task_name = "", reason = "",
    score = 0, target_available = false, target_bbox_xyxy = {},
    last_error = "none",
  }
  local calls_before_decision = #calls
  local ok = state_module.start_task_decision(state, 100)
  expect(ok and state.selected_available and
      state_module.route_for_state(state) == "task_decision_progress",
    "starting a decision must preserve the old selection")
  expect(#calls == calls_before_decision,
    "local decision must not route through Agent capabilities")
  local_decision_replies[#local_decision_replies + 1] = {
    ok = true, state = "SELECTED", active = false, generation = 9,
    decision_id = "decision-9",
    source_scan_id = "scan_20260725_114552_000",
    source_scene_sha256 = string.rep("a", 64),
    task_id = "OT_CUP_DRINK_L2", task_name = "水杯拿取与饮水训练（二级）",
    reason = "目标清晰且适合当前训练", score = 88,
    target_available = true, target_object_id = "object_3",
    target_class = "bottle", target_source_frame_id = "frame_000004",
    target_source_frame_sequence = 4,
    target_bbox_xyxy = { 100, 60, 260, 330 },
    target_geometry = {
      available = true, valid = true, object_height_mm = 264,
      support_height_mm = 765.412,
      top_height_above_floor_mm = 1029.412,
    },
    last_error = "none",
  }
  local changed = state_module.refresh_task_decision_status(state, 500, true)
  local decision_view = state_module.get_task_decision_view(state)
  expect(changed and decision_view.selected and
      decision_view.task_id == "OT_CUP_DRINK_L2" and
      state.selected_target_geometry.object_height_mm == 264 and
      state_module.route_for_state(state) == "task_decision_result",
    "validated SELECTED status must route to the dedicated decision result")
end

-- Stage 8C-1D keeps suggested distance separate from target geometry and
-- applies one shared mm-to-cm formatter on both result and home data.
do
  local geometry_ui = require("rehab_ui_geometry")
  local state = state_module.new()
  local changed = state_module.apply_selected(state, {
    available = true,
    task_id = "OT_CUP_TRANSFER_L1",
    task_name = "水杯横向取放训练（一级）",
    reason = "固定理由🤖",
    score = 90,
    suggested_distance_cm = 0,
    decision_generation = 4,
    decision_rules_version = "fixture",
    target_available = true,
    target_object_id = "bottle_redacted",
    target_class = "bottle",
    target_geometry = {
      available = true,
      valid = true,
      object_height_mm = 264,
      support_height_mm = 765.412,
      top_height_above_floor_mm = 1029.412,
      distance_mm = 4663.2,
      distance_status = "dense_depth_unverified_for_thin_object",
    },
  })
  expect(changed and state.suggested_distance_cm == 0,
    "selected distance must remain a separate field")
  expect(geometry_ui.format_height(
      state.selected_target_geometry, "object_height_mm") == "26.4 cm",
    "object height must convert from mm to one-decimal cm")
  expect(geometry_ui.format_height(
      state.selected_target_geometry, "support_height_mm") == "76.5 cm",
    "support height must convert from mm to one-decimal cm")
  expect(geometry_ui.format_height(
      state.selected_target_geometry,
      "top_height_above_floor_mm") == "102.9 cm",
    "top height must convert from mm to one-decimal cm")
  expect(geometry_ui.format_distance(state.selected_target_geometry) ==
      "距离不可用",
    "an unverified absolute distance must not render as reliable")
  expect(state.selected_reason == "固定理由？",
    "unsupported dynamic glyphs must use a visible placeholder")
  expect(geometry_ui.format_height(
      { available = false, valid = false }, "object_height_mm") ==
      "高度不可用",
    "missing or invalid geometry must not render zero cm")
  expect(geometry_ui.format_height(
      { available = true, valid = true, object_height_mm = 0 },
      "object_height_mm") == "--",
    "zero geometry fields must not render zero cm")
  local own, support = geometry_ui.target_height_lines(
    state.selected_target_geometry, "bottle")
  expect(own == "水瓶自身高度：26.4 cm" and
      support == "水瓶离地高度：76.5 cm",
    "result must independently show both bottle heights")
  own, support = geometry_ui.target_height_lines(
    { object_height_mm = 123 }, "cup")
  expect(own == "水杯自身高度：12.3 cm" and
      support == "水杯离地高度：未知",
    "missing support height must not hide object height")
  own, support = geometry_ui.target_height_lines(
    { support_height_mm = 793.841 }, "cup")
  expect(own == "水杯自身高度：未知" and
      support == "水杯离地高度：79.4 cm",
    "missing object height must not hide support height")
  own, support = geometry_ui.target_height_lines({}, "book")
  expect(own == "书本自身高度：未知" and
      support == "书本离地高度：未知",
    "book labels and two independently missing heights must be explicit")
  own, support = geometry_ui.target_height_lines(
    { object_height_mm = 0, support_height_mm = -1 }, "bottle")
  expect(own == "水瓶自身高度：未知" and
      support == "水瓶离地高度：未知",
    "non-positive target heights must never render fake zero cm")
end

for _, call in ipairs(calls) do
  expect(call.name ~= "activate_skill" and call.name ~= "ask" and
    call.name ~= "agent_run",
    "all local UI tests must remain independent of Agent/LLM/skills")
end

print("rehab_ui_state offline tests: PASS")
