local lvgl = require("lvgl")
local task_decision = require("rehab_task_decision_ui")
local geometry_ui = require("rehab_ui_geometry")

local M = {}
local view = nil

local factor_order = {
  { "difficulty", "难度匹配", 35 },
  { "advice", "医嘱匹配", 25 },
  { "compensation", "代偿历史", 20 },
  { "frequency", "训练频率", 10 },
  { "perception", "感知质量", 10 },
  { "final_raw", "总分", 100 },
}

local entry_source_text = {
  local_ui = "本地界面",
  wechat = "微信",
  console = "控制台",
  unknown = "未知",
}

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
    text_color = color, text_font = "rehab",
    text_align = align or "left", long_mode = "wrap",
  }))
end

local function panel(parent, x, y, w, h, theme)
  return remember(lvgl.container(parent, {
    x = x, y = y, w = w, h = h,
    bg_color = theme.panel, bg_opa = 255,
    border_color = theme.border, border_width = 1,
    radius = theme.radius, pad = 0,
  }))
end

local function text_or(value, fallback)
  if type(value) == "string" and value ~= "" then return value end
  return fallback or "本次未提供"
end

local function number_or(value, fallback)
  if type(value) == "number" then return string.format("%.1f", value) end
  return fallback or "无可用数据"
end

