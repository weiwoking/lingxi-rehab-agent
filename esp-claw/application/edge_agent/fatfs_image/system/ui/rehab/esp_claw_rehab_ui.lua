local board_manager = require("board_manager")
local lvgl = require("lvgl")
local system = require("system")

local state_module = require("rehab_ui_state")
local theme = require("rehab_ui_theme")
local home_page = require("page_home")
local training_page = require("page_training")
local result_page = require("page_result")
local person_follow_page = require("page_person_follow")
local environment_scan_page = require("page_environment_scan")
local task_decision_prepare_page = require("page_task_decision_prepare")
local task_decision_progress_page = require("page_task_decision_progress")
local task_decision_result_page = require("page_task_decision_result")
local task_decision_details_page = require("page_task_decision_details")
local task_decision_reasoning_page = require("page_task_decision_reasoning")
local task_decision = require("rehab_task_decision_ui")

local pages = {
  home = home_page,
  training = training_page,
  result = result_page,
  person_follow = person_follow_page,
  environment_scan = environment_scan_page,
  task_decision_prepare = task_decision_prepare_page,
  task_decision_progress = task_decision_progress_page,
  task_decision_result = task_decision_result_page,
  task_decision_details = task_decision_details_page,
  task_decision_reasoning = task_decision_reasoning_page,
}

local M = {}

local app = {
  screen = nil,
  page_root = nil,
  page = nil,
  page_module = nil,
  pending_page = nil,
  state = state_module.new(),
  state_module = state_module,
  theme = theme,
  lvgl = lvgl,
  system = system,
  running = false,
  started = false,
  force_full_refresh = true,
  last_ui_signature_by_page = {},
  ui_refresh_in_progress = false,
  pending_action = nil,
  touch_registered = false,
  lvgl_initialized = false,
}

function app.navigate(page_name)
  if pages[page_name] and page_name ~= app.page then
    app.pending_page = page_name
  end
end

function app.exit_to_emote()
  app.running = false
end

local function queue_action(action_type, args)
  if app.pending_action or app.state.action_inflight then
    app.state.last_error = "操作正在处理中"
    app.force_full_refresh = true
    return false
  end
  app.pending_action = {
    type = action_type,
    args = args,
    requested_at_ms = system.millis(),
  }
  app.force_full_refresh = true
  if action_type == "set_handheld_debug" then
    print(string.format("REHAB_UI_ACTION_QUEUED type=%s requested_mode=%s",
      tostring(action_type), tostring(args and args.mode or "")))
  else
    print("REHAB_UI_ACTION_QUEUED type=" .. tostring(action_type))
  end
  return true
end

function app.queue_handheld_debug(enabled)
  local queued = queue_action("set_handheld_debug", {
    mode = enabled == true and "BLE" or "SERIAL",
  })
  if queued then
    app.state.handheld_mode_switch_inflight = true
  end
  return queued
end

function app.open_person_follow_confirm()
  app.state_module.open_person_follow_confirm(app.state)
  app.navigate("person_follow")
end

function app.queue_person_follow_start()
  if app.state_module.is_person_follow_active(app.state) then
    app.navigate("person_follow")
    return false
  end
  local queued = queue_action("start_person_follow")
  if queued then
    app.state.person_follow_starting_inflight = true
    app.state.person_follow_last_ui_error = nil
  end
  return queued
end

function app.queue_person_follow_stop()
  if app.state_module.is_person_follow_stopped(app.state) then
    app.navigate("home")
    return false
  end
  local queued = queue_action("stop_person_follow")
  if queued then
    app.state.person_follow_stopping_inflight = true
    app.state.person_follow_last_ui_error = nil
  end
  return queued
end

function app.open_environment_scan()
  app.state_module.open_environment_scan(app.state)
  if not app.state_module.is_environment_scan_active(app.state) then
    queue_action("start_environment_scan")
  end
  app.navigate("environment_scan")
end

function app.queue_environment_scan_cancel()
  if not app.state_module.is_environment_scan_active(app.state) then
    return false
  end
  return queue_action("cancel_environment_scan")
end

function app.return_environment_scan_home()
  app.state_module.close_environment_scan(app.state)
  app.navigate("home")
end

function app.open_task_decision_prepare()
  if app.state_module.open_task_decision_prepare(app.state) then
    app.navigate("task_decision_prepare")
    return true
  end
  app.force_full_refresh = true
  return false
end

function app.return_task_decision_prepare()
  app.state.page = "home"
  app.navigate("home")
end

function app.queue_task_decision_start()
  if queue_action("start_task_decision") then
    return true
  end
  return false
end

function app.queue_task_redecision()
  return app.queue_task_decision_start()
end

function app.return_task_decision_home()
  app.state_module.close_task_decision(app.state)
  app.state.page = "home"
  app.navigate("home")
end

function app.open_task_decision_details(source)
  app.details_source = source == "result" and "result" or "home"
  app.details_expected_decision_id = app.details_source == "result"
    and ((app.state.task_decision_status or {}).decision_id)
    or app.state.selected_decision_id
  app.navigate("task_decision_details")
