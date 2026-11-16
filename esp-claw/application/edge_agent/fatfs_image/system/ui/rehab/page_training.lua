local lvgl = require("lvgl")

local M = {}
local view = nil

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

local function node_text(node)
  if type(node) ~= "table" or node.online ~= true then
    return "未连接"
  end
  if node.packet_updating == true then
    return "已连接 / 数据变化中"
  end
  return "已连接 / 等待数据"
end

local function set_confirm_visible(state, visible)
  state.end_confirm_open = visible == true
  if view and view.confirm_overlay then
    view.confirm_overlay:set_pos(0, visible and 0 or 620)
  end
end

function M.create(app)
  local t = app.theme
  local state = app.state
  view = { objects = {}, events = {} }

  local root = remember(lvgl.container(app.screen, {
    x = 0, y = 0, w = 1024, h = 600,
    bg_color = t.bg, bg_opa = 255, border_width = 0, radius = 0, pad = 0,
  }))
  view.root = root

  local header = panel(root, 0, 0, 1024, 72, t)
  header:set_style({ radius = 0 })
  label(header, "康复训练", 20, 14, t.accent, 300, 24)
  label(header, "真实传感器数据记录", 20, 42, t.muted, 300, 22)
  view.phase = label(header, "状态：idle", 684, 24, t.normal, 316, 28)

  local task = panel(root, 20, 88, 300, 260, t)
  label(task, "当前任务", 16, 16, t.accent, 220, 24)
  view.task_name = label(task, "--", 16, 54, t.text, 268, 52)
  label(task, "Session", 16, 126, t.muted, 160, 24)
  view.session = label(task, "--", 16, 156, t.text, 268, 48)
  label(task, "数据完整性", 16, 214, t.muted, 160, 24)
  view.integrity = label(task, "等待状态", 150, 214, t.normal, 134, 24)

  local counters = panel(root, 336, 88, 350, 260, t)
  label(counters, "记录数据", 16, 16, t.accent, 220, 24)
  label(counters, "CSV 行数", 28, 72, t.muted, 140, 24)
  view.rows = label(counters, "0", 190, 68, t.text, 130, 32)
  label(counters, "queue drop", 28, 126, t.muted, 140, 24)
  view.queue_drop = label(counters, "0", 190, 122, t.text, 130, 32)
  label(counters, "SD error", 28, 180, t.muted, 150, 24)
  view.sd_error = label(counters, "0", 190, 176, t.text, 130, 32)
  view.counter_note = label(counters, "行数不是动作次数", 28, 226,
    t.muted, 290, 22)

  local sensors = panel(root, 702, 88, 302, 260, t)
  label(sensors, "传感器状态", 16, 16, t.accent, 220, 24)
  label(sensors, "Node 1", 16, 64, t.muted, 90, 24)
  view.node1 = label(sensors, "等待状态", 110, 64, t.warning, 176, 48)
  label(sensors, "Node 2", 16, 120, t.muted, 90, 24)
  view.node2 = label(sensors, "等待状态", 110, 120, t.warning, 176, 48)
  label(sensors, "所需传感器", 16, 184, t.muted, 130, 24)
  view.readiness = label(sensors, "未就绪", 160, 184,
    t.warning, 126, 24)
  view.sd_ready = label(sensors, "SD 未就绪", 16, 226,
    t.warning, 270, 22)

  local feedback = panel(root, 20, 364, 984, 80, t)
  label(feedback, "实时提示", 16, 12, t.accent, 180, 24)
  view.feedback = label(feedback, "等待记录状态", 210, 26,
    t.text, 750, 42)

  local actions = panel(root, 20, 460, 984, 120, t)
  view.status = label(actions, "等待训练状态", 18, 49,
    t.muted, 560, 24)
  view.stop_button = remember(lvgl.button(actions, {
    text = "结束训练", x = 738, y = 26, w = 226, h = 68,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))

  view.confirm_overlay = remember(lvgl.container(root, {
    x = 0, y = 620, w = 1024, h = 600,
    bg_color = t.bg, bg_opa = 220,
    border_width = 0, radius = 0, pad = 0,
  }))
  local confirm = panel(view.confirm_overlay, 198, 186, 628, 220, t)
  confirm:set_style({ bg_color = t.panel, border_color = t.accent,
    border_width = 2 })
  label(confirm, "结束训练", 24, 22, t.accent, 220, 28)
  label(confirm, "确定结束本次训练并保存数据吗？", 24, 74,
    t.text, 580, 54)
  local cancel = remember(lvgl.button(confirm, {
    text = "取消", x = 290, y = 146, w = 136, h = 52,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  local submit = remember(lvgl.button(confirm, {
    text = "确认结束", x = 444, y = 146, w = 150, h = 52,
    bg_color = t.warning, text_color = t.bg,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))

  bind(view.stop_button, function()
    if app.state_module.is_stop_enabled(state) then
      set_confirm_visible(state, true)
      app.force_full_refresh = true
    end
  end)
  bind(cancel, function()
    set_confirm_visible(state, false)
    app.force_full_refresh = true
  end)
  bind(submit, function()
    -- Only queue the action. The main UI loop performs the Capability call.
    app.queue_stop_training()
  end)
  return root
end

function M.refresh(app)
  if not view then return end
  local state = app.state
  local t = app.theme
  local node1 = state.node1 or {}
  local node2 = state.node2 or {}

  view.task_name:set_text(state.current_task_name ~= "" and
    state.current_task_name or state.selected_task_name or "--")
  view.session:set_text(state.current_session_id ~= "" and
    state.current_session_id or state.bound_training_session_id or "--")
  view.phase:set_text("状态：" .. tostring(state.training_state or "idle"))
  view.rows:set_text(tostring(state.rows_written or 0))
  view.queue_drop:set_text(tostring(state.queue_drop or 0))
  view.sd_error:set_text(tostring(state.sd_write_error or 0))
  view.node1:set_text(node_text(node1))
  view.node2:set_text(node_text(node2))
  view.node1:set_style({ text_color = node1.online and t.normal or t.warning })
  view.node2:set_style({ text_color = node2.online and t.normal or t.warning })
  view.readiness:set_text(state.required_sensors_ok and "已就绪" or "未就绪")
  view.readiness:set_style({ text_color = state.required_sensors_ok and
    t.normal or t.warning })
  view.sd_ready:set_text(state.sd_ready and "SD 已就绪" or "SD 未就绪")
  view.sd_ready:set_style({ text_color = state.sd_ready and t.normal or t.warning })

  local integrity_ok = (state.queue_drop or 0) == 0 and
    (state.sd_write_error or 0) == 0
  view.integrity:set_text(integrity_ok and "写入可用" or "请检查")
  view.integrity:set_style({ text_color = integrity_ok and t.normal or t.warning })
  view.feedback:set_text(state.training_error or
    (state.training_state == "recording" and "记录中" or
      tostring(state.training_state or "等待状态")))
  view.status:set_text(state.end_confirm_open and "等待确认结束" or
    (state.action_inflight and "结束处理中" or "训练进行中"))

  if app.state_module.is_stop_enabled(state) then
    view.stop_button:set_style(t.button_style(t.warning, t.bg))
  else
    view.stop_button:set_style(t.button_style(t.button_dark, t.muted))
  end
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
