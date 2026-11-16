local lvgl = require("lvgl")

local M = {}
local view = nil

local function remember(obj)
  view.objects[#view.objects + 1] = obj
  return obj
end

function M.create(app)
  local t = app.theme
  view = { objects = {}, events = {} }
  local root = remember(lvgl.container(app.screen, {
    x = 0, y = 0, w = t.screen_width, h = t.screen_height,
    bg_color = t.bg, bg_opa = 255, border_width = 0, radius = 0, pad = 0,
  }))
  view.root = root
  remember(lvgl.label(root, {
    text = "任务决策准备", x = 20, y = 24, w = 984, h = 42,
    text_color = t.accent, text_font = "rehab", text_align = "center",
  }))
  remember(lvgl.label(root, {
    text = "环境数据已准备", x = 80, y = 170, w = 864, h = 48,
    text_color = t.normal, text_font = "rehab", text_align = "center",
  }))
  remember(lvgl.label(root, {
    text = "将使用最新一次成功扫描、医嘱与最近训练记录", x = 80, y = 250,
    w = 864, h = 42, text_color = t.text, text_font = "rehab",
    text_align = "center",
  }))
  local back = remember(lvgl.button(root, {
    text = "返回首页", x = 180, y = 430, w = 300, h = 64,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  local handle = back:on("clicked", function()
    app.return_task_decision_prepare()
  end)
  view.events[#view.events + 1] = {
    obj = back, handle = handle,
  }
  local decide = remember(lvgl.button(root, {
    text = "决策任务", x = 544, y = 430, w = 300, h = 64,
    bg_color = t.accent, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  local decide_handle = decide:on("clicked", function()
    app.queue_task_decision_start()
  end)
  view.events[#view.events + 1] = {
    obj = decide, handle = decide_handle,
  }
  return root
end

function M.refresh(app)
  local _ = app
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
