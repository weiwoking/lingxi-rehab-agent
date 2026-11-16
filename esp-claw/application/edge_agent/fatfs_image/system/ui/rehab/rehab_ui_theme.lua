local M = {
  screen_width = 1024,
  screen_height = 600,
  event_slice_ms = 250,
  margin = 20,
  gap = 14,
  radius = 6,
  bg = "#071019",
  panel = "#101a27",
  panel_alt = "#172333",
  text = "#f4f7fb",
  muted = "#8fa0b5",
  border = "#26364a",
  normal = "#34c77b",
  danger = "#d94c5c",
  warning = "#f0a52b",
  accent = "#36a3ff",
  scan_idle = "#d7dee8",
  scan_active = "#5aaee8",
  scan_complete = "#5fbf91",
  button_dark = "#29384b",
  font_path = "/system/ui/fonts/rehab_cn_20.bin",
  font_glyph_count = 3900,
  font_loaded = false,
  font_error = nil,
}

function M.load_fonts(lvgl)
  if M.font_loaded then
    return true
  end
  print("P4_REHAB_UI_FONT_LOAD_BEGIN")
  local ok, result = pcall(lvgl.load_font, M.font_path, M.font_glyph_count)
  if ok and type(result) == "table" and result.ok == true and result.loaded == true then
    M.font_loaded = true
    M.font_error = nil
    print(string.format("P4_REHAB_UI_FONT_GLYPH_CHECK missing=%d", 0))
    print("P4_REHAB_UI_FONT_APPLIED")
    return true
  end
  M.font_error = ok and (result and result.message or "unknown") or tostring(result)
  print("P4_REHAB_UI_FONT_LOAD_FAILED reason=" .. tostring(M.font_error))
  print(string.format("P4_REHAB_UI_FONT_GLYPH_CHECK missing=%d", M.font_glyph_count))
  return false
end

function M.text_style(color, align)
  return {
    text_color = color or M.text,
    text_font = "rehab",
    text_align = align or "left",
  }
end

function M.apply_text_style(obj, color, align)
  if obj and obj.is_valid and obj:is_valid() then
    obj:set_style(M.text_style(color, align))
  end
end

function M.button_style(bg, color)
  return {
    bg_color = bg or M.button_dark,
    text_color = color or M.text,
    text_font = "rehab",
    text_align = "center",
    radius = M.radius,
  }
end

return M
