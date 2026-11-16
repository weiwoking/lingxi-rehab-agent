"""Static architecture checks for the score-event fatigue integration."""
from pathlib import Path
import subprocess


COMPONENT = Path(__file__).resolve().parents[1]
APP = COMPONENT.parents[1]
REPO = APP.parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


fatigue = read(COMPONENT / "src" / "rehab_fatigue.c")
fatigue_rules = read(COMPONENT / "src" / "rehab_fatigue_rules.c")
fatigue_rules_header = read(COMPONENT / "src" / "rehab_fatigue_rules.h")
fatigue_public_header = read(COMPONENT / "include" / "rehab_fatigue.h")
capabilities = read(COMPONENT / "src" / "rehab_capabilities.c")
system = read(COMPONENT / "src" / "rehab_system.c")
score_core = read(APP / "components" / "rehab_score_bridge" / "src" /
                  "rehab_score_bridge_core.c")
score_header = read(APP / "components" / "rehab_score_bridge" / "include" /
                    "rehab_score_bridge.h")

for forbidden in ("opendir(", "readdir(", "patient_summary",
                  "activation_baseline", "pending_session"):
    assert forbidden not in fatigue, f"forbidden fatigue behavior: {forbidden}"

download = score_core[score_core.index("static esp_err_t download_result("):
                      score_core.index("static esp_err_t poll_job(")]
assert download.index("rehab_score_storage_write_result(") < download.index(
    "REHAB_SCORE_STATE_SUCCEEDED") < download.index("s_success_observer(")
assert "parse_err == ESP_OK && parsed->valid &&" in download
assert "parsed->average_score_available" in download
for forbidden_gate in ("warning_count", "warnings_summary", "data_quality_level"):
    assert forbidden_gate not in download
assert score_core.count("rehab_training_register_finalized_callback(") == 1
assert "rehab_score_success_observer_t" in score_header

query = capabilities[capabilities.index("static esp_err_t rehab_score_query_execute("):
                     capabilities.index("static esp_err_t rehab_fatigue_status_execute(")]
assert "rehab_fatigue_" not in query

assert '.id = "rehab_fatigue_status"' in capabilities
descriptor = capabilities[capabilities.index('.id = "rehab_fatigue_status"'):
                          capabilities.index('.id = "rehab_score_query"')]
assert ".cap_flags = CLAW_CAP_FLAG_RESTRICTED" in descriptor
assert "CLAW_CAP_FLAG_CALLABLE_BY_LLM" not in descriptor

assert system.index("rehab_fatigue_init()") < system.index(
    "rehab_score_bridge_init()")
assert "fatigue unavailable; rehab continues" in system
assert "FATIGUE_UPDATE_FAILED" in system

settle = fatigue[fatigue.index("static esp_err_t fatigue_settle_locked("):
                 fatigue.index("esp_err_t rehab_fatigue_init(")]
assert "fatigue_time_now(&now, date)" in settle
assert "rehab_fatigue_rules_rollover_day(&s_fatigue.state, date)" in settle
assert "fatigue_save_locked()" in settle
assert "extract_session_date" not in settle

score_succeeded = fatigue[
    fatigue.index("esp_err_t rehab_fatigue_on_score_succeeded("):
    fatigue.index("esp_err_t rehab_fatigue_before_training(")]
assert "fatigue_accounting_date_is_valid(s_fatigue.state.count_date)" in score_succeeded
assert "memcpy(date, s_fatigue.state.count_date, sizeof(date))" in score_succeeded
assert "return ESP_OK;" in score_succeeded
assert "rehab_fatigue_rules_extract_session_date(" not in score_succeeded

stop = capabilities[capabilities.index("static esp_err_t rehab_training_stop_execute("):
                    capabilities.index("static esp_err_t rehab_training_status_execute(")]
assert stop.index("rehab_training_stop(") < stop.index(
    "rehab_fatigue_on_training_stopped(")
assert capabilities.count("rehab_fatigue_before_training(") == 2
assert "#define REHAB_FATIGUE_LIMIT                40" in fatigue_rules_header
assert "#define REHAB_FATIGUE_STATUS_LIMIT           40" in fatigue_public_header
assert "return fatigue < REHAB_FATIGUE_LIMIT;" in fatigue_rules
assert fatigue.count("rehab_fatigue_rules_training_allowed(") == 2

changed = subprocess.run(
    ["git", "-C", str(REPO), "diff", "--name-only"],
    check=True, capture_output=True, text=True).stdout.splitlines()
protected = (
    # Stage 8C explicitly owns the Rehab UI and its generated font. Keep the
    # fatigue regression guard on the unrelated LVGL module core only.
    "components/lua_modules/lua_module_lvgl/",
)
assert not any(path.replace("\\", "/").startswith(protected) for path in changed)

print("rehab fatigue static contracts: all tests passed")

