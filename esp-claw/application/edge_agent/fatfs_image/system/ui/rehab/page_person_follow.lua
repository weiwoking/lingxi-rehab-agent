local lvgl = require("lvgl")
local snapshot_bridge = require("rehab_person_follow_snapshot")

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

local function label(parent, text, x, y, color, w, h, align)
  return remember(lvgl.label(parent, {
    text = text, x = x, y = y, w = w, h = h,
    text_color = color, text_font = "rehab", text_align = align or "left",
  }))
end

local function clear_content()
  if not view then return end
  for _, event in ipairs(view.events) do
    if event.obj and event.obj:is_valid() then
      pcall(function() event.obj:off(event.handle) end)
    end
  end
  for index = #view.objects, 1, -1 do
    local object = view.objects[index]
    if object and object:is_valid() then
      pcall(function() object:delete() end)
    end
  end
  view.objects = {}
  view.events = {}
end

local function build_header(app, subtitle)
  local t = app.theme
  local header = remember(lvgl.container(view.root, {
    x = 0, y = 0, w = 1024, h = 72,
    bg_color = t.panel, bg_opa = 255,
    border_color = t.border, border_width = 1,
    radius = 0, pad = 0,
  }))
  label(header, "追人模式", 20, 14, t.accent, 360, 24)
  label(header, subtitle, 20, 42, t.muted, 500, 22)
end