end

function app.return_task_decision_details()
  local destination = app.details_source == "result"
    and "task_decision_result" or "home"
  app.details_expected_decision_id = nil
  app.navigate(destination)
end

function app.open_task_decision_reasoning(source)
  local expected_decision_id = source == "result"
    and tostring((app.state.task_decision_status or {}).decision_id or "")
    or nil
  if (source == "result" and expected_decision_id == "") or
      type(task_decision.reasoning_available) ~= "function" or
      task_decision.reasoning_available(expected_decision_id) ~= true then
    app.state.home_hint = "暂无AI分析记录"
    app.force_full_refresh = true
    return false
  end
  app.reasoning_source = source == "result" and "result" or "home"
  app.reasoning_expected_decision_id = expected_decision_id
  app.navigate("task_decision_reasoning")
  return true
end

function app.return_task_decision_reasoning()
  local destination = app.reasoning_source == "result"
    and "task_decision_result" or "home"
  app.reasoning_expected_decision_id = nil
  app.navigate(destination)
end

function app.queue_start_training()
  if app.state_module.is_task_decision_active(app.state) then
    app.state.home_hint = "正在重新决策，请稍候"
    app.force_full_refresh = true
    return false
  end
  return queue_action("start_training")
end

function app.queue_stop_training()
  if not app.state_module.is_stop_enabled(app.state) then
    app.state.training_error = "当前状态不能结束训练"
    app.force_full_refresh = true
    return false
  end
  app.state.end_confirm_open = false
  return queue_action("stop_training")
end

local function current_page_id()
  return app.page or app.state.page or "home"
end

local function refresh_current_page(force)
  if force then
    app.force_full_refresh = true
  end
  if not app.running or not app.page_module or not app.page_module.refresh then
    return false
  end
  if app.ui_refresh_in_progress then
    return false
  end

  local page_id = current_page_id()
  local signature = app.state_module.build_ui_signature(app.state, page_id)
  if not app.force_full_refresh and
      app.last_ui_signature_by_page[page_id] == signature then
    return false
  end

  app.ui_refresh_in_progress = true
  local ok, err = pcall(app.page_module.refresh, app)
  app.ui_refresh_in_progress = false
  if not ok then
    app.state.last_error = "页面刷新失败：" .. tostring(err)
    print("REHAB_UI_RENDER_FAILED page=" .. page_id ..
      " reason=" .. tostring(err))
    return false
  end
  app.last_ui_signature_by_page[page_id] =
    app.state_module.build_ui_signature(app.state, page_id)
  app.force_full_refresh = false
  return true
end

function app.refresh_current_page(force)
  return refresh_current_page(force)
end

local function finish_action(action, ok, reason)
  if action and action.type == "set_handheld_debug" then
    print(string.format(
      "REHAB_UI_ACTION_DONE type=%s ok=%s requested_mode=%s reason=%s",
      tostring(action.type), tostring(ok == true),
      tostring(action.args and action.args.mode or ""),
      tostring(reason or "OK")))
  else
    print(string.format("REHAB_UI_ACTION_DONE type=%s ok=%s reason=%s",
      tostring(action and action.type), tostring(ok == true),
      tostring(reason or "OK")))
  end
  app.pending_action = nil
  if action and action.type == "set_handheld_debug" then
    app.state.handheld_mode_switch_inflight = false
  elseif action and action.type == "start_person_follow" then
    app.state.person_follow_starting_inflight = false
  elseif action and action.type == "stop_person_follow" then
    app.state.person_follow_stopping_inflight = false
  end
  app.force_full_refresh = true
end

local function process_pending_action(now_ms)
  local action = app.pending_action
  if not action then
    return false
  end

  local ok, result, err
  if action.type == "start_training" then
    ok, result, err = app.state_module.start_training(app.state, now_ms)
  elseif action.type == "stop_training" then
    ok, result, err = app.state_module.stop_training(app.state, now_ms)
  elseif action.type == "set_handheld_debug" then
    ok, result, err = app.state_module.set_handheld_debug(app.state,
      action.args and action.args.mode)
  elseif action.type == "start_person_follow" then
    ok, result, err = app.state_module.start_person_follow(app.state, now_ms)
  elseif action.type == "stop_person_follow" then
    ok, result, err = app.state_module.stop_person_follow(app.state, now_ms)
  elseif action.type == "start_environment_scan" then
    ok, result, err = app.state_module.start_environment_scan(
      app.state, now_ms)
  elseif action.type == "cancel_environment_scan" then
    ok, result, err = app.state_module.cancel_environment_scan(
      app.state, now_ms)
  elseif action.type == "start_task_decision" then
    ok, result, err = app.state_module.start_task_decision(
      app.state, now_ms)
  else
    finish_action(action, false, "UNKNOWN_ACTION")
    return true
  end

  finish_action(action, ok, err)
  local route = app.state_module.route_for_state(app.state)
  if route ~= app.page then
    app.navigate(route)
  else
    refresh_current_page(true)
  end
  return true
