local capability = require("capability")
local json = require("json")
local environment_scan = require("rehab_environment_scan_ui")
local task_decision = require("rehab_task_decision_ui")
local ui_text = require("rehab_ui_text")

local M = {}

local ACTIVE_STATES = {
  starting = true,
  recording = true,
  stopping = true,
}

local PERSON_FOLLOW_ACTIVE_STATES = {
  STARTING = true,
  SENSE = true,
  CONFIRM = true,
  SEARCH = true,
  ALIGN = true,
  FOLLOW_FORWARD = true,
  TARGET_LOST = true,
  RECOVER = true,
  STOPPING = true,
}

local PERSON_FOLLOW_STOPPED_STATES = {
  IDLE = true,
  CANCELLED = true,
}

local ENVIRONMENT_SCAN_ACTIVE_STATES = {
  ACQUIRING_LEASE = true,
  WARMING_CAMERA = true,
  ROTATING = true,
  STOPPING = true,
  SETTLING = true,
  CAPTURING = true,
  WRITING = true,
  COMMITTING_SCAN = true,
  UPLOADING = true,
  ANALYZING = true,
  CANCELLING = true,
}

local TASK_DECISION_ACTIVE_STATES = {
  PREPARING = true,
  DECIDING = true,
  VALIDATING = true,
  COMMITTING = true,
}

local SCORE_PENDING_STATES = {
  pending = true,
  uploading = true,
  accepted = true,
  scoring = true,
}

local SCORE_TERMINAL_STATES = {
  succeeded = true,
  failed = true,
  invalid = true,
  session_mismatch = true,
  score_status_invalid = true,
  score_result_missing = true,
  score_result_invalid = true,
}

local STATUS_ACTIVE_PERIOD_MS = 250
local STATUS_IDLE_PERIOD_MS = 1000
local SELECTED_PERIOD_MS = 5000
local SCORE_PERIOD_MS = 1000
local FATIGUE_PERIOD_MS = 5000
local PERSON_FOLLOW_IDLE_PERIOD_MS = 1000
local PERSON_FOLLOW_ACTIVE_PERIOD_MS = 500
local ENVIRONMENT_SCAN_IDLE_PERIOD_MS = 1000
local ENVIRONMENT_SCAN_ACTIVE_PERIOD_MS = 500
local TASK_DECISION_ACTIVE_PERIOD_MS = 500
local TASK_DECISION_IDLE_PERIOD_MS = 1000
local ERROR_TEXT_MAX = 160

local function bounded_text(value)
  return ui_text.sanitize(value, ERROR_TEXT_MAX)
end

local function copy_table(value)
  if type(value) == "string" then
    return ui_text.sanitize(value)
  end
  if type(value) ~= "table" then
    return value
  end
  local out = {}
  for key, item in pairs(value) do
    out[key] = copy_table(item)
  end
  return out
end

