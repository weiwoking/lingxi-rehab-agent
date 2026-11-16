local lvgl = require("lvgl")
local task_decision = require("rehab_task_decision_ui")

local M = {}
local view = nil

local PAGE_BYTES = 768
local SELECTOR_PAGE_COUNT = 15
local SELECTOR_COLUMNS = 5
local render_page

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

local function continuation_byte(value)
  return value and value >= 0x80 and value < 0xC0
end

local function build_pages(text)
  local pages = {}
  local start_index = 1
  while start_index <= #text do
    local finish_index = math.min(#text, start_index + PAGE_BYTES - 1)
    while finish_index >= start_index and finish_index < #text and
        continuation_byte(string.byte(text, finish_index + 1)) do
      finish_index = finish_index - 1
    end
    if finish_index < start_index then
      finish_index = math.min(#text, start_index + PAGE_BYTES - 1)
    end
    pages[#pages + 1] = string.sub(text, start_index, finish_index)
    start_index = finish_index + 1
  end
  return pages
end

local function page_height(text)
  local _, explicit_lines = string.gsub(text, "\n", "")
  local wrapped_lines = math.max(1, math.ceil(#text / 45))
  return math.max(300, math.min(900,
    (wrapped_lines + explicit_lines) * 26 + 24))
end

local function close_page_selector()
  if not view or not view.page_selector then return end
  if view.page_selector:is_valid() then
    pcall(function() view.page_selector:delete() end)
  end
  view.page_selector = nil
  view.selector_range = nil
end

local function render_selector_range()
  if not view or not view.page_selector or
      not view.page_selector:is_valid() then return end

  view.selector_grid:clean()
  local first = view.selector_range
  local last = math.min(#view.pages, first + SELECTOR_PAGE_COUNT - 1)
  view.selector_range_value:set_text(string.format(
    "第 %d - %d 页（共 %d 页）", first, last, #view.pages))

  for target_page = first, last do
    local offset = target_page - first
    local column = offset % SELECTOR_COLUMNS
    local row = math.floor(offset / SELECTOR_COLUMNS)
    local page_number = target_page
    local button = lvgl.button(view.selector_grid, {
      text = tostring(page_number),
      x = 12 + column * 154, y = 10 + row * 66,
      w = 142, h = 54,
      bg_color = page_number == view.page_index and
        view.theme.accent or view.theme.button_dark,
      text_color = view.theme.text, text_font = "rehab",
      text_align = "center", radius = view.theme.radius,
    })
    bind(button, function()
      view.page_index = page_number
      close_page_selector()
      render_page()
    end)
  end

  view.selector_previous:set_style(view.theme.button_style(
    view.theme.button_dark,
    first > 1 and view.theme.text or view.theme.muted))
  view.selector_next:set_style(view.theme.button_style(
    view.theme.button_dark,
    last < #view.pages and view.theme.text or view.theme.muted))
end

local function open_page_selector()
  if not view or not view.available or #view.pages == 0 or
      view.page_selector then return end

  local t = view.theme
  view.selector_range =
    math.floor((view.page_index - 1) / SELECTOR_PAGE_COUNT) *
      SELECTOR_PAGE_COUNT + 1
  view.page_selector = lvgl.container(view.root, {
    x = 80, y = 64, w = 864, h = 458,
    bg_color = t.panel_alt, bg_opa = 255,
    border_color = t.accent, border_width = 2,
    radius = t.radius, pad = 0,
  })
  label(view.page_selector, "跳转到第几页", 16, 14,
    t.accent, 832, 30, "center")
  view.selector_range_value = label(view.page_selector, "", 16, 50,
    t.muted, 832, 28, "center")
  view.selector_grid = lvgl.container(view.page_selector, {
    x = 36, y = 82, w = 792, h = 208,
    bg_color = t.panel, bg_opa = 255,
    border_color = t.border, border_width = 1,
    radius = t.radius, pad = 0,
  })
  view.selector_previous = lvgl.button(view.page_selector, {
    text = "上一组", x = 36, y = 306, w = 244, h = 52,
    bg_color = t.button_dark, text_color = t.muted,
    text_font = "rehab", text_align = "center", radius = t.radius,
  })
  view.selector_next = lvgl.button(view.page_selector, {
    text = "下一组", x = 310, y = 306, w = 244, h = 52,
    bg_color = t.button_dark, text_color = t.muted,
    text_font = "rehab", text_align = "center", radius = t.radius,
  })
  local cancel = lvgl.button(view.page_selector, {
    text = "取消", x = 584, y = 306, w = 244, h = 52,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  })
  label(view.page_selector,
    "每组最多 15 页，点击页码直接跳转。", 16, 382,
    t.muted, 832, 30, "center")

  bind(view.selector_previous, function()
    if view.selector_range > 1 then
      view.selector_range = math.max(1,
        view.selector_range - SELECTOR_PAGE_COUNT)
      render_selector_range()
    end
  end)
  bind(view.selector_next, function()
    if view.selector_range + SELECTOR_PAGE_COUNT <= #view.pages then
      view.selector_range = view.selector_range + SELECTOR_PAGE_COUNT
      render_selector_range()
    end
  end)
  bind(cancel, close_page_selector)
  render_selector_range()
end

render_page = function()
  if not view or not view.available then return end
  view.body:clean()
  local text = view.pages[view.page_index] or ""
  view.reasoning_label = lvgl.label(view.body, {
    text = text, x = 14, y = 12, w = 928, h = page_height(text),
    text_color = view.theme.text, text_font = "rehab",
    text_align = "left", long_mode = "wrap",
  })
  view.page_value:set_text(string.format("第 %d / %d 页",
    view.page_index, #view.pages))
  view.previous:set_style(view.theme.button_style(
    view.theme.button_dark,
    view.page_index > 1 and view.theme.text or view.theme.muted))
  view.next:set_style(view.theme.button_style(
    view.theme.button_dark,
    view.page_index < #view.pages and view.theme.text or view.theme.muted))
end

function M.create(app)
  local t = app.theme
  local data = task_decision.reasoning(app.reasoning_expected_decision_id)
  view = {
    objects = {}, events = {}, theme = t,
    available = type(data) == "table" and data.available == true and
      type(data.reasoning_content) == "string" and
      data.reasoning_content ~= "",
    page_index = 1,
  }

  local root = remember(lvgl.container(app.screen, {
    x = 0, y = 0, w = t.screen_width, h = t.screen_height,
    bg_color = t.bg, bg_opa = 255, border_width = 0, radius = 0, pad = 0,
  }))
  view.root = root
  label(root, "AI决策分析", 20, 14, t.accent, 984, 36, "center")

  local meta = panel(root, 20, 58, 984, 92, t)
  local task_name = view.available and tostring(data.task_name or "--") or "--"
  local latency = view.available and tonumber(data.latency_sec) or 0
  label(meta, "任务：" .. task_name, 16, 12, t.text, 610, 28)
  label(meta, string.format("决策耗时：%.0fs", latency or 0),
    650, 12, t.muted, 300, 28, "right")
  label(meta, "模型：DeepSeek", 16, 50, t.muted, 400, 26)

  view.body = panel(root, 20, 164, 984, 352, t)
  view.body:set_scroll({ dir = "ver", scrollbar = "auto" })

  local back = remember(lvgl.button(root, {
    text = "返回", x = 20, y = 532, w = 180, h = 52,
    bg_color = t.button_dark, text_color = t.text,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.previous = remember(lvgl.button(root, {
    text = "上一页", x = 214, y = 532, w = 150, h = 52,
    bg_color = t.button_dark, text_color = t.muted,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.page_value = label(root, "第 0 / 0 页", 378, 546,
    t.muted, 160, 26, "center")
  view.next = remember(lvgl.button(root, {
    text = "下一页", x = 552, y = 532, w = 150, h = 52,
    bg_color = t.button_dark, text_color = t.muted,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))
  view.jump = remember(lvgl.button(root, {
    text = "跳转", x = 716, y = 532, w = 288, h = 52,
    bg_color = t.button_dark, text_color = t.muted,
    text_font = "rehab", text_align = "center", radius = t.radius,
  }))

  bind(back, function() app.return_task_decision_reasoning() end)
  bind(view.previous, function()
    if view.page_index > 1 then
      view.page_index = view.page_index - 1
      render_page()
    end
  end)
  bind(view.next, function()
    if view.page_index < #view.pages then
      view.page_index = view.page_index + 1
      render_page()
    end
  end)
  bind(view.jump, open_page_selector)

  if view.available then
    view.pages = build_pages(data.reasoning_content)
    view.jump:set_style(t.button_style(t.button_dark, t.text))
    render_page()
  else
    view.pages = {}
    label(view.body, "暂无AI分析记录", 16, 54,
      t.warning, 948, 42, "center")
    label(view.body, "文件缺失、损坏或版本不支持不会影响正式任务。",
      16, 112, t.muted, 948, 34, "center")
  end
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