local function wrapped_height(value, minimum)
  local text = tostring(value or "")
  local lines = math.max(1, math.ceil(#text / 90))
  return math.max(minimum or 30, lines * 28)
end

local function mm_or(value)
  if type(value) == "number" then
    return string.format("%.1f cm", value / 10.0)
  end
  return "无可用数据"
end

local function status_zh(value)
  if value == "available" then return "已接入" end
  if value == "fallback" or value == "unavailable_fallback" then
    return "不可用，使用回退公式"
  end
  if value == "malformed_fallback" then
    return "格式异常，使用回退公式"
  end
  if value == "not_integrated" then return "未接入" end
  if value == "not_provided" then return "本次未提供" end
  return "无可用数据"
end

local function availability_zh(value)
  return value == "available" and "可用" or "不可用"
end

local function blocked_reason_zh(value)
  if value == "ability_exceeded" then return "超出当前能力等级" end
  if value == "semantic_group_absent" or
      value == "no_matching_plane_target" then
    return "当前场景没有可用目标"
  end
  return "本次未提供"
end

local function breakdown_text(breakdown)
  if type(breakdown) ~= "table" then return "评分因素：不适用" end
  local parts = {}
  for _, item in ipairs(factor_order) do
    local factor = breakdown[item[1]]
    if type(factor) == "table" and type(factor.value) == "number" then
      parts[#parts + 1] = string.format("%s %.1f / %d", item[2],
        factor.value, item[3])
    else
      parts[#parts + 1] = item[2] .. "（不适用）"
    end
  end
  return table.concat(parts, "  ")
end

local function short_id(value)
  value = text_or(value, "--")
  if #value > 28 then return string.sub(value, 1, 28) .. "…" end
  return value
end

local function show_target_image(data, body, y, theme)
  local target = data.target_details or {}
  local canvas = remember(lvgl.canvas(body, {
    x = 16, y = y, w = 640, h = 360, color_format = "rgb565",
    border_color = theme.border, border_width = 1, radius = 0,
  }))
  canvas:fill_bg("#172333")
  local note = label(body, "正在读取目标画面", 16, y + 364,
    theme.muted, 640, 26, "center")
  local frame = task_decision.target_image(data.decision_id)
  if not frame then
    note:set_text("目标图片不可用")
    return
  end
  local loaded = pcall(function() canvas:load_frame(frame) end)
  pcall(function() frame:release() end)
  if not loaded then
    note:set_text("目标图片不可用")
    return
  end
  local b = target.bbox or {}
  local x1, y1, x2, y2 = tonumber(b[1]), tonumber(b[2]),
    tonumber(b[3]), tonumber(b[4])
  if x1 and y1 and x2 and y2 and x2 > x1 and y2 > y1 then
    x1, y1 = math.max(0, math.min(640, math.floor(x1))),
      math.max(0, math.min(360, math.floor(y1)))
    x2, y2 = math.max(0, math.min(640, math.floor(x2))),
      math.max(0, math.min(360, math.floor(y2)))
    local box = remember(lvgl.container(body, {
      x = 16 + x1, y = y + y1, w = math.max(1, x2 - x1),
      h = math.max(1, y2 - y1), bg_opa = 0,
      border_color = theme.normal, border_width = 3,
      radius = 0, pad = 0,
    }))
    local _ = box
    note:set_text("目标：" .. geometry_ui.object_class(target.class_name))
  else
    note:set_text("bbox 无可用数据")
  end
end

local function create_unavailable(body, data, theme)
  local message = data.code == "decision_mismatch"
    and "本次详情不可用" or "决策详情暂无可用数据"
  label(body, message, 20, 40, theme.warning, 920, 48, "center")
  label(body, "文件缺失、损坏或版本不支持不会影响正式任务。",
    20, 104, theme.muted, 920, 36, "center")
end

local function create_details(body, data, app)
  local t = app.theme
  local y = 12
  label(body, "决策：" .. short_id(data.decision_id) ..
    "    决策来源：" .. (entry_source_text[data.entry_source] or "未知") ..
    "    时间状态：" ..
    status_zh(data.time_status), 16, y, t.muted, 940, 30)
  y = y + 42

  local selected = data.selected_task or {}
  local summary = text_or(data.deepseek_summary)
  local selected_reason = text_or(selected.reason)
  local selected_reason_h = wrapped_height(selected_reason, 58)
  local summary_h = wrapped_height(summary, 58)
  local selected_h = 112 + selected_reason_h + summary_h
  local selected_card = panel(body, 12, y, 948, selected_h, t)
  label(selected_card, "本次选择", 14, 10, t.accent, 180, 26)
  label(selected_card, text_or(selected.task_name, "无可用数据"),
    14, 42, t.text, 600, 30)
  label(selected_card, "最终分数：" .. number_or(selected.score),
    640, 42, t.normal, 280, 30)
  label(selected_card, "推荐原因：" .. selected_reason,
    14, 78, t.text, 906, selected_reason_h)
  label(selected_card, "DeepSeek 总结：" .. summary,
    14, 84 + selected_reason_h, t.text, 906, summary_h)
  y = y + selected_h + 14

  local inputs = data.decision_inputs or {}
  local condition = data.patient_condition or {}
  local ability = data.patient_ability or {}
  local basis = data.advice_basis or {}
  local availability = data.data_availability or {}
  local raw_condition = text_or(condition.raw_text, "本次未提供")
  local raw_h = wrapped_height(raw_condition, 58)
  local input_h = 234 + raw_h
  local input_card = panel(body, 12, y, 948, input_h, t)
  label(input_card, "本次输入事实", 14, 10, t.accent, 220, 26)
  label(input_card, "能力等级：" .. number_or(ability.level) ..
    "；解析状态：" .. text_or(ability.parse_status),
    14, 42, t.text, 906, 28)
  label(input_card, "患者情况来源：" ..
    text_or(condition.source) .. "（" ..
    status_zh(availability.patient_condition) .. "）",
    14, 74, t.text, 906, 28)
  label(input_card, "患者情况与医嘱：" .. raw_condition,
    14, 106, t.text, 906, raw_h)
  local basis_y = 112 + raw_h
  label(input_card, "医嘱方向：" .. text_or(basis.direction) ..
    "；真实依据：" .. text_or(basis.evidence),
    14, basis_y, t.text, 906, 56)
  local history = inputs.recent_history_summary or {}
  label(input_card, "最近评分：" .. number_or(inputs.recent_score_count) ..
    " 项；趋势：" .. text_or(history.tendency_level, "无可用数据") ..
    "（" .. status_zh(availability.recent_history) .. "）",
    14, basis_y + 62, t.text, 906, 28)
  local scene = inputs.scene_source or {}
  label(input_card, "场景 / scan：" .. text_or(scene.scan_id),
    14, basis_y + 94, t.text, 600, 28)
  local comp = inputs.compensation_facts or {}
  local freq = inputs.frequency_facts or {}
  label(input_card, "代偿：" .. text_or(comp.status, "无可用数据") ..
    "；频率事实：水杯 " .. number_or(freq.cup_count) ..
    " / 球类 " .. number_or(freq.ball_count),
    14, basis_y + 126, t.text, 906, 34)
  y = y + input_h + 14

  label(body, "五个候选", 16, y, t.accent, 220, 30)
  y = y + 36
  for index = 1, 5 do
    local candidate = (data.candidates or {})[index] or {}
    local reason_text = text_or(candidate.reason)
    local reason_h = wrapped_height(reason_text, 46)
    local breakdown = breakdown_text(candidate.score_breakdown)
    local breakdown_h = wrapped_height(breakdown, 48)
    local card_h = 202 + reason_h + breakdown_h
    local card = panel(body, 12, y, 948, card_h, t)
    label(card, text_or(candidate.task_name, "无可用数据"),
      14, 10, t.text, 610, 30)
    local score_text = candidate.availability == "available"
      and number_or(candidate.score) or "不适用"
    label(card, availability_zh(candidate.availability) .. "  分数：" ..
      score_text, 640, 10,
      candidate.availability == "available" and t.normal or t.warning,
      280, 30)
    label(card, "理由：" .. reason_text,
      14, 44, t.text, 906, reason_h)
    if candidate.availability ~= "available" then
      label(card, "不可用原因：" ..
        blocked_reason_zh(candidate.blocked_reason),
        14, 50 + reason_h, t.warning, 906, 28)
    end
    local gates = candidate.hard_gates or {}
    label(card, "硬门：能力 " .. tostring(gates.ability_allowed) ..
      "；同组物体 " .. number_or(gates.group_object_count) ..
      "；可行目标 " .. tostring(gates.feasible_target),
      14, 84 + reason_h, t.muted, 906, 34)
    local facts = candidate.target_summary or {}
    label(card, "目标事实：" .. text_or(facts.raw_class) ..
      "；平面 " .. text_or(facts.plane) ..
      "；高度置信度 " .. number_or(facts.height_confidence) ..
      "；几何置信度 " .. number_or(facts.geometry_confidence,
        status_zh(facts.geometry_confidence_status)) ..
      "；感知质量 " .. number_or(facts.perception_quality),
      14, 122 + reason_h, t.muted, 906, 42)
    label(card, breakdown,
      14, 168 + reason_h, t.muted, 906, breakdown_h)
    y = y + card_h + 12
  end

  label(body, "目标详情", 16, y, t.accent, 220, 30)
  y = y + 36
  local target = data.target_details or {}
  show_target_image(data, body, y, t)
  label(body, "目标类别：" ..
    geometry_ui.object_class(target.class_name), 674, y,
    t.text, 280, 30)
  label(body, "bbox：" .. (type(target.bbox) == "table" and
    table.concat(target.bbox, ", ") or "无可用数据"),
    674, y + 40, t.text, 280, 54)
  label(body, "物体自身高度：" .. mm_or(target.object_height_mm),
    674, y + 104, t.text, 280, 30)
  label(body, "离地高度：" .. mm_or(target.support_height_mm),
    674, y + 140, t.text, 280, 30)
  label(body, "顶部高度：" .. mm_or(target.top_height_mm),
    674, y + 176, t.text, 280, 30)
  label(body, "距离：" .. mm_or(target.distance_mm),
    674, y + 212, t.text, 280, 30)
  label(body, "高度置信度：" ..
    number_or(target.height_confidence) .. "（" ..
    status_zh(availability.height_confidence) .. "）",
    674, y + 248, t.text, 280, 44)
  label(body, "几何置信度：" ..
    number_or(target.geometry_confidence) .. "（" ..
    status_zh(availability.geometry_confidence) .. "）",
    674, y + 298, t.text, 280, 44)
  label(body, "图片状态：" .. status_zh(availability.target_image),
    674, y + 348, t.muted, 280, 30)
  label(body, " ", 16, y + 410, t.muted, 10, 30)
end

function M.create(app)
  local t = app.theme
  view = { objects = {}, events = {} }
  local root = remember(lvgl.container(app.screen, {
    x = 0, y = 0, w = t.screen_width, h = t.screen_height,
    bg_color = t.bg, bg_opa = 255, border_width = 0, radius = 0, pad = 0,
  }))
  view.root = root
  label(root, "决策详情", 20, 16, t.accent, 984, 40, "center")
  local body = panel(root, 20, 64, 984, 466, t)
  body:set_scroll({ dir = "ver", scrollbar = "auto" })
  local data = task_decision.details(app.details_expected_decision_id)
  view.data = data
  if data and data.available then
    create_details(body, data, app)
  else
    create_unavailable(body, data or {}, t)
  end
  local back = remember(lvgl.button(root, {
    text = "返回", x = 402, y = 540, w = 220, h = 48,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  bind(back, function() app.return_task_decision_details() end)
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
