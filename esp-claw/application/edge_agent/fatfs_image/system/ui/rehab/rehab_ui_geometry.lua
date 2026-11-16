local M = {}

local function positive_finite(value)
  local number = tonumber(value)
  if type(number) ~= "number" or number ~= number or
      number == math.huge or number == -math.huge or number <= 0 then
    return nil
  end
  return number
end

function M.format_height(geometry, field)
  if type(geometry) ~= "table" or geometry.available ~= true or
      geometry.valid ~= true then
    return "高度不可用"
  end
  local millimetres = positive_finite(geometry[field])
  if not millimetres then
    return "--"
  end
  return string.format("%.1f cm", millimetres / 10.0)
end

function M.format_distance(geometry)
  if type(geometry) ~= "table" or geometry.available ~= true or
      geometry.valid ~= true then
    return "距离不可用"
  end
  local status = string.lower(tostring(geometry.distance_status or ""))
  if status == "" or string.find(status, "unverified", 1, true) then
    return "距离不可用"
  end
  local millimetres = positive_finite(geometry.distance_mm)
  if not millimetres then
    return "距离不可用"
  end
  return string.format("%.1f cm", millimetres / 10.0)
end

function M.object_class(class_name)
  local names = {
    bottle = "杯子",
    cup = "杯子",
    ["sports ball"] = "排球",
    ball = "排球",
    volleyball = "排球",
    book = "书本",
  }
  local value = tostring(class_name or "")
  return names[value] or (value ~= "" and value or "--")
end

local function target_height_value(geometry, field)
  local millimetres = type(geometry) == "table" and
    positive_finite(geometry[field]) or nil
  return millimetres and string.format("%.1f cm", millimetres / 10.0) or
    "未知"
end

function M.target_height_lines(geometry, class_name)
  local name = M.object_class(class_name)
  return string.format("%s自身高度：%s", name,
      target_height_value(geometry, "object_height_mm")),
    string.format("%s离地高度：%s", name,
      target_height_value(geometry, "support_height_mm"))
end

return M
