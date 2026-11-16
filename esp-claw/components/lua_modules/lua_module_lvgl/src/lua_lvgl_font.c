/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "lua_lvgl_private.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>

#define LUA_LVGL_FONT_MAX_FILE_BYTES (256 * 1024)
#define LUA_LVGL_FONT_FS_LETTER 'R'

static const char *TAG = "lua_lvgl_font";
static lv_fs_drv_t s_rehab_font_fs_drv;
static bool s_rehab_font_fs_registered;

static bool lua_lvgl_font_fs_ready(lv_fs_drv_t *drv)
{
    (void)drv;
    return true;
}

static void *lua_lvgl_font_fs_open(lv_fs_drv_t *drv, const char *path, lv_fs_mode_t mode)
{
    (void)drv;
    if ((mode & LV_FS_MODE_WR) != 0 || !path) {
        return NULL;
    }
    return fopen(path, "rb");
}

static lv_fs_res_t lua_lvgl_font_fs_close(lv_fs_drv_t *drv, void *file_p)
{
    (void)drv;
    return file_p && fclose((FILE *)file_p) == 0 ? LV_FS_RES_OK : LV_FS_RES_UNKNOWN;
}

static lv_fs_res_t lua_lvgl_font_fs_read(lv_fs_drv_t *drv, void *file_p,
                                         void *buf, uint32_t btr, uint32_t *br)
{
    (void)drv;
    if (!file_p || !buf || !br) {
        return LV_FS_RES_INV_PARAM;
    }
    *br = (uint32_t)fread(buf, 1, btr, (FILE *)file_p);
    return ferror((FILE *)file_p) ? LV_FS_RES_UNKNOWN : LV_FS_RES_OK;
}

static lv_fs_res_t lua_lvgl_font_fs_seek(lv_fs_drv_t *drv, void *file_p,
                                         uint32_t pos, lv_fs_whence_t whence)
{
    int origin;

    (void)drv;
    if (!file_p) {
        return LV_FS_RES_INV_PARAM;
    }
    switch (whence) {
    case LV_FS_SEEK_SET:
        origin = SEEK_SET;
        break;
    case LV_FS_SEEK_CUR:
        origin = SEEK_CUR;
        break;
    case LV_FS_SEEK_END:
        origin = SEEK_END;
        break;
    default:
        return LV_FS_RES_INV_PARAM;
    }
    return fseek((FILE *)file_p, (long)pos, origin) == 0 ? LV_FS_RES_OK : LV_FS_RES_UNKNOWN;
}

static lv_fs_res_t lua_lvgl_font_fs_tell(lv_fs_drv_t *drv, void *file_p, uint32_t *pos_p)
{
    long pos;

    (void)drv;
    if (!file_p || !pos_p) {
        return LV_FS_RES_INV_PARAM;
    }
    pos = ftell((FILE *)file_p);
    if (pos < 0) {
        return LV_FS_RES_UNKNOWN;
    }
    *pos_p = (uint32_t)pos;
    return LV_FS_RES_OK;
}

static void lua_lvgl_register_font_fs(void)
{
    if (s_rehab_font_fs_registered || lv_fs_get_drv(LUA_LVGL_FONT_FS_LETTER)) {
        s_rehab_font_fs_registered = true;
        return;
    }
    lv_fs_drv_init(&s_rehab_font_fs_drv);
    s_rehab_font_fs_drv.letter = LUA_LVGL_FONT_FS_LETTER;
    s_rehab_font_fs_drv.ready_cb = lua_lvgl_font_fs_ready;
    s_rehab_font_fs_drv.open_cb = lua_lvgl_font_fs_open;
    s_rehab_font_fs_drv.close_cb = lua_lvgl_font_fs_close;
    s_rehab_font_fs_drv.read_cb = lua_lvgl_font_fs_read;
    s_rehab_font_fs_drv.seek_cb = lua_lvgl_font_fs_seek;
    s_rehab_font_fs_drv.tell_cb = lua_lvgl_font_fs_tell;
    lv_fs_drv_register(&s_rehab_font_fs_drv);
    s_rehab_font_fs_registered = true;
}

static void lua_lvgl_push_font_status(lua_State *L, bool ok, const char *message)
{
    lua_newtable(L);
    lua_pushboolean(L, ok);
    lua_setfield(L, -2, "ok");
    lua_pushboolean(L, s_lvgl.text_font != NULL);
    lua_setfield(L, -2, "loaded");
    lua_pushstring(L, s_lvgl.text_font_path[0] ? s_lvgl.text_font_path : "");
    lua_setfield(L, -2, "path");
    lua_pushinteger(L, (lua_Integer)s_lvgl.text_font_buffer_size);
    lua_setfield(L, -2, "size");
    lua_pushinteger(L, (lua_Integer)s_lvgl.text_font_glyphs);
    lua_setfield(L, -2, "glyphs");
    lua_pushstring(L, message ? message : s_lvgl.text_font_error);
    lua_setfield(L, -2, "message");
}