end

local function show_page(page_name)
  local next_page = pages[page_name]
  if not next_page then
    error("unknown rehab page: " .. tostring(page_name))
  end
  local previous_page = app.page
  if app.page_module then
    pcall(app.page_module.destroy)
  end

  app.page = page_name
  app.state.page = page_name
  app.state_module.mark_task_decision_presented(app.state, page_name)
  app.page_module = next_page
  app.pending_page = nil
  app.force_full_refresh = true
  if page_name == "home" and previous_page ~= nil and
      previous_page ~= "home" then
    app.state_module.refresh_fatigue(app.state, system.millis(), true)
  end
  local ok, root_or_err = pcall(next_page.create, app)
  if not ok then
    print("REHAB_UI_RENDER_FAILED page=" .. page_name ..
      " reason=" .. tostring(root_or_err))
    error(root_or_err)
  end
  app.page_root = root_or_err
  refresh_current_page(true)
  print("REHAB_UI_PAGE_" .. string.upper(page_name))
end

local function cleanup_ui()
  app.running = false
  app.pending_page = nil
  app.pending_action = nil

  if app.page_module then
    pcall(app.page_module.destroy)
    app.page_module = nil
    app.page_root = nil
  end
  if app.touch_registered then
    local ok, err = pcall(lvgl.indev_unregister, "touch")
    if not ok then
      print("touch unregister failed: " .. tostring(err))
    end
    app.touch_registered = false
  end
  if app.screen and app.screen:is_valid() then
    pcall(function() app.screen:clean() end)
  end
  app.screen = nil
  if app.lvgl_initialized then
    local ok, err = pcall(lvgl.deinit)
    if not ok then
      print("lvgl.deinit failed: " .. tostring(err))
    end
    app.lvgl_initialized = false
  end
end

local function run_ui()
  local panel, io, width, height, panel_if =
    board_manager.get_display_lcd_params("display_lcd")
  if panel == nil then
    error("display_lcd is unavailable: " .. tostring(io))
  end
  if width ~= theme.screen_width or height ~= theme.screen_height then
    error(string.format("rehab UI requires %dx%d, got %dx%d",
      theme.screen_width, theme.screen_height, width, height))
  end

  -- lvgl.init acquires DISPLAY_ARBITER_OWNER_LUA. lvgl.deinit releases it,
  -- returning ownership to the existing emote UI.
  lvgl.init(panel, io, width, height, panel_if, {
    buffer_lines = 40,
    tick_ms = 5,
    task_period_ms = 10,
  })
  app.lvgl_initialized = true

  local ok, err = pcall(function()
    local touch, touch_err = board_manager.get_lcd_touch_handle("lcd_touch")
    if touch == nil then
      error("lcd_touch is unavailable: " .. tostring(touch_err))
    end
    app.touch_registered = lvgl.indev_register("touch", touch)
    if not app.touch_registered then
      error("failed to register lcd_touch as LVGL input")
    end

    app.screen = lvgl.create_screen()
    app.screen:set_style({ bg_color = theme.bg })
    local font_ok = theme.load_fonts(lvgl)
    if not font_ok then
      error("rehab font load failed: " .. tostring(theme.font_error))
    end

    -- Cold boot only routes to Training when the authoritative status is
    -- active. A historical last_completed_session never opens Result here.
    app.state_module.refresh_from_agent(app.state, system.millis(), true)
    show_page(app.state_module.route_for_state(app.state))
    app.screen:load()
    print("REHAB_UI_ENTRY_READY")

    while app.running do
      local now_ms = system.millis()
      lvgl.process_events(theme.event_slice_ms)

      local action_used = process_pending_action(now_ms)
      if not action_used then
        local refreshed = app.state_module.refresh_from_agent(app.state,
          now_ms, false)
        local route = app.state_module.route_for_state(app.state)
        if route ~= app.page then
          app.navigate(route)
        elseif refreshed then
          refresh_current_page(false)
        end
      end

      if app.pending_page then
        show_page(app.pending_page)
      end
      if app.running and app.page_module and app.page_module.tick then
        local tick_ok, tick_err = pcall(app.page_module.tick, app,
          system.millis())
        if not tick_ok then
          app.state.last_error = "页面事件失败：" .. tostring(tick_err)
          print("REHAB_UI_TICK_FAILED reason=" .. tostring(tick_err))
        end
      end
    end
  end)

  if not ok then
    error(err)
  end
end

function M.start()
  if app.started then
    print("REHAB_UI_ENTRY_READY status=ALREADY_STARTED")
    return "ALREADY_STARTED"
  end
  app.started = true
  app.running = true
  app.force_full_refresh = true
  local ok, err = pcall(run_ui)
  cleanup_ui()
  app.started = false

  if not ok then
    print("REHAB_UI_ENTRY_FAILED reason=" .. tostring(err))
    error(err)
  end
  return "STOPPED"
end

return M
