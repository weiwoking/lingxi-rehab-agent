print("P4_REHAB_UI_ENTRY_BEGIN")

local ok, ui_or_err = pcall(require, "esp_claw_rehab_ui")
if not ok then
  print("P4_REHAB_UI_ENTRY_FAILED reason=require:" .. tostring(ui_or_err))
  error(ui_or_err)
end

local ui = ui_or_err
if type(ui) ~= "table" or type(ui.start) ~= "function" then
  print("P4_REHAB_UI_ENTRY_FAILED reason=missing_start")
  error("esp_claw_rehab_ui.start is unavailable")
end

local started, err = pcall(ui.start)
if not started then
  print("P4_REHAB_UI_ENTRY_FAILED reason=start:" .. tostring(err))
  error(err)
end