local function build_confirm(app)
  local t = app.theme
  local state = app.state
  build_header(app, "确认后开始自动追人")

  local card = remember(lvgl.container(view.root, {
    x = 172, y = 132, w = 680, h = 356,
    bg_color = t.panel, bg_opa = 255,
    border_color = t.border, border_width = 1,
    radius = t.radius, pad = 0,
  }))
  label(card, "确认开始", 32, 42, t.text, 616, 34, "center")
  label(card, "确认后开始自动追人", 32, 102, t.muted, 616, 34,
    "center")
  view.error_label = label(card, "", 32, 154, t.warning, 616, 30,
    "center")
  view.home_button = remember(lvgl.button(card, {
    text = "返回首页", x = 72, y = 224, w = 244, h = 64,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.start_button = remember(lvgl.button(card, {
    text = state.person_follow_starting_inflight and "启动中" or "确认开始",
    x = 364, y = 224, w = 244, h = 64,
    bg_color = state.person_follow_starting_inflight and
      t.button_dark or t.accent,
    text_color = state.person_follow_starting_inflight and t.muted or t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))

  bind(view.home_button, function()
    app.state_module.close_person_follow_confirm(state)
    app.navigate("home")
  end)
  bind(view.start_button, function()
    if app.queue_person_follow_start() then
      view.start_button:set_text("启动中")
      view.start_button:set_style(t.button_style(t.button_dark, t.muted))
    end
  end)
end

local function build_running(app)
  local t = app.theme
  local state = app.state
  build_header(app, "追人中")

  view.image_canvas = remember(lvgl.canvas(view.root, {
    x = 20, y = 82, w = 640, h = 360, color_format = "rgb565",
    border_color = t.border, border_width = 1, radius = 0,
  }))
  view.image_canvas:fill_bg("#172333")
  view.left_boundary = remember(lvgl.container(view.root, {
    x = 20 + 128, y = 82, w = 2, h = 360,
    bg_color = t.warning, bg_opa = 255, border_width = 0, radius = 0,
  }))
  view.right_boundary = remember(lvgl.container(view.root, {
    x = 20 + 512, y = 82, w = 2, h = 360,
    bg_color = t.warning, bg_opa = 255, border_width = 0, radius = 0,
  }))
  label(view.root, "LEFT", 28, 88, t.warning, 90, 24)
  label(view.root, "CENTER", 286, 88, t.warning, 100, 24, "center")
  label(view.root, "RIGHT", 566, 88, t.warning, 82, 24, "right")
  view.selected_box = remember(lvgl.container(view.root, {
    x = 20, y = 82, w = 1, h = 1, bg_opa = 0,
    border_color = t.normal, border_width = 0, radius = 0, pad = 0,
  }))
  view.selected_center = remember(lvgl.container(view.root, {
    x = 20, y = 82, w = 8, h = 8,
    bg_color = t.danger, bg_opa = 0, border_width = 0, radius = 4,
  }))
  view.image_note = label(view.root, "等待本轮画面", 180, 410,
    t.muted, 320, 24, "center")

  local card = remember(lvgl.container(view.root, {
    x = 674, y = 82, w = 330, h = 360,
    bg_color = t.panel, bg_opa = 255,
    border_color = t.border, border_width = 1,
    radius = t.radius, pad = 0,
  }))
  view.state_label = label(card, "状态 --", 14, 12, t.normal, 302, 26)
  view.target_label = label(card, "目标 --", 14, 50, t.text, 302, 26)
  view.metrics_label = label(card, "置信 --  宽度 --", 14, 88,
    t.text, 302, 26)
  view.planned_label = label(card, "计划 --", 14, 126, t.accent, 302, 26)
  view.actual_label = label(card, "实际 --", 14, 164, t.normal, 302, 26)
  view.reason_label = label(card, "原因 --", 14, 202, t.text, 302, 50)
  view.car_label = label(card, "车控 --", 14, 260, t.text, 302, 26)
  view.params_label = label(card, "参数 --", 14, 302, t.muted, 302, 44)

  local footer = remember(lvgl.container(view.root, {
    x = 20, y = 452, w = 984, h = 128,
    bg_color = t.panel, bg_opa = 255,
    border_color = t.border, border_width = 1,
    radius = t.radius, pad = 0,
  }))
  view.timing_label = label(footer, "耗时 --", 14, 10, t.text, 730, 26)
  view.http_label = label(footer, "HTTP --", 14, 45, t.text, 730, 26)
  view.error_label = label(footer, "最近失败 --", 14, 80,
    t.warning, 730, 34)
  view.stop_button = remember(lvgl.button(footer, {
    text = state.person_follow_stopping_inflight and "停止中" or "暂停追人",
    x = 758, y = 34, w = 208, h = 60,
    bg_color = state.person_follow_stopping_inflight and
      t.button_dark or t.danger,
    text_color = state.person_follow_stopping_inflight and t.muted or t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  bind(view.stop_button, function()
    if app.queue_person_follow_stop() then
      view.stop_button:set_text("停止中")
      view.stop_button:set_style(t.button_style(t.button_dark, t.muted))
    end
  end)
  view.last_snapshot_seq = 0
  view.last_image_seq = 0
end

local function finite(value)
  return type(value) == "number" and value == value and
    value ~= math.huge and value ~= -math.huge
end

local function number(value, fallback)
  return finite(value) and value or (fallback or 0)
end

local function integer(value)
  return math.floor(number(value) + 0.5)
end

local function hide_selected(t)
  view.selected_box:set_style({ bg_opa = 0, border_width = 0 })
  view.selected_center:set_style({ bg_opa = 0, border_width = 0 })
  if t then
    view.image_note:set_style(t.text_style(t.muted, "center"))
  end
end

local function update_selected_overlay(status, t)
  if status.selected_valid ~= true then
    hide_selected(t)
    return
  end
  local x1 = math.floor(number(status.selected_bbox_x1) + 0.5)
  local y1 = math.floor(number(status.selected_bbox_y1) + 0.5)
  local x2 = math.floor(number(status.selected_bbox_x2) + 0.5)
  local y2 = math.floor(number(status.selected_bbox_y2) + 0.5)
  if x1 < 0 or y1 < 0 or x2 > 640 or y2 > 360 or
      x2 <= x1 or y2 <= y1 then
    hide_selected(t)
    return
  end
  view.selected_box:set_pos(20 + x1, 82 + y1)
  view.selected_box:set_size(x2 - x1, y2 - y1)
  view.selected_box:set_style({
    bg_opa = 0, border_color = t.normal, border_width = 3, radius = 0,
  })
  local center_x = number(status.selected_center_x, -1)
  local center_y = number(status.selected_center_y, -1)
  if center_x < 0 or center_x > 1 or center_y < 0 or center_y > 1 then
    hide_selected(t)
    return
  end
  local cx = math.min(639, math.floor(center_x * 640 + 0.5))
  local cy = math.min(359, math.floor(center_y * 360 + 0.5))
  view.selected_center:set_pos(20 + cx - 4, 82 + cy - 4)
  view.selected_center:set_style({
    bg_color = t.danger, bg_opa = 255, border_width = 0, radius = 4,
  })
end

local function update_snapshot_image(status, t)
  local seq = tonumber(status.snapshot_seq) or 0
  if seq <= 0 or seq == view.last_snapshot_seq then return end
  view.last_snapshot_seq = seq
  if status.image_current ~= true or status.image_snapshot_seq ~= seq then
    hide_selected(t)
    view.image_note:set_text("本轮画面未更新；保留上一张")
    return
  end

  local frame, acquire_error = snapshot_bridge.acquire(seq)
  if not frame then
    hide_selected(t)
    view.image_note:set_text("画面获取失败：" .. tostring(acquire_error or "--"))
    return
  end
  local loaded, load_error = pcall(function()
    view.image_canvas:load_frame(frame)
  end)
  pcall(function() frame:release() end)
  if not loaded then
    hide_selected(t)
    view.image_note:set_text("画面解码失败：" .. tostring(load_error))
    return
  end
  view.last_image_seq = seq
  view.image_note:set_text("帧 " .. tostring(seq) .. "  " ..
    tostring(status.zone or "NO_TARGET"))
  update_selected_overlay(status, t)
end

local function desired_mode(app)
  return app.state_module.is_person_follow_running_page(app.state) and
    "running" or "confirm"
end

local function rebuild(app, mode)
  clear_content()
  view.mode = mode
  if mode == "running" then
    build_running(app)
  else
    build_confirm(app)
  end
end

function M.create(app)
  local t = app.theme
  view = { objects = {}, events = {}, mode = nil }
  view.root = lvgl.container(app.screen, {
    x = 0, y = 0, w = t.screen_width, h = t.screen_height,
    bg_color = t.bg, bg_opa = 255, border_width = 0, radius = 0, pad = 0,
  })
  rebuild(app, desired_mode(app))
  return view.root
end

function M.refresh(app)
  if not view then return end
  local t = app.theme
  local state = app.state
  local mode = desired_mode(app)
  if mode ~= view.mode then
    rebuild(app, mode)
  end

  if mode == "confirm" then
    local error_text = state.person_follow_last_ui_error or
      state.person_follow_status_error or ""
    view.error_label:set_text(error_text)
    if state.person_follow_starting_inflight or state.action_inflight then
      view.start_button:set_text("启动中")
      view.start_button:set_style(t.button_style(t.button_dark, t.muted))
    else
      view.start_button:set_text("确认开始")
      view.start_button:set_style(t.button_style(t.accent, t.text))
    end
    return
  end

  local status = state.person_follow_status or {}
  update_snapshot_image(status, t)
  local state_text = status.startup_observation_active == true and
    ("启动观察  剩余 " ..
      tostring(integer(status.startup_observation_remaining_ms)) .. "ms") or
    tostring(status.state or "--")
  view.state_label:set_text("状态 " .. state_text ..
    "  帧 " .. tostring(status.snapshot_seq or "--"))
  local target = status.target_found == true and "检测到人" or
    (status.target_found == false and "未检测到人" or "目标 --")
  view.target_label:set_text(target .. "  " ..
    tostring(status.zone or status.last_zone or "--") .. "  raw/去重 " ..
    tostring(status.raw_count or 0) .. "/" .. tostring(status.dedup_count or 0))
  view.metrics_label:set_text(string.format("置信 %.2f  宽度 %.3f  裁切 %s",
    number(status.confidence), number(status.bbox_width_ratio),
    status.cropped_vertical == true and "是" or "否"))
  view.planned_label:set_text("计划 " ..
    tostring(status.planned_action or status.last_planned_action or "--"))
  view.actual_label:set_text("实际 " ..
    tostring(status.actual_action or status.last_action or "--"))
  view.reason_label:set_text("原因 " ..
    tostring(status.action_reason or status.last_reason or "--") ..
    "  连续停止 " .. tostring(status.rolling_stop_reason or "--"))
  view.car_label:set_text("车控 owner=" ..
    tostring(status.snapshot_owner_acquired == true) .. "  结果 " ..
    tostring(status.snapshot_car_result or status.last_car_result or "--"))
  view.params_label:set_text(string.format(
    "速度 %d  动作 %dms  下次转向 %dms\n等待 %dms  连续 %s  段 %d  租约 %dms",
    integer(status.speed), integer(status.hold_ms),
    integer(status.next_turn_hold_ms),
    integer(status.snapshot_post_action_wait_ms or status.post_action_wait_ms),
    status.forward_rolling == true and "开" or "关",
    integer(status.forward_segments_completed),
    integer(status.forward_lease_remaining_ms)))
  view.timing_label:set_text(string.format(
    "耗时 Camera %dms  HTTP %dms  PC %.1f/%.1fms  总计 %dms",
    integer(status.camera_ms), integer(status.http_ms),
    number(status.pc_inference_ms), number(status.pc_total_ms),
    integer(status.loop_total_ms)))
  view.http_label:set_text("HTTP " .. tostring(status.http_status or
    status.last_http_status or "--") .. "  JPEG " ..
    tostring(status.jpeg_bytes or status.last_jpeg_bytes or 0) ..
    " bytes  图像 " .. (status.image_current == true and "本轮" or "未更新"))
  local last_error = state.person_follow_last_ui_error or
    state.person_follow_status_error
  local snapshot_error = status.snapshot_error or status.last_error
  if not last_error and snapshot_error and snapshot_error ~= "" and
      snapshot_error ~= "none" then
    last_error = snapshot_error
  end
  view.error_label:set_text("最近失败 " ..
    tostring(status.error_stage or "--") .. " / " ..
    tostring(last_error or "--"))
  if state.person_follow_stopping_inflight or state.action_inflight then
    view.stop_button:set_text("停止中")
    view.stop_button:set_style(t.button_style(t.button_dark, t.muted))
  else
    view.stop_button:set_text("暂停追人")
    view.stop_button:set_style(t.button_style(t.danger, t.text))
  end
end

function M.destroy()
  if not view then return end
  clear_content()
  if view.root and view.root:is_valid() then
    pcall(function() view.root:delete() end)
  end
  view = nil
end

return M
