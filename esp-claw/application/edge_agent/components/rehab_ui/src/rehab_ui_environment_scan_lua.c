// SPDX-License-Identifier: Apache-2.0
#include "rehab_ui.h"

#include "cap_lua.h"
#include "lua.h"
#include "lauxlib.h"

#define REHAB_ENVIRONMENT_SCAN_UI_LUA_MODULE "rehab_environment_scan_ui"

static rehab_environment_scan_handle_t s_environment_scan;
static bool s_registered;

static void push_status(
    lua_State *L,
    esp_err_t err,
    const rehab_environment_scan_status_t *status)
{
    lua_newtable(L);

    lua_pushboolean(L, err == ESP_OK);
    lua_setfield(L, -2, "ok");
    lua_pushstring(L, esp_err_to_name(err));
    lua_setfield(L, -2, "error");

    if (status == NULL) {
        lua_pushliteral(L, "IDLE");
        lua_setfield(L, -2, "state");
        lua_pushboolean(L, false);
        lua_setfield(L, -2, "active");
        return;
    }

    lua_pushstring(L, rehab_environment_scan_state_name(status->state));
    lua_setfield(L, -2, "state");
    lua_pushboolean(L, status->active);
    lua_setfield(L, -2, "active");
    lua_pushboolean(L, status->cancel_requested);
    lua_setfield(L, -2, "cancel_requested");
    lua_pushstring(L, status->scan_id);
    lua_setfield(L, -2, "scan_id");
    lua_pushinteger(L, status->generation);
    lua_setfield(L, -2, "generation");
    lua_pushinteger(L, status->direction_index);
    lua_setfield(L, -2, "direction_index");
    lua_pushinteger(L, status->directions_completed);
    lua_setfield(L, -2, "directions_completed");
    lua_pushinteger(L, status->directions_total);
    lua_setfield(L, -2, "directions_total");
    lua_pushinteger(L, status->warmup_completed);
    lua_setfield(L, -2, "warmup_completed");
    lua_pushinteger(L, status->warmup_total);
    lua_setfield(L, -2, "warmup_total");
    lua_pushstring(L, rehab_scan_transfer_state_name(status->upload_state));
    lua_setfield(L, -2, "upload_state");
    lua_pushboolean(L, status->retryable);
    lua_setfield(L, -2, "retryable");
    lua_pushinteger(L, status->last_error_code);
    lua_setfield(L, -2, "last_error_code");
    lua_pushstring(L, status->last_error);
    lua_setfield(L, -2, "last_error");
    lua_pushstring(L, status->upload_error);
    lua_setfield(L, -2, "upload_error");
    lua_pushinteger(L, status->package_length);
    lua_setfield(L, -2, "package_length");
    lua_pushstring(L, status->package_sha256);
    lua_setfield(L, -2, "package_sha256");
    lua_pushstring(L, status->scene_result_path);
    lua_setfield(L, -2, "scene_result_path");
}

static int lua_environment_scan_start(lua_State *L)
{
    rehab_environment_scan_status_t status = {0};
    bool busy = false;
    esp_err_t err = s_environment_scan == NULL
        ? ESP_ERR_INVALID_STATE
        : rehab_environment_scan_start(s_environment_scan, &busy, &status);

    push_status(L, err, &status);
    lua_pushboolean(L, busy);
    lua_setfield(L, -2, "busy");
    return 1;
}

static int lua_environment_scan_status(lua_State *L)
{
    rehab_environment_scan_status_t status = {0};
    esp_err_t err = s_environment_scan == NULL
        ? ESP_ERR_INVALID_STATE
        : rehab_environment_scan_get_status(s_environment_scan, &status);

    push_status(L, err, &status);
    return 1;
}

static int lua_environment_scan_cancel(lua_State *L)
{
    rehab_environment_scan_status_t status = {0};
    bool already_idle = false;
    esp_err_t err = s_environment_scan == NULL
        ? ESP_ERR_INVALID_STATE
        : rehab_environment_scan_cancel(
            s_environment_scan, &already_idle, &status);

    push_status(L, err, &status);
    lua_pushboolean(L, already_idle);
    lua_setfield(L, -2, "already_idle");
    return 1;
}

static int luaopen_rehab_environment_scan_ui(lua_State *L)
{
    static const luaL_Reg functions[] = {
        {"start", lua_environment_scan_start},
        {"status", lua_environment_scan_status},
        {"cancel", lua_environment_scan_cancel},
        {NULL, NULL},
    };
    lua_newtable(L);
    luaL_setfuncs(L, functions, 0);
    return 1;
}

esp_err_t rehab_ui_environment_scan_lua_register(void)
{
    if (s_registered) {
        return ESP_OK;
    }
    esp_err_t err = cap_lua_register_module(
        REHAB_ENVIRONMENT_SCAN_UI_LUA_MODULE,
        luaopen_rehab_environment_scan_ui);
    if (err == ESP_OK) {
        s_registered = true;
    }
    return err;
}

void rehab_ui_environment_scan_lua_bind(
    rehab_environment_scan_handle_t environment_scan)
{
    s_environment_scan = environment_scan;
}
