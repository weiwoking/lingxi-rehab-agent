local lvgl = require("lvgl")

local M = {}
local view = nil

local COMPLETED_STAGE_TEXT = {
  "✓ 已加载患者状态",
  "✓ 已分析环境信息",
  "✓ 已生成候选任务",
  "✓ 已完成候选任务分析",
  "✓ 已完成任务校验",
}

local function remember(obj)
  view.objects[#view.objects + 1] = obj
  return obj
end

function M.create(app)
  local t = app.theme
  view = {
    objects = {}, events = {}, completed = {}, completed_count = 0,
    generation = 0,
  }
  local root = remember(lvgl.container(app.screen, {
    x = 0, y = 0, w = t.screen_width, h = t.screen_height,
    bg_color = t.bg, bg_opa = 255, border_width = 0, radius = 0, pad = 0,
  }))
  view.root = root
  view.title = remember(lvgl.label(root, {
    text = "正在生成康复任务", x = 80, y = 34, w = 864, h = 52,
    text_color = t.normal, text_font = "rehab", text_align = "center",
  }))
  for index = 1, #COMPLETED_STAGE_TEXT do
    view.completed[index] = remember(lvgl.label(root, {
      text = "", x = 150, y = 108 + (index - 1) * 42, w = 724, h = 32,
      text_color = t.normal, text_font = "rehab", text_align = "left",
    }))
  end
  view.current_label = remember(lvgl.label(root, {
    text = "当前阶段：", x = 150, y = 128, w = 724, h = 28,
    text_color = t.muted, text_font = "rehab", text_align = "left",
  }))
  view.detail = remember(lvgl.label(root, {
    text = "正在启动任务决策…", x = 150, y = 162, w = 724, h = 44,
    text_color = t.accent, text_font = "rehab", text_align = "left",
  }))
  remember(lvgl.label(root, {
    text = "决策耗时：", x = 80, y = 488, w = 864, h = 28,
    text_color = t.muted, text_font = "rehab", text_align = "center",
  }))
  view.elapsed = remember(lvgl.label(root, {
    text = "0s", x = 80, y = 522, w = 864, h = 44,
    text_color = t.accent, text_font = "rehab", text_align = "center",
  }))
  return root
end

function M.refresh(app)
  if not view then return end
  local data = app.state_module.get_task_decision_view(app.state)
  if data.generation > 0 and data.generation ~= view.generation then
    view.generation = data.generation
    view.completed_count = 0
  end
  view.completed_count = math.max(view.completed_count,
    data.completed_stage_count or 0)
  for index, label in ipairs(view.completed) do
    label:set_text(index <= view.completed_count and
      COMPLETED_STAGE_TEXT[index] or "")
  end
  local current_y = 128 + view.completed_count * 42
  view.current_label:set_pos(150, current_y)
  view.detail:set_pos(150, current_y + 34)
  view.title:set_text(data.title)
  view.detail:set_text(data.stage_text)
  view.elapsed:set_text(string.format("%ds", data.elapsed_sec))
end

function M.tick(app, now_ms)
  local _ = app
  local __ = now_ms
end

function M.destroy()
  if not view then return end
  if view.root and view.root:is_valid() then
    pcall(function() view.root:delete() end)
  end
  view = nil
end

return M
