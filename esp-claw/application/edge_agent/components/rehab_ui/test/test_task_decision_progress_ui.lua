local objects = {}

package.preload.lvgl = function()
  local function new_object(kind, parent, props)
    local object = {
      kind = kind,
      parent = parent,
      props = props,
      initial_text = props.text,
      text = props.text,
      valid = true,
    }
    function object:set_text(text) self.text = text end
    function object:set_pos(x, y) self.props.x = x self.props.y = y end
    function object:is_valid() return self.valid end
    function object:delete() self.valid = false end
    objects[#objects + 1] = object
    return object
  end
  return {
    container = function(parent, props)
      return new_object("container", parent, props)
    end,
    label = function(parent, props)
      return new_object("label", parent, props)
    end,
  }
end

local ui_dir = (arg and arg[1] and arg[1] ~= "") and arg[1] or "."
package.path = ui_dir .. "/?.lua;" .. package.path

local page = require("page_task_decision_progress")
local projection = {
  title = "正在生成康复任务",
  stage_text = "DeepSeek 正在分析候选任务…",
  elapsed_sec = 87,
  completed_stage_count = 3,
  generation = 9,
}
local app = {
  screen = {},
  theme = {
    screen_width = 1024,
    screen_height = 600,
    bg = 0,
    normal = 1,
    muted = 2,
    accent = 3,
  },
  state = {},
  state_module = {
    get_task_decision_view = function() return projection end,
  },
}

local function expect(value, message)
  if not value then error(message or "expectation failed", 2) end
end

local function checked_count()
  local count = 0
  for _, object in ipairs(objects) do
    if string.sub(object.text or "", 1, #"✓") == "✓" then
      count = count + 1
    end
  end
  return count
end

page.create(app)
page.refresh(app)
expect(checked_count() == 3,
  "MODEL_WAIT projection must show three completed stages")

local elapsed = nil
for _, object in ipairs(objects) do
  if object.initial_text == "0s" then elapsed = object end
end
expect(elapsed and elapsed.text == "87s",
  "elapsed seconds must update independently")

projection.completed_stage_count = 4
projection.stage_text = "正在校验任务方案…"
page.refresh(app)
expect(checked_count() == 4,
  "validation projection must add candidate analysis completion")

projection.completed_stage_count = 3
projection.stage_text = "DeepSeek 正在分析候选任务…"
page.refresh(app)
expect(checked_count() == 4,
  "same-generation retry must keep completed stages visible")

projection.generation = 10
projection.completed_stage_count = 0
projection.stage_text = "正在启动任务决策…"
projection.elapsed_sec = 0
page.refresh(app)
expect(checked_count() == 0 and elapsed.text == "0s",
  "new generation must reset the presentation stage list")

page.destroy()
print("task decision progress UI tests: PASS")