local function signature_part(value)
  local text = value == nil and "<nil>" or tostring(value)
  return tostring(#text) .. ":" .. text
end

local function signature_bool(value)
  return signature_part(value == true and "true" or "false")
end

local function geometry_signature(geometry)
  geometry = type(geometry) == "table" and geometry or {}
  return table.concat({
    signature_bool(geometry.available),
    signature_bool(geometry.valid),
    signature_part(geometry.object_height_mm),
    signature_part(geometry.support_height_mm),
    signature_part(geometry.top_height_above_floor_mm),
    signature_part(geometry.distance_mm),
    signature_part(geometry.distance_status),
  }, "")
end

local function finite_number(value)
  if type(value) ~= "number" or value ~= value or
      value == math.huge or value == -math.huge then
    return nil
  end
  return value
end

local function nonnegative_integer(value)
  value = finite_number(value)
  if not value or value < 0 or value ~= math.floor(value) then
    return nil
  end
  return value
end

local function optional_string(value)
  if value == nil then
    return ""
  end
  if type(value) ~= "string" then
    return nil
  end
  return ui_text.sanitize(value)
end

local function session_id_from_path(path)
  local value = tostring(path or "")
  if value == "" then
    return ""
  end
  value = string.gsub(value, "\\", "/")
  return string.match(value, "([^/]+)$") or value
end

local function response_reason(result, call_err)
  if type(result) == "table" then
    return bounded_text(result.code or result.error_code or result.message or
      result.reason_code or result.last_error or call_err or "CAPABILITY_FAILED")
  end
  return bounded_text(call_err or "CAPABILITY_FAILED")
end

local function decode_output(raw)
  if type(raw) == "table" then
    return raw
  end
  if type(raw) ~= "string" or raw == "" then
    return nil
  end
  local ok, decoded = pcall(json.decode, raw)
  if ok and type(decoded) == "table" then
    return decoded
  end
  return nil
end

local function call_log_signature(name, ok, result, err)
  if name ~= "person_follow_status" and
      name ~= "rehab_training_status" and
      name ~= "rehab_task_decision_selected_get" and
      name ~= "rehab_fatigue_status" then
    return nil
  end
  result = type(result) == "table" and result or {}
  local current = type(result.current_session) == "table" and
    result.current_session or {}
  local reason = ok and bounded_text(result.error or result.error_code or
    result.reason or result.reason_code or result.last_error or
    result.warning or "") or
    response_reason(result, err)
  if name == "rehab_task_decision_selected_get" then
    return table.concat({
      signature_bool(ok),
      signature_bool(result.available),
      signature_part(result.task_id),
      signature_part(result.task_name),
      signature_part(result.decision_generation),
      signature_part(result.decision_id),
      signature_part(reason),
    }, "")
  end
  if name == "rehab_fatigue_status" then
    return table.concat({
      signature_bool(ok),
      signature_bool(result.available),
      signature_part(result.fatigue),
      signature_part(result.training_allowed),
      signature_part(result.count_today),
      signature_part(result.decay_paused),
      signature_part(result.time_trusted),
      signature_part(reason),
    }, "")
  end
  local parts = {
    signature_bool(ok),
    signature_part(result.state),
    signature_part(result.session_id or
      session_id_from_path(current.session_path or result.session_path)),
    signature_part(reason),
  }
  if name == "rehab_training_status" then
    parts[#parts + 1] = signature_part(result.generation)
  end
  return table.concat(parts, "")
end

local function log_call(state, name, ok, result, err)
  local signature = call_log_signature(name, ok, result, err)
  if signature then
    state.capability_log_signatures = state.capability_log_signatures or {}
    if state.capability_log_signatures[name] == signature then
      return
    end
    state.capability_log_signatures[name] = signature
  end
  if ok then
    print(string.format("REHAB_UI_CAP_CALL name=%s ok=true state=%s session=%s",
      tostring(name), tostring(result and result.state or ""),
      tostring(result and result.session_id or "")))
  else
    print(string.format("REHAB_UI_CAP_CALL_FAILED name=%s reason=%s",
      tostring(name), response_reason(result, err)))
  end
end

function M.call_agent_capability(state, name, args)
  if state.capability_call_inflight then
    return false, nil, "CAPABILITY_CALL_IN_FLIGHT"
  end

  state.capability_call_inflight = true
  local pcall_ok, call_ok, call_out, call_err = pcall(function()
    return capability.call(name, args or {}, {
      source_cap = "rehab_ui",
      max_output_bytes = 65536,
    })
  end)
  state.capability_call_inflight = false

  if not pcall_ok then
    local reason = bounded_text(call_ok)
    log_call(state, name, false, nil, reason)
    return false, nil, reason
  end

  local decoded = decode_output(call_out)
  if not decoded and call_out then
    local reason = "CAPABILITY_JSON_DECODE_FAILED"
    log_call(state, name, false, nil, reason)
    return false, nil, reason
  end

  log_call(state, name, call_ok == true, decoded, call_err)
  return call_ok == true, decoded, call_err
end

local function training_reason_text(reason)
  local messages = {
    no_selected_task = "暂无可用训练任务，请先完成任务决策",
    selected_task_unavailable = "暂无可用训练任务，请先完成任务决策",
    task_decision_active = "正在重新决策，请稍候",
    not_ready = "训练传感器尚未就绪",
    fatigue_limit = "当前疲劳门禁不允许开始训练",
  }
  return messages[tostring(reason or "")] or bounded_text(reason)
end

local function call_environment_scan(method)
  local operation = environment_scan[method]
  if type(operation) ~= "function" then
    return false, nil, "LOCAL_SCAN_API_UNAVAILABLE"
  end
  local pcall_ok, result = pcall(operation)
  if not pcall_ok then
    return false, nil, bounded_text(result)
  end
  if type(result) ~= "table" then
    return false, nil, "LOCAL_SCAN_STATUS_INVALID"
  end
  if result.ok ~= true then
    return false, result, bounded_text(result.error or "LOCAL_SCAN_FAILED")
  end
  return true, result, nil
end

local function call_task_decision(method)
  local operation = task_decision[method]
  if type(operation) ~= "function" then
    return false, nil, "LOCAL_TASK_DECISION_API_UNAVAILABLE"
  end
  local pcall_ok, result = pcall(operation)
  if not pcall_ok then
    return false, nil, bounded_text(result)
  end
  if type(result) ~= "table" then
    return false, nil, "LOCAL_TASK_DECISION_STATUS_INVALID"
  end
  if result.ok ~= true and result.busy ~= true then
    return false, result, bounded_text(result.error or
      result.last_error or "LOCAL_TASK_DECISION_FAILED")
  end
  return true, result, nil
end

function M.new()
  return {
    page = "home",
    bootstrap_ready = false,
    selected_available = false,
    selected_decision_id = "",
    selected_task_id = "",
    selected_task_name = "",
    selected_reason = "",
    selected_score = 0,
    selected_target_available = false,
    selected_target_object_id = "",
    selected_target_class = "",
    selected_target_source_frame_id = "",
    selected_target_source_frame_sequence = 0,
    selected_target_bbox_xyxy = {},
    selected_target_geometry = { available = false, valid = false },
    suggested_distance_cm = nil,
    decision_generation = nil,
    decision_rules_version = "",
    training_state = "idle",
    previous_training_state = nil,
    previous_current_session_id = "",
    current_session_id = "",
    current_task_name = "",
    last_completed_session_id = "",
    last_completed_task_name = "",
    rows_written = 0,
    queue_drop = 0,
    sd_write_error = 0,
    sd_ready = false,
    required_sensors_ok = false,
    node1 = {},
    node2 = {},
    emg = {},
    imu = {},
    training_error = nil,
    bound_training_session_id = "",
    bound_result_session_id = "",
    result_task_name = "",
    score_state = "missing",
    score_result = nil,
    score_error = nil,
    score_cache = {},
    fatigue_available = false,
    fatigue_value = nil,
    fatigue_limit = nil,
    fatigue_training_allowed = nil,
    fatigue_count_today = nil,
    fatigue_latest_session_id = "",
    fatigue_latest_score = nil,
    fatigue_latest_delta = nil,
    fatigue_minutes_until_allowed = nil,
    fatigue_last_refresh_ms = -FATIGUE_PERIOD_MS,
    fatigue_error = nil,
    fatigue_stale = false,
    home_hint = "正在等待训练状态",
    handheld_requested_mode = "SERIAL",
    handheld_mode_confirmation = "local_uart_write_only_no_ack",
    handheld_mode_switch_result = "not_requested",
    handheld_mode_error = nil,
    handheld_mode_switch_inflight = false,
    person_follow_status = nil,
    person_follow_status_error = nil,
    person_follow_last_poll_ms = -PERSON_FOLLOW_IDLE_PERIOD_MS,
    person_follow_manual_confirm = false,
    person_follow_starting_inflight = false,
    person_follow_stopping_inflight = false,
    person_follow_last_ui_error = nil,
    environment_scan_status = {
      state = "IDLE",
      active = false,
      cancel_requested = false,
      scan_id = "",
      generation = 0,
      direction_index = 0,
      directions_completed = 0,
      directions_total = 11,
      warmup_completed = 0,
      warmup_total = 5,
      upload_state = "IDLE",
      retryable = false,
      last_error = "",
      upload_error = "",
      package_length = 0,
      package_sha256 = "",
      scene_result_path = "",
    },
    environment_scan_last_poll_ms = -ENVIRONMENT_SCAN_IDLE_PERIOD_MS,
    environment_scan_error = nil,
    environment_scan_view_requested = false,
    task_decision_status = {
      state = "IDLE",
      active = false,
      trace_stage = "IDLE",
      elapsed_sec = 0,
      trace_stage_seq = 0,
      trace_active = false,
      trace_failure_code = "",
      generation = 0,
      decision_id = "",
      source_scan_id = "",
      source_scene_sha256 = "",
      task_id = "",
      task_name = "",
      reason = "",
      score = 0,
      target_available = false,
      target_object_id = "",
      target_class = "",
      target_source_frame_id = "",
      target_source_frame_sequence = 0,
      target_bbox_xyxy = {},
      retry_used = false,
      last_error = "none",
    },
    task_decision_last_poll_ms = -TASK_DECISION_IDLE_PERIOD_MS,
    task_decision_error = nil,
    task_decision_view_requested = false,
    task_decision_last_seen_generation = 0,
    task_decision_pending_generation = 0,
    task_decision_pending_target = "",
    task_decision_presented_generation = 0,
    task_decision_presented_target = "",
    action_inflight = false,
    capability_call_inflight = false,
    capability_log_signatures = {},
    end_confirm_open = false,
    last_status_poll_ms = -STATUS_IDLE_PERIOD_MS,
    last_selected_poll_ms = -SELECTED_PERIOD_MS,
    last_score_poll_ms = -SCORE_PERIOD_MS,
    last_error = nil,
  }
end

local function person_follow_snapshot_signature(state)
  local status = state.person_follow_status or {}
  return table.concat({
    signature_part(status.snapshot_seq),
    signature_part(status.state),
    signature_bool(status.running),
    signature_part(status.generation),
    signature_bool(status.cancel_requested),
    signature_bool(status.worker_alive),
    signature_bool(status.target_found),
    signature_part(status.zone),
    signature_part(status.selected_bbox_x1),
    signature_part(status.selected_bbox_y1),
    signature_part(status.selected_bbox_x2),
    signature_part(status.selected_bbox_y2),
    signature_part(status.planned_action),
    signature_part(status.actual_action),
    signature_part(status.hold_ms),
    signature_part(status.next_turn_hold_ms),
    signature_bool(status.startup_observation_active),
    signature_part(status.startup_observation_remaining_ms),
    signature_part(status.startup_observation_exit_reason),
    signature_bool(status.forward_rolling),
    signature_part(status.forward_segments_completed),
    signature_part(status.rolling_stop_reason),
    signature_part(status.snapshot_car_result),
    signature_part(status.snapshot_error),
    signature_part(status.error_stage),
    signature_bool(status.image_current),
    signature_part(state.person_follow_status_error),
  }, "")
end

function M.is_person_follow_active(state)
  local status = state.person_follow_status
  return type(status) == "table" and
    (status.running == true or
     PERSON_FOLLOW_ACTIVE_STATES[tostring(status.state or "")] == true)
end

function M.is_person_follow_stopped(state)
  local status = state.person_follow_status
  return type(status) == "table" and status.running == false and
    PERSON_FOLLOW_STOPPED_STATES[tostring(status.state or "")] == true
end

function M.is_person_follow_running_page(state)
  if M.is_person_follow_active(state) then
    return true
  end
  local status = state.person_follow_status
  return state.page == "person_follow" and
    state.person_follow_manual_confirm ~= true and
    type(status) == "table" and status.state == "ERROR"
end

function M.open_person_follow_confirm(state)
  state.person_follow_manual_confirm = true
  state.person_follow_last_ui_error = nil
end

function M.close_person_follow_confirm(state)
  state.person_follow_manual_confirm = false
  state.person_follow_last_ui_error = nil
end

function M.apply_person_follow_status(state, result)
  if type(result) ~= "table" or type(result.running) ~= "boolean" or
      type(result.state) ~= "string" then
    return false
  end
  local before = person_follow_snapshot_signature(state)
  state.person_follow_status = copy_table(result)
  state.person_follow_status_error = nil
  if M.is_person_follow_active(state) then
    state.person_follow_manual_confirm = false
  end
  return before ~= person_follow_snapshot_signature(state)
end

function M.set_handheld_debug(state, requested_mode)
  if state.action_inflight then
    return false, nil, "ACTION_IN_FLIGHT"
  end
  if requested_mode ~= "BLE" and requested_mode ~= "SERIAL" then
    return false, nil, "HANDHELD_MODE_INVALID"
  end
  state.action_inflight = true
  local ok, result, err = M.call_agent_capability(state,
    "car_handheld_debug_set", { mode = requested_mode })
  state.action_inflight = false

  local requested_mode = type(result) == "table" and
    tostring(result.requested_mode or "") or ""
  if ok and type(result) == "table" and result.accepted == true and
      (requested_mode == "SERIAL" or requested_mode == "BLE") then
    state.handheld_requested_mode = requested_mode
    state.handheld_mode_confirmation = tostring(result.confirmation or
      "local_uart_write_only_no_ack")
    state.handheld_mode_switch_result = "ok"
    state.handheld_mode_error = nil
    return true, result, nil
  end

  local reason = response_reason(result, err)
  state.handheld_mode_switch_result = "failed"
  state.handheld_mode_error = reason
  return false, result, reason
end

function M.apply_selected(state, result)
  if type(result) ~= "table" then
    return false
  end
  local before = table.concat({
    tostring(state.selected_available), state.selected_decision_id,
    state.selected_task_id,
    state.selected_task_name, tostring(state.suggested_distance_cm),
    tostring(state.decision_generation), state.selected_reason,
    tostring(state.selected_score), state.selected_target_object_id,
    geometry_signature(state.selected_target_geometry),
  }, "|")

  state.selected_available = result.available == true
  if state.selected_available then
    state.selected_decision_id = tostring(result.decision_id or "")
    state.selected_task_id = tostring(result.task_id or "")
    state.selected_task_name = tostring(result.task_name or "")
    state.suggested_distance_cm = tonumber(result.suggested_distance_cm)
    state.decision_generation = tonumber(result.decision_generation)
    state.decision_rules_version = tostring(result.decision_rules_version or "")
    state.selected_reason = ui_text.sanitize(result.reason or "")
    state.selected_score = tonumber(result.score) or 0
    state.selected_target_available = result.target_available == true
    state.selected_target_object_id = tostring(result.target_object_id or "")
    state.selected_target_class = tostring(result.target_class or "")
    state.selected_target_source_frame_id =
      tostring(result.target_source_frame_id or "")
    state.selected_target_source_frame_sequence =
      tonumber(result.target_source_frame_sequence) or 0
    state.selected_target_bbox_xyxy = copy_table(
      result.target_bbox_xyxy or {})
    state.selected_target_geometry = copy_table(
      result.target_geometry or { available = false, valid = false })
    state.home_hint = "训练任务已就绪"
  else
    state.selected_decision_id = ""
    state.selected_task_id = ""
    state.selected_task_name = ""
    state.suggested_distance_cm = nil
    state.decision_generation = nil
    state.decision_rules_version = ""
    state.selected_reason = ""
    state.selected_score = 0
    state.selected_target_available = false
    state.selected_target_object_id = ""
    state.selected_target_class = ""
    state.selected_target_source_frame_id = ""
    state.selected_target_source_frame_sequence = 0
    state.selected_target_bbox_xyxy = {}
    state.selected_target_geometry = { available = false, valid = false }
    state.home_hint = "尚未选择训练任务"
  end

  local after = table.concat({
    tostring(state.selected_available), state.selected_decision_id,
    state.selected_task_id,
    state.selected_task_name, tostring(state.suggested_distance_cm),
    tostring(state.decision_generation), state.selected_reason,
    tostring(state.selected_score), state.selected_target_object_id,
    geometry_signature(state.selected_target_geometry),
  }, "|")
  return before ~= after
end

local function completed_session_id(completed)
  if type(completed) ~= "table" or completed.exists ~= true then
    return ""
  end
  return session_id_from_path(completed.session_path)
end

local function bind_active_session(state, session_id, task_name)
  if session_id == "" then
    return
  end
  state.current_session_id = session_id
  state.bound_training_session_id = session_id
  if task_name and task_name ~= "" then
    state.current_task_name = task_name
  end
end

local function bind_result_session(state, session_id, task_name)
  if session_id == "" then
    return false
  end
  state.bound_result_session_id = session_id
  state.result_task_name = tostring(task_name or state.current_task_name or "")
  state.score_state = "pending"
  state.score_result = nil
  state.score_error = nil
  state.last_score_poll_ms = -SCORE_PERIOD_MS
  return true
end

function M.apply_training_status(state, result)
  if type(result) ~= "table" then
    return false
  end

  local previous_state = state.training_state
  local previous_session = state.current_session_id ~= "" and
    state.current_session_id or state.bound_training_session_id
  local current = type(result.current_session) == "table" and
    result.current_session or {}
  local completed = type(result.last_completed_session) == "table" and
    result.last_completed_session or {}
  local next_state = tostring(result.state or current.state or "idle")
  local current_id = session_id_from_path(current.session_path or result.session_path)
  local current_task = tostring(current.task_name or "")
  local completed_id = completed_session_id(completed)

  state.previous_training_state = previous_state
  state.previous_current_session_id = previous_session or ""
  state.training_state = next_state
  state.rows_written = tonumber(current.rows_written or result.rows_written) or 0
  state.queue_drop = tonumber(current.record_queue_drop_count or
    result.queue_drop_count) or 0
  state.sd_write_error = tonumber(current.sd_write_error_count or
    result.write_error_count) or 0
  state.sd_ready = result.sd_ready == true
  state.required_sensors_ok = result.required_sensors_ok == true
  state.node1 = copy_table(result.node1 or {})
  state.node2 = copy_table(result.node2 or {})
  state.emg = copy_table(result.emg or {})
  state.imu = {
    copy_table(result.imu1 or {}), copy_table(result.imu2 or {}),
    copy_table(result.imu3 or {}), copy_table(result.imu4 or {}),
  }
  local status_error = result.last_error
  state.training_error = type(status_error) == "string" and
    status_error ~= "" and bounded_text(status_error) or nil
  state.last_completed_session_id = completed_id
  state.last_completed_task_name = tostring(completed.task_name or "")

  if ACTIVE_STATES[next_state] then
    bind_active_session(state, current_id, current_task)
  else
    state.current_session_id = ""
  end

  if state.bootstrap_ready and ACTIVE_STATES[previous_state] and
      next_state == "idle" and completed_id ~= "" and
      completed_id == previous_session then
    bind_result_session(state, completed_id, completed.task_name)
  end

  state.bootstrap_ready = true
  state.last_error = nil
  return true
end

function M.apply_score(state, result)
  local bound = state.bound_result_session_id
  if bound == "" or type(result) ~= "table" then
    return false
  end
  if result.ok ~= true then
    state.score_state = tostring(result.error_code or "missing")
    state.score_error = bounded_text(result.message or result.error_code or
      "评分结果暂不可用")
    state.score_result = nil
    return true
  end
  if tostring(result.session_id or "") ~= bound then
    state.score_state = "session_mismatch"
    state.score_error = "Session mismatch"
    state.score_result = nil
    return true
  end

  state.score_state = tostring(result.state or "pending")
  state.result_task_name = tostring(result.task_name or state.result_task_name or "")
  state.score_error = result.last_error and bounded_text(result.last_error) or nil
  if state.score_state == "succeeded" and result.result_available == true and
      type(result.result) == "table" then
    state.score_result = copy_table(result.result)
    state.score_cache[bound] = copy_table(result)
    if result.result.valid ~= true then
      state.score_state = "invalid"
      state.score_error = bounded_text(result.result.invalid_reason or
        "评分结果无效")
    end
  else
    state.score_result = nil
  end
  return true
end

local function fatigue_signature(state)
  return table.concat({
    signature_bool(state.fatigue_available),
    signature_part(state.fatigue_value),
    signature_part(state.fatigue_limit),
    signature_part(state.fatigue_training_allowed),
    signature_part(state.fatigue_count_today),
    signature_part(state.fatigue_latest_session_id),
    signature_part(state.fatigue_latest_score),
    signature_part(state.fatigue_latest_delta),
    signature_part(state.fatigue_minutes_until_allowed),
    signature_part(state.fatigue_error),
    signature_bool(state.fatigue_stale),
  }, "")
end

local function clear_fatigue_values(state)
  state.fatigue_value = nil
  state.fatigue_limit = nil
  state.fatigue_training_allowed = nil
  state.fatigue_count_today = nil
  state.fatigue_latest_session_id = ""
  state.fatigue_latest_score = nil
  state.fatigue_latest_delta = nil
  state.fatigue_minutes_until_allowed = nil
end

function M.apply_fatigue(state, result)
  local before = fatigue_signature(state)
  if type(result) ~= "table" or result.ok ~= true or
      result.available ~= true then
    state.fatigue_available = false
    clear_fatigue_values(state)
    state.fatigue_error = type(result) == "table" and
      response_reason(result, nil) or "FATIGUE_STATUS_INVALID"
    state.fatigue_stale = false
    return before ~= fatigue_signature(state)
  end

  local invalid = false
  local latest_session_id = optional_string(result.latest_session_id)
  if latest_session_id == nil then
    latest_session_id = ""
    invalid = true
  end
  local training_allowed = result.training_allowed
  if type(training_allowed) ~= "boolean" then
    training_allowed = nil
    invalid = true
  end

  state.fatigue_available = true
  state.fatigue_value = finite_number(result.fatigue)
  state.fatigue_limit = finite_number(result.fatigue_limit)
  state.fatigue_training_allowed = training_allowed
  state.fatigue_count_today = nonnegative_integer(result.count_today)
  state.fatigue_latest_session_id = latest_session_id
  state.fatigue_latest_score = finite_number(result.latest_score)
  state.fatigue_latest_delta = finite_number(result.latest_delta)
  state.fatigue_minutes_until_allowed =
    nonnegative_integer(result.minutes_until_allowed)
  if result.fatigue ~= nil and state.fatigue_value == nil then invalid = true end
  if result.fatigue_limit ~= nil and state.fatigue_limit == nil then invalid = true end
  if result.count_today ~= nil and state.fatigue_count_today == nil then
    invalid = true
  end
  if result.latest_score ~= nil and state.fatigue_latest_score == nil then
    invalid = true
  end
  if result.latest_delta ~= nil and state.fatigue_latest_delta == nil then
    invalid = true
  end
  if result.minutes_until_allowed ~= nil and
      state.fatigue_minutes_until_allowed == nil then
    invalid = true
  end
  state.fatigue_error = invalid and "FATIGUE_STATUS_INVALID" or nil
  state.fatigue_stale = false
  return before ~= fatigue_signature(state)
end

function M.get_fatigue_summary(state)
  if state.fatigue_available ~= true then
    return {
      available = false,
      fatigue = "--", today = "0", score = "--", load = "--",
      notice = "Fatigue unavailable",
    }
  end

  local load = "--"
  if state.fatigue_latest_delta ~= nil and
      (state.fatigue_latest_delta ~= 0 or
       state.fatigue_latest_session_id ~= "") then
    load = string.format("%+.6g", state.fatigue_latest_delta)
  end
  local notice = ""
  if state.fatigue_training_allowed == false then
    local minutes = state.fatigue_minutes_until_allowed and
      string.format("%d", state.fatigue_minutes_until_allowed) or "--"
    notice = "Rest about " .. minutes .. " min"
  elseif state.fatigue_stale then
    notice = "Data stale"
  end
  return {
    available = true,
    fatigue = state.fatigue_value and
      string.format("%.0f", state.fatigue_value) or "--",
    today = state.fatigue_count_today and
      string.format("%d", state.fatigue_count_today) or "0",
    score = state.fatigue_latest_score and
      string.format("%.2f", state.fatigue_latest_score) or "--",
    load = load,
    notice = notice,
  }
end

function M.refresh_selected(state, now_ms, force)
  if not force and now_ms - state.last_selected_poll_ms < SELECTED_PERIOD_MS then
    return false, false
  end
  state.last_selected_poll_ms = now_ms
  local ok, result, err = M.call_agent_capability(state,
    "rehab_task_decision_selected_get", {})
  if not ok then
    state.last_error = response_reason(result, err)
    state.home_hint = "训练任务状态暂不可用"
    return false, true
  end
  return M.apply_selected(state, result), true
end

function M.refresh_training_status(state, now_ms, force)
  local period = ACTIVE_STATES[state.training_state] and
    STATUS_ACTIVE_PERIOD_MS or STATUS_IDLE_PERIOD_MS
  if not force and now_ms - state.last_status_poll_ms < period then
    return false, false
  end
  state.last_status_poll_ms = now_ms
  local ok, result, err = M.call_agent_capability(state,
    "rehab_training_status", {})
  if not ok then
    state.training_error = response_reason(result, err)
    state.last_error = state.training_error
    return false, true
  end
  return M.apply_training_status(state, result), true
end

function M.refresh_score(state, now_ms, force)
  local session_id = state.bound_result_session_id
  if session_id == "" then
    return false, false
  end
  if not force and now_ms - state.last_score_poll_ms < SCORE_PERIOD_MS then
    return false, false
  end
  if not force and SCORE_TERMINAL_STATES[state.score_state] then
    return false, false
  end
  state.last_score_poll_ms = now_ms
  local ok, result, err = M.call_agent_capability(state, "rehab_score_query", {
    session_id = session_id,
  })
  if not ok then
    state.score_state = "failed"
    state.score_error = response_reason(result, err)
    return true, true
  end
  return M.apply_score(state, result), true
end

function M.refresh_fatigue(state, now_ms, force)
  if state.page ~= "home" then
    return false, false
  end
  if not force and
      now_ms - state.fatigue_last_refresh_ms < FATIGUE_PERIOD_MS then
    return false, false
  end
  state.fatigue_last_refresh_ms = now_ms
  local ok, result, err = M.call_agent_capability(state,
    "rehab_fatigue_status", {})
  if not ok then
    if type(result) == "table" and result.available == false then
      return M.apply_fatigue(state, result), true
    end
    local before = fatigue_signature(state)
    state.fatigue_error = response_reason(result, err)
    state.fatigue_stale = state.fatigue_available == true
    return before ~= fatigue_signature(state), true
  end
  return M.apply_fatigue(state, result), true
end

function M.refresh_person_follow_status(state, now_ms, force)
  local period = M.is_person_follow_active(state) and
    PERSON_FOLLOW_ACTIVE_PERIOD_MS or PERSON_FOLLOW_IDLE_PERIOD_MS
  if not force and
      now_ms - state.person_follow_last_poll_ms < period then
    return false, false
  end
  state.person_follow_last_poll_ms = now_ms
  local before = person_follow_snapshot_signature(state)
  local ok, result, err = M.call_agent_capability(state,
    "person_follow_status", {})
  if not ok then
    state.person_follow_status_error = response_reason(result, err)
    return before ~= person_follow_snapshot_signature(state), true
  end
  if not M.apply_person_follow_status(state, result) then
    if type(result) ~= "table" or type(result.running) ~= "boolean" or
        type(result.state) ~= "string" then
      state.person_follow_status_error = "PERSON_FOLLOW_STATUS_INVALID"
      return before ~= person_follow_snapshot_signature(state), true
    end
  end
  return before ~= person_follow_snapshot_signature(state), true
end

function M.is_environment_scan_active(state)
  local status = state.environment_scan_status or {}
  return status.active == true or
    ENVIRONMENT_SCAN_ACTIVE_STATES[tostring(status.state or "")] == true
end

local function environment_scan_signature(state)
  local status = state.environment_scan_status or {}
  return table.concat({
    signature_part(status.state),
    signature_bool(status.active),
    signature_bool(status.cancel_requested),
    signature_part(status.scan_id),
    signature_part(status.generation),
    signature_part(status.direction_index),
    signature_part(status.directions_completed),
    signature_part(status.directions_total),
    signature_part(status.warmup_completed),
    signature_part(status.warmup_total),
    signature_part(status.upload_state),
    signature_part(status.last_error),
    signature_part(status.upload_error),
    signature_part(status.package_length),
    signature_part(status.package_sha256),
    signature_part(status.scene_result_path),
    signature_part(state.environment_scan_error),
  }, "")
end

function M.apply_environment_scan_status(state, result)
  if type(result) ~= "table" or type(result.state) ~= "string" or
      type(result.active) ~= "boolean" then
    state.environment_scan_error = "LOCAL_SCAN_STATUS_INVALID"
    return false
  end
  local before = environment_scan_signature(state)
  state.environment_scan_status = copy_table(result)
  state.environment_scan_error = nil
  return before ~= environment_scan_signature(state)
end

function M.refresh_environment_scan_status(state, now_ms, force)
  local period = M.is_environment_scan_active(state) and
    ENVIRONMENT_SCAN_ACTIVE_PERIOD_MS or ENVIRONMENT_SCAN_IDLE_PERIOD_MS
  if not force and
      now_ms - state.environment_scan_last_poll_ms < period then
    return false, false
  end
  state.environment_scan_last_poll_ms = now_ms
  local before = environment_scan_signature(state)
  local ok, result, err = call_environment_scan("status")
  if not ok then
    state.environment_scan_error = bounded_text(err)
    return before ~= environment_scan_signature(state), true
  end
  return M.apply_environment_scan_status(state, result), true
end

function M.open_environment_scan(state)
  state.environment_scan_view_requested = true
  state.environment_scan_error = nil
end

function M.close_environment_scan(state)
  state.environment_scan_view_requested = false
end

function M.open_task_decision_prepare(state)
  if M.is_task_decision_active(state) then
    state.home_hint = "正在进行任务决策，请稍候"
    return false
  end
  if state.training_state ~= "idle" then
    state.home_hint = "训练正在进行，暂不能重新决策"
    return false
  end
  local ok, result = call_task_decision("scene_status")
  if not ok or type(result) ~= "table" then
    state.home_hint = "环境结果不可用，请重新扫描"
    return false
  end
  if result.available ~= true then
    state.home_hint = result.code == "missing" and
      "暂无环境分析结果，请先扫描环境" or
      "环境结果不可用，请重新扫描"
    return false
  end
  state.environment_scan_view_requested = false
  state.page = "task_decision_prepare"
  return true
end

local function task_decision_signature(state)
  local status = state.task_decision_status or {}
  local bbox = status.target_bbox_xyxy or {}
  return table.concat({
    signature_part(status.state),
    signature_bool(status.active),
    signature_part(status.trace_stage),
    signature_part(status.elapsed_sec),
    signature_part(status.trace_stage_seq),
    signature_bool(status.trace_active),
    signature_part(status.trace_failure_code),
    signature_part(status.generation),
    signature_part(status.decision_id),
    signature_part(status.source_scan_id),
    signature_part(status.source_scene_sha256),
    signature_part(status.task_id),
    signature_part(status.task_name),
    signature_part(status.reason),
    signature_part(status.score),
    signature_bool(status.target_available),
    signature_part(status.target_object_id),
    signature_part(status.target_class),
    signature_part(status.target_source_frame_id),
    signature_part(status.target_source_frame_sequence),
    signature_part(bbox[1]),
    signature_part(bbox[2]),
    signature_part(bbox[3]),
    signature_part(bbox[4]),
    geometry_signature(status.target_geometry),
    signature_bool(status.retry_used),
    signature_part(status.last_error),
    signature_part(state.task_decision_error),
    signature_bool(state.task_decision_view_requested),
    signature_part(state.task_decision_last_seen_generation),
    signature_part(state.task_decision_pending_generation),
    signature_part(state.task_decision_pending_target),
    signature_part(state.task_decision_presented_generation),
    signature_part(state.task_decision_presented_target),
  }, "")
end

function M.is_task_decision_active(state)
  local status = state.task_decision_status or {}
  return status.active == true or
    TASK_DECISION_ACTIVE_STATES[tostring(status.state or "")] == true
end

local TASK_DECISION_SAFE_PAGES = {
  home = true,
  task_decision_prepare = true,
  task_decision_progress = true,
  task_decision_result = true,
  task_decision_details = true,
  task_decision_reasoning = true,
}

local function task_decision_target(status)
  local decision_state = tostring((status or {}).state or "IDLE")
  if TASK_DECISION_ACTIVE_STATES[decision_state] then
    return "progress"
  end
  if decision_state == "SELECTED" or decision_state == "FAILED" or
      decision_state == "CANCELLED" then
    return "result"
  end
  return nil
end

function M.is_task_decision_safe_page(state)
  return TASK_DECISION_SAFE_PAGES[tostring(state.page or "home")] == true and
    state.end_confirm_open ~= true and
    not ACTIVE_STATES[state.training_state] and
    not M.is_environment_scan_active(state) and
    not M.is_person_follow_active(state)
end

local function observe_task_decision_generation(state, result)
  local generation = nonnegative_integer(result.generation) or 0
  if generation == 0 then
    return
  end
  state.task_decision_last_seen_generation = math.max(
    state.task_decision_last_seen_generation or 0, generation)
  local target = task_decision_target(result)
  if not target then
    return
  end
  local presented_generation =
    state.task_decision_presented_generation or 0
  local already_presented = generation < presented_generation or
    (generation == presented_generation and
     target == state.task_decision_presented_target)
  if already_presented then
    return
  end
  if state.task_decision_pending_generation ~= generation or
      state.task_decision_pending_target ~= target then
    state.task_decision_pending_generation = generation
    state.task_decision_pending_target = target
    if not M.is_task_decision_safe_page(state) then
      print(string.format(
        "TASK_DECISION_UI_SYNC generation=%d state=%s action=pending page=%s",
        generation, tostring(result.state), tostring(state.page)))
    end
  end
end

function M.mark_task_decision_presented(state, page_id)
  local target = page_id == "task_decision_progress" and "progress" or
    (page_id == "task_decision_result" and "result" or nil)
  if not target or state.task_decision_pending_target ~= target or
      (state.task_decision_pending_generation or 0) == 0 then
    return false
  end
  local generation = state.task_decision_pending_generation
  state.task_decision_presented_generation = generation
  state.task_decision_presented_target = target
  state.task_decision_pending_generation = 0
  state.task_decision_pending_target = ""
  state.task_decision_view_requested = true
  print(string.format(
    "TASK_DECISION_UI_SYNC generation=%d state=%s action=%s page=%s",
    generation, tostring((state.task_decision_status or {}).state), target,
    tostring(page_id)))
  return true
end

function M.apply_task_decision_status(state, result)
  if type(result) ~= "table" or type(result.state) ~= "string" or
      type(result.active) ~= "boolean" then
    state.task_decision_error = "LOCAL_TASK_DECISION_STATUS_INVALID"
    return false
  end
  local before = task_decision_signature(state)
  state.task_decision_status = copy_table(result)
  state.task_decision_error = nil
  observe_task_decision_generation(state, result)
  if result.state == "SELECTED" then
    state.selected_available = true
    state.selected_decision_id = tostring(result.decision_id or "")
    state.selected_task_id = tostring(result.task_id or "")
    state.selected_task_name = tostring(result.task_name or "")
    state.selected_reason = ui_text.sanitize(result.reason or "")
    state.selected_score = tonumber(result.score) or 0
    state.selected_target_available = result.target_available == true
    state.selected_target_object_id =
      tostring(result.target_object_id or "")
    state.selected_target_class = tostring(result.target_class or "")
    state.selected_target_source_frame_id =
      tostring(result.target_source_frame_id or "")
    state.selected_target_source_frame_sequence =
      tonumber(result.target_source_frame_sequence) or 0
    state.selected_target_bbox_xyxy =
      copy_table(result.target_bbox_xyxy or {})
    state.selected_target_geometry = copy_table(
      result.target_geometry or { available = false, valid = false })
    state.decision_generation = tonumber(result.generation)
    state.home_hint = "训练任务已就绪"
  elseif result.state == "FAILED" or result.state == "CANCELLED" then
    state.home_hint = state.selected_available and
      "决策失败，已保留原任务" or "决策失败，暂无可保留任务"
  end
  return before ~= task_decision_signature(state)
end

function M.refresh_task_decision_status(state, now_ms, force)
  local period = M.is_task_decision_active(state) and
    TASK_DECISION_ACTIVE_PERIOD_MS or TASK_DECISION_IDLE_PERIOD_MS
  if not force and
      now_ms - state.task_decision_last_poll_ms < period then
    return false, false
  end
  state.task_decision_last_poll_ms = now_ms
  local before = task_decision_signature(state)
  local ok, result, err = call_task_decision("status")
  if not ok then
    state.task_decision_error = bounded_text(err)
    return before ~= task_decision_signature(state), true
  end
  return M.apply_task_decision_status(state, result), true
end

function M.start_task_decision(state, now_ms)
  if state.action_inflight then
    return false, nil, "ACTION_IN_FLIGHT"
  end
  if state.training_state ~= "idle" then
    state.home_hint = "训练正在进行，暂不能重新决策"
    return false, nil, state.home_hint
  end
  state.action_inflight = true
  local ok, result, err = call_task_decision("start")
  state.action_inflight = false
  if ok and type(result) == "table" then
    state.home_hint = state.selected_available and
      "正在重新决策，当前任务仍保留" or "正在进行任务决策"
    M.apply_task_decision_status(state, result)
  end
  if not ok then
    state.task_decision_error = bounded_text(
      type(result) == "table" and result.last_error or err)
    state.task_decision_view_requested = false
    state.task_decision_pending_generation = 0
    state.task_decision_pending_target = ""
  end
  return ok, result, ok and nil or state.task_decision_error
end

function M.close_task_decision(state)
  state.task_decision_view_requested = false
  state.task_decision_error = nil
end

function M.get_task_decision_view(state)
  local status = state.task_decision_status or {}
  local current = tostring(status.state or "IDLE")
  local trace_fallback = {
    IDLE = "IDLE",
    PREPARING = "PREPARING",
    DECIDING = "MODEL_WAIT",
    VALIDATING = "VALIDATING",
    COMMITTING = "COMMITTING",
    SELECTED = "SUCCESS",
    FAILED = "FAILED",
    CANCELLED = "FAILED",
  }
  local trace_stage = tostring(status.trace_stage or trace_fallback[current] or
    "IDLE")
  local trace_text = {
    IDLE = "正在启动任务决策…",
    PREPARING = "正在启动任务决策…",
    INPUT_READY = "正在分析环境信息…",
    CONTEXT_READY = "正在生成候选任务…",
    MODEL_WAIT = "DeepSeek 正在分析候选任务…",
    TOOL_CALL = "正在校验任务方案…",
    VALIDATING = "正在校验任务方案…",
    COMMITTING = "正在生成最终方案…",
    SUCCESS = "决策完成",
    FAILED = "决策失败",
  }
  local trace_completed_count = {
    IDLE = 0,
    PREPARING = 0,
    INPUT_READY = 1,
    CONTEXT_READY = 2,
    MODEL_WAIT = 3,
    TOOL_CALL = 4,
    VALIDATING = 4,
    COMMITTING = 5,
    SUCCESS = 5,
    FAILED = 0,
  }
  local elapsed_sec = math.min(999,
    nonnegative_integer(status.elapsed_sec) or 0)
  local failed = current == "FAILED" or current == "CANCELLED"
  local preserved = failed and state.selected_available == true
  return {
    state = current,
    trace_stage = trace_stage,
    stage_text = trace_text[trace_stage] or "正在处理任务决策",
    completed_stage_count = trace_completed_count[trace_stage] or 0,
    elapsed_sec = elapsed_sec,
    decision_id = tostring(status.decision_id or ""),
    generation = nonnegative_integer(status.generation) or 0,
    active = M.is_task_decision_active(state),
    title = "正在生成康复任务",
    detail = trace_text[trace_stage] or "正在处理任务决策",
    selected = current == "SELECTED",
    failed = failed,
    preserved = preserved,
    has_task = current == "SELECTED" or preserved,
    task_id = preserved and state.selected_task_id or
      tostring(status.task_id or ""),
    task_name = preserved and state.selected_task_name or
      tostring(status.task_name or ""),
    reason = preserved and state.selected_reason or
      tostring(status.reason or ""),
    score = preserved and state.selected_score or
      (tonumber(status.score) or 0),
    target_available = preserved and state.selected_target_available or
      status.target_available == true,
    target_object_id = preserved and state.selected_target_object_id or
      tostring(status.target_object_id or ""),
    target_class = preserved and state.selected_target_class or
      tostring(status.target_class or ""),
    bbox = preserved and copy_table(state.selected_target_bbox_xyxy) or
      copy_table(status.target_bbox_xyxy or {}),
    geometry = preserved and copy_table(state.selected_target_geometry) or
      copy_table(status.target_geometry or
        { available = false, valid = false }),
    error = bounded_text(state.task_decision_error or
      (status.last_error ~= "none" and status.last_error or "")),
  }
end

function M.start_environment_scan(state, now_ms)
  if state.action_inflight then
    return false, nil, "ACTION_IN_FLIGHT"
  end
  state.action_inflight = true
  local ok, result, err = call_environment_scan("start")
  state.action_inflight = false
  if type(result) == "table" and type(result.state) == "string" then
    M.apply_environment_scan_status(state, result)
  else
    M.refresh_environment_scan_status(state, now_ms, true)
  end
  if ok or (type(result) == "table" and result.busy == true) then
    return true, result, nil
  end
  state.environment_scan_error = bounded_text(err)
  return false, result, state.environment_scan_error
end

function M.cancel_environment_scan(state, now_ms)
  if state.action_inflight then
    return false, nil, "ACTION_IN_FLIGHT"
  end
  state.action_inflight = true
  local ok, result, err = call_environment_scan("cancel")
  state.action_inflight = false
  if type(result) == "table" and type(result.state) == "string" then
    M.apply_environment_scan_status(state, result)
  else
    M.refresh_environment_scan_status(state, now_ms, true)
  end
  if not ok then
    state.environment_scan_error = bounded_text(err)
  end
  return ok, result, ok and nil or state.environment_scan_error
end

function M.get_environment_scan_view(state)
  local status = state.environment_scan_status or {}
  local scan_state = tostring(status.state or "IDLE")
  local completed = tonumber(status.directions_completed) or 0
  local total = tonumber(status.directions_total) or 11
  local current = math.max(completed,
    (tonumber(status.direction_index) or 0) + 1)
  current = math.min(math.max(current, 0), total)
  local warmup = tonumber(status.warmup_completed) or 0
  local warmup_total = tonumber(status.warmup_total) or 5
  local title = "准备中"
  local detail = "正在准备环境扫描"

  if scan_state == "WARMING_CAMERA" then
    title = string.format("相机预热 %d/%d", warmup, warmup_total)
    detail = "正在准备相机"
  elseif scan_state == "ROTATING" then
    title = string.format("扫描中 %d/%d", current, total)
    detail = "正在转向下一个方向"
  elseif scan_state == "STOPPING" then
    title = string.format("扫描中 %d/%d", current, total)
    detail = "车辆正在停止"
  elseif scan_state == "SETTLING" then
    title = string.format("扫描中 %d/%d", current, total)
    detail = "等待车辆稳定"
  elseif scan_state == "CAPTURING" then
    title = string.format("扫描中 %d/%d", current, total)
    detail = "正在采集图像与深度"
  elseif scan_state == "WRITING" then
    title = string.format("扫描中 %d/%d", current, total)
    detail = "正在写入存储卡"
  elseif scan_state == "COMMITTING_SCAN" then
    title = string.format("扫描中 %d/%d", total, total)
    detail = "正在提交完整扫描"
  elseif scan_state == "UPLOADING" then
    title = "正在上传环境数据"
    detail = "11 组 RGB+ToF 已采集完成，正在上传分析服务。"
  elseif scan_state == "ANALYZING" then
    title = "正在分析环境"
    detail = "正在分析环境，请稍候"
  elseif scan_state == "DECISION_READY" then
    title = "环境分析完成"
    detail = "环境分析完成，可以进行任务决策"
  elseif scan_state == "CANCELLING" then
    title = "正在停止"
    detail = "车辆已收到停止请求"
  elseif scan_state == "CANCELLED" then
    title = "已取消"
    detail = "环境扫描已取消"
  elseif scan_state == "FAILED" then
    title = "环境分析失败"
    detail = bounded_text(status.last_error ~= "" and status.last_error or
      status.upload_error ~= "" and status.upload_error or
      state.environment_scan_error or "未知原因")
  elseif scan_state == "ACQUIRING_LEASE" then
    detail = "正在准备车辆与相机"
  elseif scan_state == "IDLE" and state.environment_scan_error then
    title = "扫描失败"
    detail = bounded_text(state.environment_scan_error)
  end

  return {
    state = scan_state,
    title = title,
    detail = detail,
    scan_id = tostring(status.scan_id or ""),
    completed = completed,
    total = total,
    active = M.is_environment_scan_active(state),
    can_stop = M.is_environment_scan_active(state) and
      scan_state ~= "CANCELLING",
    can_decide = scan_state == "DECISION_READY",
  }
end

function M.refresh_from_agent(state, now_ms, force)
  local changed = false
  local polled = false
  local scan_changed, scan_polled = M.refresh_environment_scan_status(
    state, now_ms, force)
  changed = changed or scan_changed
  polled = polled or scan_polled

  local decision_changed, decision_polled =
    M.refresh_task_decision_status(state, now_ms, force)
  changed = changed or decision_changed
  polled = polled or decision_polled

  local follow_changed, follow_polled = M.refresh_person_follow_status(state,
    now_ms, force)
  changed = changed or follow_changed
  polled = polled or follow_polled

  local status_changed, status_polled = M.refresh_training_status(state,
    now_ms, force)
  changed = changed or status_changed
  polled = polled or status_polled

  if state.page == "home" or force then
    local selected_changed, selected_polled = M.refresh_selected(state,
      now_ms, force)
    changed = changed or selected_changed
    polled = polled or selected_polled
  end
  if state.page == "home" then
    local fatigue_changed, fatigue_polled = M.refresh_fatigue(state,
      now_ms, force)
    changed = changed or fatigue_changed
    polled = polled or fatigue_polled
  end
  if state.page == "result" or state.bound_result_session_id ~= "" then
    local score_changed, score_polled = M.refresh_score(state, now_ms, force)
    changed = changed or score_changed
    polled = polled or score_polled
  end
  return changed, polled
end

function M.start_person_follow(state, now_ms)
  if state.action_inflight then
    return false, nil, "ACTION_IN_FLIGHT"
  end
  state.action_inflight = true
  local ok, result, err = M.call_agent_capability(state,
    "person_follow_start", {})
  state.action_inflight = false

  M.refresh_person_follow_status(state, now_ms, true)
  if M.is_person_follow_active(state) then
    state.person_follow_manual_confirm = false
    state.person_follow_last_ui_error = nil
    return true, result, nil
  end

  local reason
  if not ok then
    reason = response_reason(result, err)
  else
    reason = state.person_follow_status_error or
      "PERSON_FOLLOW_START_NOT_RUNNING"
  end
  state.person_follow_last_ui_error = reason
  return false, result, reason
end

function M.stop_person_follow(state, now_ms)
  if state.action_inflight then
    return false, nil, "ACTION_IN_FLIGHT"
  end
  state.action_inflight = true
  local ok, result, err = M.call_agent_capability(state,
    "person_follow_stop", {})
  state.action_inflight = false

  M.refresh_person_follow_status(state, now_ms, true)
  if M.is_person_follow_stopped(state) then
    state.person_follow_manual_confirm = false
    state.person_follow_last_ui_error = nil
    return true, result, nil
  end

  local reason
  if not ok then
    reason = response_reason(result, err)
  else
    reason = state.person_follow_status_error or
      "PERSON_FOLLOW_STOP_NOT_IDLE"
  end
  state.person_follow_last_ui_error = reason
  return false, result, reason
end

function M.start_training(state, now_ms)
  if state.action_inflight then
    return false, nil, "ACTION_IN_FLIGHT"
  end
  state.action_inflight = true
  local ok, result, err = M.call_agent_capability(state,
    "rehab_training_start_selected", {})
  state.action_inflight = false

  if ok and type(result) == "table" and result.training_started == true then
    local session_id = tostring(result.session_id or "")
    bind_active_session(state, session_id, result.task_name)
    state.training_state = tostring(result.state or "recording")
    state.rows_written = tonumber(result.rows_written) or 0
    state.training_error = nil
    state.home_hint = "训练已开始"
    state.last_status_poll_ms = -STATUS_ACTIVE_PERIOD_MS
    M.refresh_training_status(state, now_ms, true)
    return true, result, nil
  end

  local reason = training_reason_text(response_reason(result, err))
  state.training_error = reason
  state.home_hint = reason
  if type(result) == "table" and result.code == "already_recording" then
    M.refresh_training_status(state, now_ms, true)
    if ACTIVE_STATES[state.training_state] then
      return true, result, nil
    end
  end
  return false, result, reason
end

function M.stop_training(state, now_ms)
  if state.action_inflight then
    return false, nil, "ACTION_IN_FLIGHT"
  end
  local fallback_session = state.current_session_id ~= "" and
    state.current_session_id or state.bound_training_session_id
  state.action_inflight = true
  local ok, result, err = M.call_agent_capability(state,
    "rehab_training_stop", { reason = "user_stop" })
  state.action_inflight = false

  local finalized_session = type(result) == "table" and
    session_id_from_path(result.session_path) or ""
  if finalized_session == "" then
    finalized_session = fallback_session
  end
  if ok and type(result) == "table" and result.accepted == true then
    state.training_state = tostring(result.state or "idle")
    state.rows_written = tonumber(result.rows_written) or state.rows_written
    state.queue_drop = tonumber(result.queue_drop_count) or state.queue_drop
    state.sd_write_error = tonumber(result.write_error_count) or state.sd_write_error
    bind_result_session(state, finalized_session, result.task_name)
    state.last_status_poll_ms = -STATUS_IDLE_PERIOD_MS
    M.refresh_training_status(state, now_ms, true)
    return true, result, nil
  end

  M.refresh_training_status(state, now_ms, true)
  if state.training_state == "idle" and
      state.last_completed_session_id == fallback_session and
      fallback_session ~= "" then
    bind_result_session(state, fallback_session,
      state.last_completed_task_name)
    return true, result, nil
  end
  local reason = response_reason(result, err)
  state.training_error = reason
  return false, result, reason
end

function M.clear_result_binding(state)
  state.bound_result_session_id = ""
  state.result_task_name = ""
  state.score_state = "missing"
  state.score_result = nil
  state.score_error = nil
  state.end_confirm_open = false
end

function M.route_for_state(state)
  if M.is_environment_scan_active(state) or
      state.environment_scan_view_requested or
      state.page == "environment_scan" then
    return "environment_scan"
  end
  if M.is_person_follow_active(state) then
    return "person_follow"
  end
  if ACTIVE_STATES[state.training_state] then
    return "training"
  end
  if state.bound_result_session_id ~= "" then
    return "result"
  end
  if state.page == "person_follow" and
      (state.person_follow_manual_confirm or
       (state.person_follow_status and
         state.person_follow_status.state == "ERROR")) then
    return "person_follow"
  end
  if state.page == "task_decision_details" then
    return "task_decision_details"
  end
  if state.page == "task_decision_reasoning" then
    return "task_decision_reasoning"
  end
  if M.is_task_decision_safe_page(state) and
      (state.task_decision_pending_generation or 0) > 0 then
    return state.task_decision_pending_target == "result" and
      "task_decision_result" or "task_decision_progress"
  end
  if state.task_decision_view_requested then
    local target = task_decision_target(state.task_decision_status)
    if target then
      return target == "result" and
        "task_decision_result" or "task_decision_progress"
    end
  end
  if state.page == "task_decision_prepare" then
    return "task_decision_prepare"
  end
  return "home"
end

function M.is_start_enabled(state)
  return state.selected_available == true and
    state.training_state == "idle" and not state.action_inflight
end

function M.is_stop_enabled(state)
  return ACTIVE_STATES[state.training_state] and not state.action_inflight
end

function M.is_score_pending(state)
  return SCORE_PENDING_STATES[state.score_state] == true or
    state.score_state == "missing"
end

function M.get_result_feedback(state)
  if state.score_state == "succeeded" then
    local result = state.score_result or {}
    return string.format("评分完成；数据状态：%s；代偿动作：%d",
      tostring(result.data_quality_level or "--"),
      tonumber(result.compensation_action_count) or 0)
  end
  if state.score_state == "failed" or state.score_state == "invalid" then
    return state.score_error or "评分失败"
  end
  return "训练数据已保存，评分处理中"
end

function M.build_ui_signature(state, page_id)
  page_id = page_id or state.page or "home"
  local parts = {
    signature_part(page_id),
    signature_part(state.training_state),
    signature_part(state.bound_training_session_id),
    signature_part(state.bound_result_session_id),
    signature_bool(state.action_inflight),
    signature_part(state.last_error),
  }
  if page_id == "home" then
    parts[#parts + 1] = signature_bool(state.selected_available)
    parts[#parts + 1] = signature_part(state.selected_decision_id)
    parts[#parts + 1] = signature_part(state.selected_task_id)
    parts[#parts + 1] = signature_part(state.selected_task_name)
    parts[#parts + 1] = signature_part(state.suggested_distance_cm)
    parts[#parts + 1] =
      geometry_signature(state.selected_target_geometry)
    parts[#parts + 1] = signature_part(state.decision_generation)
    parts[#parts + 1] = signature_part(state.home_hint)
    parts[#parts + 1] = signature_part(state.handheld_requested_mode)
    parts[#parts + 1] = signature_part(state.handheld_mode_switch_result)
    parts[#parts + 1] = signature_part(state.handheld_mode_error)
    parts[#parts + 1] = signature_bool(state.handheld_mode_switch_inflight)
    parts[#parts + 1] = environment_scan_signature(state)
    parts[#parts + 1] = fatigue_signature(state)
  elseif page_id == "training" then
    parts[#parts + 1] = signature_part(state.current_task_name)
    parts[#parts + 1] = signature_part(state.current_session_id)
    parts[#parts + 1] = signature_part(state.rows_written)
    parts[#parts + 1] = signature_part(state.queue_drop)
    parts[#parts + 1] = signature_part(state.sd_write_error)
    parts[#parts + 1] = signature_bool(state.required_sensors_ok)
    parts[#parts + 1] = signature_bool(state.node1 and state.node1.online)
    parts[#parts + 1] = signature_bool(state.node2 and state.node2.online)
    parts[#parts + 1] = signature_part(state.training_error)
    parts[#parts + 1] = signature_bool(state.end_confirm_open)
  elseif page_id == "result" then
    parts[#parts + 1] = signature_part(state.result_task_name)
    parts[#parts + 1] = signature_part(state.score_state)
    parts[#parts + 1] = signature_part(state.score_error)
    local result = state.score_result or {}
    for _, key in ipairs({ "average_score", "max_score", "min_score",
      "consistency_score", "repetition_count", "target_repetition_count",
      "data_quality_level", "compensation_detected",
      "compensation_action_count", "completed_at" }) do
      parts[#parts + 1] = signature_part(result[key])
    end
    for _, warning in ipairs(result.warnings or {}) do
      parts[#parts + 1] = signature_part(warning)
    end
  elseif page_id == "person_follow" then
    parts[#parts + 1] = person_follow_snapshot_signature(state)
    parts[#parts + 1] = signature_bool(state.person_follow_manual_confirm)
    parts[#parts + 1] = signature_bool(
      state.person_follow_starting_inflight)
    parts[#parts + 1] = signature_bool(
      state.person_follow_stopping_inflight)
    parts[#parts + 1] = signature_part(state.person_follow_last_ui_error)
  elseif page_id == "environment_scan" then
    parts[#parts + 1] = environment_scan_signature(state)
  elseif page_id == "task_decision_prepare" or
      page_id == "task_decision_progress" or
      page_id == "task_decision_result" or
      page_id == "task_decision_details" or
      page_id == "task_decision_reasoning" then
    parts[#parts + 1] = task_decision_signature(state)
    parts[#parts + 1] = signature_bool(state.selected_available)
    parts[#parts + 1] = signature_part(state.selected_task_id)
    parts[#parts + 1] = signature_part(state.selected_task_name)
    parts[#parts + 1] = signature_part(state.selected_reason)
    parts[#parts + 1] = signature_part(state.selected_score)
  end
  return table.concat(parts, "")
end

M.ACTIVE_STATES = ACTIVE_STATES
M.PERSON_FOLLOW_ACTIVE_STATES = PERSON_FOLLOW_ACTIVE_STATES
M.ENVIRONMENT_SCAN_ACTIVE_STATES = ENVIRONMENT_SCAN_ACTIVE_STATES
M.TASK_DECISION_ACTIVE_STATES = TASK_DECISION_ACTIVE_STATES
M.SCORE_PENDING_STATES = SCORE_PENDING_STATES
M.session_id_from_path = session_id_from_path

return M
