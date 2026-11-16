local lvgl = require("lvgl")
local task_decision = require("rehab_task_decision_ui")
local geometry_ui = require("rehab_ui_geometry")

local M = {}
local view = nil

local function remember(obj)
  view.objects[#view.objects + 1] = obj
  return obj
end

local function bind(obj, callback)
  local handle = obj:on("clicked", callback)
  view.events[#view.events + 1] = { obj = obj, handle = handle }
end

local function label(parent, text, x, y, color, w, h, align)
  return remember(lvgl.label(parent, {
    text = text, x = x, y = y, w = w, h = h,
    text_color = color, text_font = "rehab", text_align = align or "left",
  }))
end

local function result_title(data)
  if data.selected then return "任务决策完成" end
  if data.preserved then return "当前任务已保留" end
  return "任务决策未完成，请重新决策"
end

local function result_reason(data)
  if data.has_task and data.reason ~= "" then return data.reason end
  return "任务决策未完成，请重新决策"
end

local function target_height_texts(data)
  if not data.target_available then return "", "" end
  return geometry_ui.target_height_lines(data.geometry, data.target_class)
end

local function show_target_image(data, theme)
  if not data.has_task or not data.target_available then
    view.image_note:set_text("暂无目标画面")
    return
  end
  local frame = task_decision.target_image()
  if not frame then
    view.image_note:set_text("目标图片不可用")
    return
  end
  local loaded = pcall(function()
    view.canvas:load_frame(frame)
  end)
  pcall(function() frame:release() end)
  if not loaded then
    view.image_note:set_text("目标图片不可用")
    return
  end
  local b = data.bbox or {}
  local function finite(value)
    return type(value) == "number" and value == value and
      value ~= math.huge and value ~= -math.huge
  end
  local raw = {
    tonumber(b[1]), tonumber(b[2]), tonumber(b[3]), tonumber(b[4]),
  }
  if finite(raw[1]) and finite(raw[2]) and finite(raw[3]) and
      finite(raw[4]) then
    local x1 = math.max(0, math.min(640, math.floor(raw[1])))
    local y1 = math.max(0, math.min(360, math.floor(raw[2])))
    local x2 = math.max(0, math.min(640, math.floor(raw[3])))
    local y2 = math.max(0, math.min(360, math.floor(raw[4])))
    if x2 > x1 and y2 > y1 then
    view.target_box:set_pos(20 + x1, 82 + y1)
    view.target_box:set_size(x2 - x1, y2 - y1)
    view.target_box:set_style({
      bg_opa = 0, border_color = theme.normal,
      border_width = 3, radius = 0,
    })
    else
      view.image_note:set_text("目标图片不可用")
      return
    end
  else
    view.image_note:set_text("目标图片不可用")
    return
  end
  view.image_note:set_text(
    "目标：" .. geometry_ui.object_class(data.target_class))
end

function M.create(app)
  local t = app.theme
  local data = app.state_module.get_task_decision_view(app.state)
  view = { objects = {}, events = {} }
  local root = remember(lvgl.container(app.screen, {
    x = 0, y = 0, w = t.screen_width, h = t.screen_height,
    bg_color = t.bg, bg_opa = 255, border_width = 0, radius = 0, pad = 0,
  }))
  view.root = root
  view.title = label(root, result_title(data), 20, 18,
    data.selected and t.normal or t.warning, 984, 42, "center")
  view.elapsed = label(root,
    string.format("AI决策耗时：%ds", data.elapsed_sec),
    674, 54, t.muted, 330, 24, "right")
  view.canvas = remember(lvgl.canvas(root, {
    x = 20, y = 82, w = 640, h = 360, color_format = "rgb565",
    border_color = t.border, border_width = 1, radius = 0,
  }))
  view.canvas:fill_bg("#172333")
  view.target_box = remember(lvgl.container(root, {
    x = 20, y = 82, w = 1, h = 1, bg_opa = 0,
    border_color = t.normal, border_width = 0, radius = 0, pad = 0,
  }))
  view.image_note = label(root, "正在读取目标画面", 20, 448,
    t.muted, 640, 28, "center")
  local card = remember(lvgl.container(root, {
    x = 674, y = 82, w = 330, h = 388,
    bg_color = t.panel, bg_opa = 255, border_color = t.border,
    border_width = 1, radius = t.radius, pad = 0,
  }))
  label(card, "推荐任务", 14, 6, t.muted, 302, 24)
  view.task = label(card, data.task_name ~= "" and data.task_name or "--",
    14, 30, t.accent, 302, 52)
  view.score = label(card, string.format("推荐分数：%.0f", data.score),
    14, 84, t.text, 302, 26)
  label(card, "推荐原因", 14, 112, t.muted, 302, 24)
  view.reason = label(card, result_reason(data),
    14, 136, data.has_task and t.text or t.warning, 302, 76)
  view.target_class = label(card,
    "目标物体：" .. geometry_ui.object_class(data.target_class),
    14, 216, t.text, 302, 24)
  local object_height_text, support_height_text = target_height_texts(data)
  view.object_height = label(card,
    object_height_text, 14, 244, t.text, 302, 24)
  view.support_height = label(card,
    support_height_text, 14, 272, t.text, 302, 24)

  local home = remember(lvgl.button(root, {
    text = "返回首页", x = 20, y = 504, w = 180, h = 60,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  local retry = remember(lvgl.button(root, {
    text = "重新决策", x = 214, y = 504, w = 180, h = 60,
    bg_color = t.scan_idle, text_color = t.bg,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  local details = remember(lvgl.button(root, {
    text = "查看详情", x = 408, y = 504, w = 160, h = 60,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.reasoning = remember(lvgl.button(root, {
    text = "查看AI分析", x = 582, y = 504, w = 180, h = 60,
    bg_color = t.button_dark, text_color = t.muted,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.start = remember(lvgl.button(root, {
    text = "开始训练", x = 776, y = 504, w = 228, h = 60,
    bg_color = t.accent, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  bind(home, function() app.return_task_decision_home() end)
  bind(retry, function() app.queue_task_redecision() end)
  bind(details, function() app.open_task_decision_details("result") end)
  bind(view.reasoning, function()
    app.open_task_decision_reasoning("result")
  end)
  bind(view.start, function()
    app.queue_start_training()
  end)
  show_target_image(data, t)
  local reasoning_available = data.selected and data.decision_id ~= "" and
    type(task_decision.reasoning_available) == "function" and
    task_decision.reasoning_available(data.decision_id) == true
  view.reasoning:set_style(t.button_style(
    t.button_dark, reasoning_available and t.text or t.muted))
  return root
end

function M.refresh(app)
  if not view then return end
  local data = app.state_module.get_task_decision_view(app.state)
  view.title:set_text(result_title(data))
  view.elapsed:set_text(string.format("AI决策耗时：%ds", data.elapsed_sec))
  view.task:set_text(data.task_name ~= "" and data.task_name or "--")
  view.score:set_text(string.format("推荐分数：%.0f", data.score))
  view.reason:set_text(result_reason(data))
  view.target_class:set_text(
    "目标物体：" .. geometry_ui.object_class(data.target_class))
  local object_height_text, support_height_text = target_height_texts(data)
  view.object_height:set_text(object_height_text)
  view.support_height:set_text(support_height_text)
  local reasoning_available = data.selected and data.decision_id ~= "" and
    type(task_decision.reasoning_available) == "function" and
    task_decision.reasoning_available(data.decision_id) == true
  view.reasoning:set_style(app.theme.button_style(
    app.theme.button_dark,
    reasoning_available and app.theme.text or app.theme.muted))
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