lv_font_t *lua_lvgl_get_named_font_locked(const char *name)
{
    if (!name || name[0] == '\0' || strcmp(name, "rehab") == 0 ||
            strcmp(name, "default") == 0 || strcmp(name, "title") == 0) {
        return s_lvgl.text_font;
    }
    return NULL;
}

void lua_lvgl_apply_default_text_font_locked(lv_obj_t *obj)
{
    if (obj && s_lvgl.text_font) {
        lv_obj_set_style_text_font(obj, s_lvgl.text_font, 0);
    }
}

void lua_lvgl_release_fonts_locked(void)
{
    if (s_lvgl.text_font) {
        lv_binfont_destroy(s_lvgl.text_font);
        s_lvgl.text_font = NULL;
    }
    s_lvgl.text_font_buffer_size = 0;
    s_lvgl.text_font_glyphs = 0;
    s_lvgl.text_font_path[0] = '\0';
    s_lvgl.text_font_error[0] = '\0';
}

static int lua_lvgl_load_font(lua_State *L)
{
    const char *path = luaL_checkstring(L, 1);
    uint32_t glyphs = (uint32_t)luaL_optinteger(L, 2, 0);
    esp_err_t err = lua_lvgl_lock();
    FILE *file = NULL;
    lv_font_t *font = NULL;
    long file_size = 0;
    char *lvgl_path = NULL;

    if (err != ESP_OK) {
        return lua_lvgl_error_esp(L, "lock", err);
    }
    if (!s_lvgl.runtime_initialized) {
        snprintf(s_lvgl.text_font_error, sizeof(s_lvgl.text_font_error), "runtime not initialized");
        lua_lvgl_push_font_status(L, false, s_lvgl.text_font_error);
        lua_lvgl_unlock();
        return 1;
    }
    if (s_lvgl.text_font && strcmp(s_lvgl.text_font_path, path) == 0) {
        lua_lvgl_push_font_status(L, true, "reused");
        lua_lvgl_unlock();
        return 1;
    }

    lua_lvgl_register_font_fs();

    file = fopen(path, "rb");
    if (!file) {
        snprintf(s_lvgl.text_font_error, sizeof(s_lvgl.text_font_error), "open failed errno=%d", errno);
        lua_lvgl_push_font_status(L, false, s_lvgl.text_font_error);
        lua_lvgl_unlock();
        return 1;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        snprintf(s_lvgl.text_font_error, sizeof(s_lvgl.text_font_error), "seek end failed");
        fclose(file);
        lua_lvgl_push_font_status(L, false, s_lvgl.text_font_error);
        lua_lvgl_unlock();
        return 1;
    }
    file_size = ftell(file);
    if (file_size <= 0 || file_size > LUA_LVGL_FONT_MAX_FILE_BYTES) {
        snprintf(s_lvgl.text_font_error, sizeof(s_lvgl.text_font_error), "invalid size %ld", file_size);
        fclose(file);
        lua_lvgl_push_font_status(L, false, s_lvgl.text_font_error);
        lua_lvgl_unlock();
        return 1;
    }
    rewind(file);

    fclose(file);

    lvgl_path = heap_caps_malloc(strlen(path) + 3, MALLOC_CAP_8BIT);
    if (!lvgl_path) {
        snprintf(s_lvgl.text_font_error, sizeof(s_lvgl.text_font_error), "path allocation failed");
        lua_lvgl_push_font_status(L, false, s_lvgl.text_font_error);
        lua_lvgl_unlock();
        return 1;
    }
    sprintf(lvgl_path, "%c:%s", LUA_LVGL_FONT_FS_LETTER, path);
    font = lv_binfont_create(lvgl_path);
    heap_caps_free(lvgl_path);
    if (!font) {
        snprintf(s_lvgl.text_font_error, sizeof(s_lvgl.text_font_error), "binfont create failed");
        lua_lvgl_push_font_status(L, false, s_lvgl.text_font_error);
        lua_lvgl_unlock();
        return 1;
    }

    lua_lvgl_release_fonts_locked();
    s_lvgl.text_font = font;
    s_lvgl.text_font_buffer_size = (size_t)file_size;
    s_lvgl.text_font_glyphs = glyphs;
    snprintf(s_lvgl.text_font_path, sizeof(s_lvgl.text_font_path), "%s", path);
    s_lvgl.text_font_error[0] = '\0';

    ESP_LOGI(TAG, "P4_REHAB_UI_FONT_LOADED path=%s size=%ld glyphs=%" PRIu32,
             path, file_size, glyphs);
    lua_lvgl_push_font_status(L, true, "loaded");
    lua_lvgl_unlock();
    return 1;
}

static int lua_lvgl_font_status(lua_State *L)
{
    esp_err_t err = lua_lvgl_lock();

    if (err != ESP_OK) {
        return lua_lvgl_error_esp(L, "lock", err);
    }
    lua_lvgl_push_font_status(L, s_lvgl.text_font != NULL, NULL);
    lua_lvgl_unlock();
    return 1;
}

const luaL_Reg lua_lvgl_font_module_funcs[] = {
    {"load_font", lua_lvgl_load_font},
    {"font_status", lua_lvgl_font_status},
    {NULL, NULL},
};
