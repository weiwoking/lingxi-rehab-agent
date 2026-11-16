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

local function score_text(value)
  local number = tonumber(value)
  return number and string.format("%.1f", number) or "--"
end

local function warnings_text(result)
  local values = type(result) == "table" and result.warnings or nil
  if type(values) ~= "table" or #values == 0 then
    return "无评分提示"
  end
  local lines = {}
  for index, item in ipairs(values) do
    lines[#lines + 1] = string.format("%d. %s", index, tostring(item))
  end
  return table.concat(lines, "\n")
end

local function result_status_text(state)
  if state.score_state == "succeeded" then
    return "评分完成", "normal"
  end
  if state.score_state == "pending" or state.score_state == "uploading" or
      state.score_state == "accepted" or state.score_state == "scoring" or
      state.score_state == "missing" then
    return "评分处理中", "muted"
  end
  if state.score_state == "failed" or state.score_state == "invalid" or
      state.score_state == "session_mismatch" then
    return "评分失败", "warning"
  end
  return "评分暂不可用", "warning"
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
  label(header, "训练评分", 20, 12, t.accent, 300, 24)
  view.task_name = label(header, state.result_task_name or "--",
    20, 42, t.muted, 560, 22)
  view.state_label = label(header, "评分处理中", 760, 24,
    t.muted, 220, 24)

  local average = panel(root, 20, 88, 294, 220, t)
  label(average, "整组评分", 16, 14, t.muted, 180, 24)
  view.average = label(average, "--", 74, 66, t.accent, 180, 62)
  view.repetitions = label(average, "动作次数 -- / --",
    42, 158, t.text, 236, 28)
  view.completed_at = label(average, "完成时间 --",
    16, 194, t.muted, 262, 22)

  local metrics = panel(root, 330, 88, 674, 220, t)
  label(metrics, "评分指标", 16, 14, t.accent, 220, 24)
  local max_card = panel(metrics, 16, 54, 194, 72, t)
  max_card:set_style({ bg_color = t.panel_alt, border_width = 0 })
  label(max_card, "最高分", 12, 10, t.muted, 90, 22)
  view.max_score = label(max_card, "--", 114, 10, t.text, 68, 32)
  local min_card = panel(metrics, 226, 54, 194, 72, t)
  min_card:set_style({ bg_color = t.panel_alt, border_width = 0 })
  label(min_card, "最小分", 12, 10, t.muted, 90, 22)
  view.min_score = label(min_card, "--", 114, 10, t.text, 68, 32)
  local consistency = panel(metrics, 436, 54, 220, 72, t)
  consistency:set_style({ bg_color = t.panel_alt, border_width = 0 })
  label(consistency, "consistency", 12, 10, t.muted, 110, 22)
  view.consistency = label(consistency, "--", 124, 10, t.text, 84, 32)
  label(metrics, "数据状态", 16, 152, t.muted, 110, 24)
  view.quality = label(metrics, "--", 138, 152, t.text, 180, 24)
  label(metrics, "代偿", 350, 152, t.muted, 80, 24)
  view.compensation = label(metrics, "--", 438, 152, t.text, 208, 44)

  local summary = panel(root, 20, 324, 984, 48, t)
  summary:set_style({ bg_color = t.panel_alt, border_width = 0 })
  label(summary, "Session", 14, 13, t.accent, 90, 24)
  view.session = label(summary, state.bound_result_session_id or "--",
    110, 13, t.text, 280, 24)
  view.summary = label(summary, app.state_module.get_result_feedback(state),
    410, 13, t.text, 554, 24)

  local report = panel(root, 20, 388, 984, 116, t)
  report:set_style({ bg_color = t.panel_alt, border_width = 0, pad = 14 })
  report:set_scroll({ dir = "ver", scrollbar = "auto" })
  view.report = remember(lvgl.label(report, {
    text = "训练数据已保存，评分处理中",
    x = 0, y = 0, w = 944, h = 90,
    text_color = t.muted, text_font = "rehab",
    text_align = "left", long_mode = "wrap",
  }))

  local buttons = panel(root, 20, 520, 984, 60, t)
  view.home_button = remember(lvgl.button(buttons, {
    text = "返回首页", x = 822, y = 8, w = 142, h = 48,
    bg_color = t.accent, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  bind(view.home_button, function()
    -- Returning home only clears this UI run's result binding. It never
    -- changes selected_task.json or any Session/score file.
    app.state_module.clear_result_binding(state)
    app.navigate("home")
  end)
  return root
end

function M.refresh(app)
  if not view then return end
  local state = app.state
  local t = app.theme
  local result = state.score_result or {}
  local status_text, status_style = result_status_text(state)

  view.task_name:set_text(state.result_task_name ~= "" and
    state.result_task_name or "训练结果")
  view.session:set_text(state.bound_result_session_id ~= "" and
    state.bound_result_session_id or "--")
  view.state_label:set_text(status_text)
  view.state_label:set_style({ text_color = t[status_style] or t.muted })
  view.average:set_text(score_text(result.average_score))
  view.max_score:set_text(score_text(result.max_score))
  view.min_score:set_text(score_text(result.min_score))
  view.consistency:set_text(score_text(result.consistency_score))
  view.repetitions:set_text(string.format("动作次数 %s / %s",
    tostring(result.repetition_count or "--"),
    tostring(result.target_repetition_count or "--")))
  view.quality:set_text(tostring(result.data_quality_level or "--"))
  view.compensation:set_text(result.compensation_detected == true and
    string.format("检测到（%d 次）",
      tonumber(result.compensation_action_count) or 0) or "未检测到")
  view.completed_at:set_text("完成时间 " ..
    tostring(result.completed_at or "--"))
  view.summary:set_text(app.state_module.get_result_feedback(state))

  if state.score_state == "succeeded" then
    view.report:set_text("评分报告\n" .. warnings_text(result))
    view.report:set_style({ text_color = t.text })
  elseif state.score_state == "failed" or state.score_state == "invalid" or
      state.score_state == "session_mismatch" or state.score_error then
    view.report:set_text("训练数据已保存。\n评分状态：" ..
      tostring(state.score_state) .. "\n提示：" ..
      tostring(state.score_error or "评分结果暂不可用"))
    view.report:set_style({ text_color = t.warning })
  else
    view.report:set_text("训练数据已保存。\n当前评分状态：" ..
      tostring(state.score_state or "pending") ..
      "\n本页只查看当前 Session，不显示其他训练分数。")
    view.report:set_style({ text_color = t.muted })
  end
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
