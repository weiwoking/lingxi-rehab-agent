local lvgl = require("lvgl")

local M = {}
local view = nil

local function remember(obj)
  view.objects[#view.objects + 1] = obj
  return obj
end

local function bind(obj, callback)
  local handle = obj:on("clicked", callback)
  view.events[#view.events + 1] = {
    obj = obj, handle = handle, callback = callback,
  }
end

local function panel(parent, x, y, w, h, theme)
  return remember(lvgl.container(parent, {
    x = x, y = y, w = w, h = h,
    bg_color = theme.panel, bg_opa = 255,
    border_color = theme.border, border_width = 1,
    radius = theme.radius, pad = 0,
  }))
end

local function label(parent, text, x, y, color, w, h, align)
  return remember(lvgl.label(parent, {
    text = text, x = x, y = y, w = w, h = h,
    text_color = color, text_font = "rehab",
    text_align = align or "left",
  }))
end

function M.create(app)
  local t = app.theme
  view = { objects = {}, events = {} }

  local root = remember(lvgl.container(app.screen, {
    x = 0, y = 0, w = t.screen_width, h = t.screen_height,
    bg_color = t.bg, bg_opa = 255, border_width = 0, radius = 0, pad = 0,
  }))
  view.root = root

  local header = panel(root, 0, 0, 1024, 72, t)
  header:set_style({ radius = 0 })
  label(header, "环境扫描", 20, 18, t.accent, 360, 30)
  label(header, "本地设备", 820, 22, t.muted, 160, 24, "right")

  local status_panel = panel(root, 80, 104, 864, 344, t)
  view.title = label(status_panel, "准备中", 32, 34, t.warning,
    800, 48, "center")
  view.detail = label(status_panel, "正在准备环境扫描", 32, 100, t.text,
    800, 40, "center")
  view.progress = label(status_panel, "已完成 0/11 组", 32, 164, t.accent,
    800, 34, "center")
  view.scan_id = label(status_panel, "扫描编号：--", 32, 222, t.muted,
    800, 30, "center")
  label(status_panel, "扫描期间车辆会分步旋转，请保持周围空间安全。",
    32, 282, t.muted, 800, 30, "center")

  view.action_button = remember(lvgl.button(root, {
    text = "停止扫描", x = 352, y = 486, w = 320, h = 64,
    bg_color = t.danger, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  bind(view.action_button, function()
    local scan = app.state_module.get_environment_scan_view(app.state)
    if scan.active and scan.can_stop then
      app.queue_environment_scan_cancel()
    elseif scan.can_decide then
      app.open_task_decision_prepare()
    elseif not scan.active then
      app.return_environment_scan_home()
    end
  end)

  return root
end

function M.refresh(app)
  if not view then return end
  local t = app.theme
  local scan = app.state_module.get_environment_scan_view(app.state)
  local title_color = t.warning
  if scan.state == "DECISION_READY" then
    title_color = t.normal
  elseif scan.state == "FAILED" or scan.state == "CANCELLED" then
    title_color = t.danger
  elseif scan.active then
    title_color = t.accent
  end

  view.title:set_text(scan.title)
  view.title:set_style({ text_color = title_color })
  view.detail:set_text(scan.detail)
  view.progress:set_text(string.format("已完成 %d/%d 组",
    scan.completed, scan.total))
  view.scan_id:set_text("扫描编号：" ..
    (scan.scan_id ~= "" and scan.scan_id or "--"))

  if scan.active then
    view.action_button:set_text(
      scan.state == "CANCELLING" and "正在停止" or "停止扫描")
    view.action_button:set_style(t.button_style(
      scan.can_stop and t.danger or t.button_dark,
      scan.can_stop and t.text or t.muted))
  else
    view.action_button:set_text(
      scan.can_decide and "决策任务" or "返回首页")
    view.action_button:set_style(t.button_style(
      scan.can_decide and t.accent or t.button_dark, t.text))
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
