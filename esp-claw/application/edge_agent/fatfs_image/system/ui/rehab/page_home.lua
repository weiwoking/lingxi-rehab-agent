local lvgl = require("lvgl")
local geometry_ui = require("rehab_ui_geometry")
local task_decision = require("rehab_task_decision_ui")

local M = {}
local view = nil

local entry_source_text = {
  local_ui = "本地界面",
  wechat = "微信",
  console = "控制台",
  unknown = "未知",
}

local function selected_entry_source(decision_id)
  if type(task_decision.details) ~= "function" then return "未知" end
  local details = task_decision.details(decision_id)
  if type(details) ~= "table" or details.available ~= true then
    return "未知"
  end
  return entry_source_text[details.entry_source] or "未知"
end

local function remember(obj)
  view.objects[#view.objects + 1] = obj
  return obj
end

local function bind(obj, callback)
  local handle = obj:on("clicked", callback)
  view.events[#view.events + 1] = { obj = obj, handle = handle,
    callback = callback }
end

local function panel(parent, x, y, w, h, theme)
  return remember(lvgl.container(parent, {
    x = x, y = y, w = w, h = h,
    bg_color = theme.panel, bg_opa = 255,
    border_color = theme.border, border_width = 1,
    radius = theme.radius, pad = 0,
  }))
end

local function label(parent, text, x, y, color, w, h)
  return remember(lvgl.label(parent, {
    text = text, x = x, y = y, w = w, h = h,
    text_color = color, text_font = "rehab",
  }))
end

function M.create(app)
  local t = app.theme
  local state = app.state
  view = { objects = {}, events = {} }

  local root = remember(lvgl.container(app.screen, {
    x = 0, y = 0, w = t.screen_width, h = t.screen_height,
    bg_color = t.bg, bg_opa = 255, border_width = 0, radius = 0, pad = 0,
  }))
  view.root = root

  local header = panel(root, 0, 0, 1024, 72, t)
  header:set_style({ radius = 0 })
  label(header, "ESP-Claw 康复训练", 20, 14, t.accent, 360, 24)
  label(header, "本地真实训练", 20, 42, t.muted, 300, 22)
  view.system_state = label(header, "等待状态", 650, 24, t.warning, 190, 24)

  local intro = panel(root, 20, 88, 984, 112, t)
  label(intro, "当前训练任务", 18, 16, t.accent, 220, 24)
  view.task_name = label(intro, "尚未选择训练任务", 18, 52,
    t.text, 610, 34)
  view.distance = label(intro, "-- cm", 690, 52,
    t.muted, 270, 28)
  view.details_button = remember(lvgl.button(intro, {
    text = "决策详情", x = 820, y = 8, w = 140, h = 40,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.reasoning_button = remember(lvgl.button(intro, {
    text = "查看AI分析", x = 650, y = 8, w = 160, h = 40,
    bg_color = t.button_dark, text_color = t.muted,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))

  local task = panel(root, 20, 216, 440, 234, t)
  label(task, "任务", 18, 16, t.accent, 180, 24)
  label(task, "目标物体", 18, 62, t.muted, 120, 24)
  view.task_target = label(task, "--", 150, 62, t.text, 270, 24)
  label(task, "决策代次", 18, 108, t.muted, 120, 24)
  view.generation = label(task, "--", 150, 108, t.text, 270, 24)
  label(task, "任务来源", 18, 154, t.muted, 120, 24)
  view.task_source = label(task, "未知", 150, 154,
    t.text, 270, 44)

  local ready = panel(root, 476, 216, 244, 234, t)
  label(ready, "训练状态", 18, 16, t.accent, 180, 24)
  view.ready_value = label(ready, "不可开始", 18, 66,
    t.warning, 208, 36)
  view.ready_detail = label(ready, state.home_hint or "正在等待训练状态",
    18, 108, t.muted, 208, 44)
  view.scan_button = remember(lvgl.button(ready, {
    text = "扫描环境", x = 18, y = 166, w = 100, h = 50,
    bg_color = t.scan_idle, text_color = t.bg,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.decision_button = remember(lvgl.button(ready, {
    text = "决策任务", x = 126, y = 166, w = 100, h = 50,
    bg_color = t.accent, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))

  local fatigue = panel(root, 736, 216, 268, 234, t)
  label(fatigue, "Fatigue summary", 14, 14, t.accent, 230, 24)
  label(fatigue, "Fatigue", 14, 54, t.muted, 106, 24)
  label(fatigue, "Today", 138, 54, t.muted, 106, 24)
  view.fatigue_value = label(fatigue, "-- / 100", 14, 82,
    t.text, 106, 28)
  view.fatigue_today = label(fatigue, "0", 138, 82,
    t.text, 106, 28)
  label(fatigue, "Score", 14, 118, t.muted, 106, 24)
  label(fatigue, "Load", 138, 118, t.muted, 106, 24)
  view.fatigue_score = label(fatigue, "--", 14, 146,
    t.text, 106, 28)
  view.fatigue_load = label(fatigue, "--", 138, 146,
    t.text, 106, 28)
  view.fatigue_notice = label(fatigue, "Fatigue unavailable", 14, 190,
    t.warning, 236, 26)

  local footer = panel(root, 20, 466, 984, 114, t)
  view.hint = label(footer, state.home_hint or "正在等待训练状态",
    18, 10, t.muted, 946, 24)
  view.handheld_button = remember(lvgl.button(footer, {
    text = "启用手柄调试", x = 18, y = 42, w = 220, h = 56,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.follow_button = remember(lvgl.button(footer, {
    text = "追人模式", x = 252, y = 42, w = 180, h = 56,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.refresh_button = remember(lvgl.button(footer, {
    text = "刷新状态", x = 446, y = 42, w = 200, h = 56,
    bg_color = t.scan_idle, text_color = t.bg,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.start_button = remember(lvgl.button(footer, {
    text = "开始训练", x = 660, y = 42, w = 306, h = 56,
    bg_color = t.accent, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))

  bind(view.refresh_button, function()
    -- Touch callbacks only mark local polling due; Capability calls remain in
    -- the main loop.
    state.last_status_poll_ms = -1000
    state.last_selected_poll_ms = -5000
    state.fatigue_last_refresh_ms = -5000
    state.home_hint = "正在刷新状态"
    app.force_full_refresh = true
  end)
  bind(view.handheld_button, function()
    -- Queue only; the main UI loop performs the Capability/UART work.
    if app.queue_handheld_debug(state.handheld_requested_mode ~= "BLE") then
      view.handheld_button:set_text("处理中")
      view.handheld_button:set_style(t.button_style(t.button_dark, t.muted))
    end
  end)
  bind(view.follow_button, function()
    app.open_person_follow_confirm()
  end)
  bind(view.start_button, function()
    app.queue_start_training()
  end)
  bind(view.scan_button, function()
    app.open_environment_scan()
  end)
  bind(view.decision_button, function()
    app.open_task_decision_prepare()
  end)
  bind(view.details_button, function()
    app.open_task_decision_details("home")
  end)
  bind(view.reasoning_button, function()
    app.open_task_decision_reasoning("home")
  end)
  return root
end

function M.refresh(app)
  if not view then return end
  local t = app.theme
  local state = app.state
  local enabled = app.state_module.is_start_enabled(state)
  local fatigue = app.state_module.get_fatigue_summary(state)

  view.task_name:set_text(state.selected_available and
    state.selected_task_name or "尚未选择训练任务")
  view.task_target:set_text(state.selected_available and
    geometry_ui.object_class(state.selected_target_class) or "--")
  view.distance:set_text("目标高度：" ..
    geometry_ui.format_height(
      state.selected_target_geometry, "object_height_mm"))
  view.generation:set_text(state.decision_generation and
    tostring(state.decision_generation) or "--")
  view.task_source:set_text(state.selected_available and
    selected_entry_source(state.selected_decision_id) or "尚未决策")
  view.hint:set_text(state.handheld_mode_error or state.home_hint or
    "状态暂不可用")
  view.ready_detail:set_text(state.home_hint or "状态暂不可用")
  view.system_state:set_text("训练状态：" ..
    tostring(state.training_state or "idle"))
  view.fatigue_value:set_text(fatigue.fatigue .. " / 100")
  view.fatigue_today:set_text(fatigue.today)
  view.fatigue_score:set_text(fatigue.score)
  view.fatigue_load:set_text(fatigue.load)
  view.fatigue_notice:set_text(fatigue.notice)
  view.fatigue_notice:set_style({
    text_color = fatigue.notice ~= "" and t.warning or t.muted,
  })

  if state.handheld_requested_mode == "BLE" then
    view.handheld_button:set_text("恢复P4控制")
  else
    view.handheld_button:set_text("启用手柄调试")
  end
  if state.handheld_mode_switch_inflight or state.action_inflight then
    view.handheld_button:set_style(t.button_style(t.button_dark, t.muted))
  else
    view.handheld_button:set_style(t.button_style(t.button_dark, t.text))
  end

  if enabled then
    view.ready_value:set_text("可以开始")
    view.ready_value:set_style({ text_color = t.normal })
  else
    view.ready_value:set_text("不可开始")
    view.ready_value:set_style({ text_color = t.warning })
  end
  view.start_button:set_style(t.button_style(t.accent, t.text))
  if app.state_module.is_environment_scan_active(state) then
    view.scan_button:set_style(t.button_style(t.scan_active, t.bg))
  else
    view.scan_button:set_style(t.button_style(t.scan_idle, t.bg))
  end
  view.decision_button:set_style(t.button_style(t.accent, t.text))
  local reasoning_available =
    type(task_decision.reasoning_available) == "function" and
    task_decision.reasoning_available() == true
  view.reasoning_button:set_style(t.button_style(
    t.button_dark, reasoning_available and t.text or t.muted))
end

function M.tick(app, now_ms)
  local _ = app
  local __ = now_ms
end

function M.destroy()
  if not view then return end
  for _, event in ipairs(view.events) do
    if event.obj and event.obj:is_valid() then
      pcall(function() event.obj:off(event.handle) end)
    end
  end
  if view.root and view.root:is_valid() then
    pcall(function() view.root:delete() end)
  end
  view = nil
end

return M
