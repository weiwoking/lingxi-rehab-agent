// SPDX-License-Identifier: Apache-2.0
#include "rehab_ui.h"

#include <stdlib.h>
#include <string.h>

#include "cap_lua.h"
#include "lua.h"
#include "lauxlib.h"
#include "lua_image.h"

#define REHAB_PERSON_FOLLOW_SNAPSHOT_LUA_MODULE "rehab_person_follow_snapshot"

typedef struct {
    rehab_person_follow_handle_t handle;
    rehab_person_follow_snapshot_frame_t frame;
} rehab_ui_snapshot_lease_t;

static rehab_person_follow_handle_t s_person_follow;
static bool s_registered;

static void snapshot_frame_release(void *ctx, const uint8_t *data)
{
    rehab_ui_snapshot_lease_t *lease = ctx;
    if (lease == NULL) {
        return;
    }
    if (lease->frame.image_data == data) {
        rehab_person_follow_snapshot_release(lease->handle, &lease->frame);
    }
    free(lease);
}

static int lua_snapshot_acquire(lua_State *L)
{
    lua_Integer requested = luaL_checkinteger(L, 1);
    if (requested <= 0 || s_person_follow == NULL) {
        lua_pushnil(L);
        lua_pushliteral(L, "snapshot_unavailable");
        return 2;
    }

    rehab_ui_snapshot_lease_t *lease = calloc(1, sizeof(*lease));
    if (lease == NULL) {
        return luaL_error(L, "person-follow snapshot lease allocation failed");
    }
    lease->handle = s_person_follow;
    esp_err_t err = rehab_person_follow_snapshot_acquire(
        lease->handle, (uint64_t)requested, &lease->frame);
    if (err != ESP_OK) {
        free(lease);
        lua_pushnil(L);
        lua_pushstring(L, esp_err_to_name(err));
        return 2;
    }

    lua_image_frame_info_t info = {
        .width = (int)lease->frame.info.width,
        .height = (int)lease->frame.info.height,
        .bytes = lease->frame.info.image_length,
        .timestamp_us = lease->frame.info.capture_timestamp_us,
    };
    strlcpy(info.pixel_format, "JPEG", sizeof(info.pixel_format));
    err = lua_image_push_frame(L, lease->frame.image_data,
                               lease->frame.info.image_length, &info,
                               snapshot_frame_release, lease);
    if (err != ESP_OK) {
        rehab_person_follow_snapshot_release(lease->handle, &lease->frame);
        free(lease);
        lua_pushnil(L);
        lua_pushstring(L, esp_err_to_name(err));
        return 2;
    }
    return 1;
}

static int luaopen_rehab_person_follow_snapshot(lua_State *L)
{
    static const luaL_Reg functions[] = {
        {"acquire", lua_snapshot_acquire},
        {NULL, NULL},
    };
    lua_newtable(L);
    luaL_setfuncs(L, functions, 0);
    return 1;
}

esp_err_t rehab_ui_person_follow_lua_register(void)
{
    if (s_registered) {
        return ESP_OK;
    }
    esp_err_t err = cap_lua_register_module(
        REHAB_PERSON_FOLLOW_SNAPSHOT_LUA_MODULE,
        luaopen_rehab_person_follow_snapshot);
    if (err == ESP_OK) {
        s_registered = true;
    }
    return err;
}

void rehab_ui_person_follow_lua_bind(
    rehab_person_follow_handle_t person_follow)
{
    s_person_follow = person_follow;
}
