/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include "rehab_sensor_internal.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "rehab_business_audio.h"

static const char *TAG = "rehab_recorder";
static rehab_training_finalized_cb_t s_finalized_callback;
static void *s_finalized_user_ctx;

#define REHAB_FUSED_FLAG_EMG_VALID     (1u << 0)
#define REHAB_FUSED_FLAG_IMU1_VALID    (1u << 1)
#define REHAB_FUSED_FLAG_IMU2_VALID    (1u << 2)
#define REHAB_FUSED_FLAG_IMU3_VALID    (1u << 3)
#define REHAB_FUSED_FLAG_IMU4_VALID    (1u << 4)
#define REHAB_FUSED_FLAG_NODE1_ONLINE  (1u << 8)
#define REHAB_FUSED_FLAG_NODE2_ONLINE  (1u << 9)
#define REHAB_SENSOR_TIME_VALID_EPOCH   INT64_C(1704067200)
#define REHAB_SENSOR_TASK_MAX_BYTES     (16U * 1024U)

const char REHAB_FUSED_CSV_HEADER[] =
    "log_seq,time_ms,output_rate_hz,flags,"
    "node1_rx,node1_tx_seq,node1_tx_gap,node1_age_ms,"
    "node2_rx,node2_tx_seq,node2_tx_gap,node2_age_ms,"
    "emg_valid,emg_latest_1,emg_latest_2,emg_latest_3,emg_latest_4,"
    "emg_rms_1,emg_rms_2,emg_rms_3,emg_rms_4,"
    "emg_mav_1,emg_mav_2,emg_mav_3,emg_mav_4,"
    "emg_peak_1,emg_peak_2,emg_peak_3,emg_peak_4,emg_n,"
    "imu1_valid,imu1_age_ms,imu1_roll,imu1_pitch,imu1_yaw,imu1_gx,imu1_gy,imu1_gz,imu1_ax,imu1_ay,imu1_az,imu1_lax,imu1_lay,imu1_laz,"
    "imu2_valid,imu2_age_ms,imu2_roll,imu2_pitch,imu2_yaw,imu2_gx,imu2_gy,imu2_gz,imu2_ax,imu2_ay,imu2_az,imu2_lax,imu2_lay,imu2_laz,"
    "imu3_valid,imu3_age_ms,imu3_roll,imu3_pitch,imu3_yaw,imu3_gx,imu3_gy,imu3_gz,imu3_ax,imu3_ay,imu3_az,imu3_lax,imu3_lay,imu3_laz,"
    "imu4_valid,imu4_age_ms,imu4_roll,imu4_pitch,imu4_yaw,imu4_gx,imu4_gy,imu4_gz,imu4_ax,imu4_ay,imu4_az,imu4_lax,imu4_lay,imu4_laz";

typedef struct {
    char session_path[REHAB_TRAINING_SESSION_PATH_LEN];
    char csv_path[REHAB_TRAINING_FILE_PATH_LEN];
    char task_json_path[REHAB_TRAINING_FILE_PATH_LEN];
    char csv_name[48];
    char start_time[40];
    uint64_t start_log_seq;
    FILE *csv_file;
    char *stdio_buffer;
    int64_t start_epoch;
    bool clock_fallback;
    bool session_created;
    bool csv_created;
    bool task_json_created;
} rehab_start_context_t;

typedef struct {
    rehab_writer_node_t nodes[2];
    rehab_sensor_node_status_t node_status[2];
    uint64_t session_gap[2];
    rehab_fused_row_t row;
    rehab_record_item_t item;
    char line[REHAB_SENSOR_CSV_LINE_BUFFER_SIZE];
    int64_t next_tick_us;
    int64_t last_row_us;
    int64_t last_flush_us;
    int64_t last_fsync_us;
    uint32_t generation;
    char final_task_name[REHAB_TRAINING_TASK_NAME_MAX_BYTES + 1];
    char final_session_path[REHAB_TRAINING_SESSION_PATH_LEN];
    char final_csv_name[48];
    char final_task_json_path[REHAB_TRAINING_FILE_PATH_LEN];
    char final_start_time[40];
    char final_end_reason[24];
    char final_end_time[40];
    int64_t final_start_epoch;
    int64_t final_start_us;
    int64_t final_stop_us;
    bool final_clock_fallback;
} rehab_writer_context_t;

typedef struct {
    char latest_id[17];
    char session_path[REHAB_TRAINING_SESSION_PATH_LEN];
    char task_path[REHAB_TRAINING_FILE_PATH_LEN];
} rehab_anchor_scan_context_t;

static bool rehab_sensor_utf8_continuation(uint8_t byte)
{
    return (byte & 0xc0u) == 0x80u;
}

bool rehab_sensor_valid_task_name(const char *task_name)
{
    const uint8_t *cursor = (const uint8_t *)task_name;
    size_t length;

    if (!task_name) {
        return false;
    }
    length = strlen(task_name);
    if (length == 0 || length > REHAB_TRAINING_TASK_NAME_MAX_BYTES) {
        return false;
    }

    for (size_t index = 0; index < length;) {
        uint8_t first = cursor[index];
        if (first < 0x20u || first == 0x7fu) {
            return false;
        }
        if (first < 0x80u) {
            index++;
        } else if (first >= 0xc2u && first <= 0xdfu) {
            if (index + 1 >= length || !rehab_sensor_utf8_continuation(cursor[index + 1])) {
                return false;
            }
            index += 2;
        } else if (first >= 0xe0u && first <= 0xefu) {
            if (index + 2 >= length ||
                    !rehab_sensor_utf8_continuation(cursor[index + 1]) ||
                    !rehab_sensor_utf8_continuation(cursor[index + 2]) ||
                    (first == 0xe0u && cursor[index + 1] < 0xa0u) ||
                    (first == 0xedu && cursor[index + 1] >= 0xa0u)) {
                return false;
            }
            index += 3;
        } else if (first >= 0xf0u && first <= 0xf4u) {
            if (index + 3 >= length ||
                    !rehab_sensor_utf8_continuation(cursor[index + 1]) ||
                    !rehab_sensor_utf8_continuation(cursor[index + 2]) ||
                    !rehab_sensor_utf8_continuation(cursor[index + 3]) ||
                    (first == 0xf0u && cursor[index + 1] < 0x90u) ||
                    (first == 0xf4u && cursor[index + 1] >= 0x90u)) {
                return false;
            }
            index += 4;
        } else {
            return false;
        }
    }
    return true;
}

bool rehab_sensor_valid_end_reason(const char *reason)
{
    return reason &&
           (strcmp(reason, "user_stop") == 0 ||
            strcmp(reason, "completed") == 0 ||
            strcmp(reason, "write_error") == 0 ||
            strcmp(reason, "sensor_timeout") == 0);
}

static bool rehab_sensor_wall_clock(char date[9], char iso_time[40])
{
    time_t now = time(NULL);
    struct tm local = {0};
    char base[24] = {0};
    char zone[8] = {0};

    if (now <= 0 || !localtime_r(&now, &local) || local.tm_year + 1900 < 2024) {
        return false;
    }
    if (strftime(date, 9, "%Y%m%d", &local) != 8 ||
            strftime(base, sizeof(base), "%Y-%m-%dT%H:%M:%S", &local) == 0) {
        return false;
    }
    if (strftime(zone, sizeof(zone), "%z", &local) == 5) {
        int written = snprintf(iso_time,
                               40,
                               "%s%c%c%c:%c%c",
                               base,
                               zone[0], zone[1], zone[2], zone[3], zone[4]);
        return written > 0 && written < 40;
    }
    return snprintf(iso_time, 40, "%sZ", base) > 0;
}

static bool rehab_sensor_format_epoch_local(int64_t epoch,
                                            char date[9],
                                            char iso_time[40])
{
    time_t raw = (time_t)epoch;
    struct tm local = {0};
    char base[24] = {0};
    char zone[8] = {0};

    if ((int64_t)raw != epoch || epoch < REHAB_SENSOR_TIME_VALID_EPOCH ||
            !localtime_r(&raw, &local) || local.tm_year + 1900 < 2024) {
        return false;
    }
    if (strftime(date, 9, "%Y%m%d", &local) != 8 ||
            strftime(base, sizeof(base), "%Y-%m-%dT%H:%M:%S", &local) == 0) {
        return false;
    }
    if (strftime(zone, sizeof(zone), "%z", &local) == 5) {
        int written = snprintf(iso_time,
                               40,
                               "%s%c%c%c:%c%c",
                               base,
                               zone[0], zone[1], zone[2], zone[3], zone[4]);
        return written > 0 && written < 40;
    }
    return snprintf(iso_time, 40, "%sZ", base) > 0;
}

static bool rehab_sensor_parse_decimal(const char *value, size_t count, int *out)
{
    int parsed = 0;

    if (!value || !out || count == 0) {
        return false;
    }
    for (size_t index = 0; index < count; ++index) {
        if (!isdigit((unsigned char)value[index])) {
            return false;
        }
        parsed = parsed * 10 + (value[index] - '0');
    }
    *out = parsed;
    return true;
}

static bool rehab_sensor_leap_year(int year)
{
    return year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
}

static int rehab_sensor_days_in_month(int year, int month)
{
    static const uint8_t days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};

    if (month < 1 || month > 12) {
        return 0;
    }
    return month == 2 && rehab_sensor_leap_year(year) ? 29 : days[month - 1];
}

static int64_t rehab_sensor_days_from_civil(int year, unsigned month, unsigned day)
{
    year -= month <= 2;
    int era = (year >= 0 ? year : year - 399) / 400;
    unsigned year_of_era = (unsigned)(year - era * 400);
    int adjusted_month = (int)month + (month > 2 ? -3 : 9);
    unsigned day_of_year = (153U * (unsigned)adjusted_month + 2U) / 5U + day - 1U;
    unsigned day_of_era = year_of_era * 365U + year_of_era / 4U -
                           year_of_era / 100U + day_of_year;
    return (int64_t)era * 146097 + (int64_t)day_of_era - 719468;
}

static bool rehab_sensor_parse_iso8601_epoch(const char *value, int64_t *out_epoch)
{
    int year;
    int month;
    int day;
    int hour;
    int minute;
    int second;
    int zone_hour = 0;
    int zone_minute = 0;
    int zone_sign = 0;
    size_t length;

    if (!value || !out_epoch) {
        return false;
    }
    length = strlen(value);
    if ((length != 20 && length != 25) ||
            value[4] != '-' || value[7] != '-' || value[10] != 'T' ||
            value[13] != ':' || value[16] != ':' ||
            !rehab_sensor_parse_decimal(value, 4, &year) ||
            !rehab_sensor_parse_decimal(value + 5, 2, &month) ||
            !rehab_sensor_parse_decimal(value + 8, 2, &day) ||
            !rehab_sensor_parse_decimal(value + 11, 2, &hour) ||
            !rehab_sensor_parse_decimal(value + 14, 2, &minute) ||
            !rehab_sensor_parse_decimal(value + 17, 2, &second)) {
        return false;
    }
    if (year < 1970 || month < 1 || month > 12 || day < 1 ||
            day > rehab_sensor_days_in_month(year, month) ||
            hour > 23 || minute > 59 || second > 59) {
        return false;
    }
    if (length == 20) {
        if (value[19] != 'Z') {
            return false;
        }
    } else {
        if ((value[19] != '+' && value[19] != '-') || value[22] != ':' ||
                !rehab_sensor_parse_decimal(value + 20, 2, &zone_hour) ||
                !rehab_sensor_parse_decimal(value + 23, 2, &zone_minute) ||
                zone_hour > 23 || zone_minute > 59) {
            return false;
        }
        zone_sign = value[19] == '+' ? 1 : -1;
    }

    int64_t epoch = rehab_sensor_days_from_civil(year, (unsigned)month, (unsigned)day) *
                    INT64_C(86400);
    epoch += hour * 3600 + minute * 60 + second;
    epoch -= zone_sign * (zone_hour * 3600 + zone_minute * 60);
    *out_epoch = epoch;
    return true;
}

static bool rehab_sensor_session_id_valid(const char *value)
{
    int sequence = 0;

    if (!value || strlen(value) != 16 || memcmp(value + 8, "_TASK", 5) != 0) {
        return false;
    }
    for (size_t index = 0; index < 8; ++index) {
        if (!isdigit((unsigned char)value[index])) {
            return false;
        }
    }
    if (!rehab_sensor_parse_decimal(value + 13, 3, &sequence)) {
        return false;
    }
    return sequence >= 1 && sequence <= 999;
}

static bool rehab_sensor_exact_task_fields(const cJSON *root)
{
    static const char *const fields[] = {
        "task_name", "csv_file", "start_time", "end_time",
        "start_log_seq", "end_log_seq", "end_reason",
    };
    bool seen[sizeof(fields) / sizeof(fields[0])] = {0};

    if (!cJSON_IsObject(root) || cJSON_GetArraySize(root) !=
            (int)(sizeof(fields) / sizeof(fields[0]))) {
        return false;
    }
    for (const cJSON *child = root->child; child; child = child->next) {
        bool matched = false;
        for (size_t index = 0; index < sizeof(fields) / sizeof(fields[0]); ++index) {
            if (child->string && strcmp(child->string, fields[index]) == 0 && !seen[index]) {
                seen[index] = true;
                matched = true;
                break;
            }
        }
        if (!matched) {
            return false;
        }
    }
    return true;
}

static bool rehab_sensor_json_uint64(const cJSON *item, uint64_t *out)
{
    if (!cJSON_IsNumber(item) || item->valuedouble < 0 ||
            item->valuedouble > 9007199254740991.0) {
        return false;
    }
    uint64_t value = (uint64_t)item->valuedouble;
    if ((double)value != item->valuedouble) {
        return false;
    }
    if (out) {
        *out = value;
    }
    return true;
}

static bool rehab_sensor_csv_name_valid(const char *value)
{
    if (!value || strlen(value) != 22 || memcmp(value, "DATA_SEQ", 8) != 0 ||
            strcmp(value + 18, ".csv") != 0) {
        return false;
    }
    for (size_t index = 8; index < 18; ++index) {
        if (!isdigit((unsigned char)value[index])) {
            return false;
        }
    }
    return true;
}

static char *rehab_sensor_read_bounded_task(const char *path)
{
    struct stat info = {0};
    FILE *file;
    char *bytes;

    if (!path || stat(path, &info) != 0 || !S_ISREG(info.st_mode) ||
            info.st_size < 1 || (uint64_t)info.st_size > REHAB_SENSOR_TASK_MAX_BYTES) {
        return NULL;
    }
    bytes = malloc((size_t)info.st_size + 1U);
    if (!bytes) {
        return NULL;
    }
    file = fopen(path, "rb");
    if (!file) {
        free(bytes);
        return NULL;
    }
    size_t received = fread(bytes, 1, (size_t)info.st_size, file);
    bool valid = received == (size_t)info.st_size && !ferror(file);
    if (fclose(file) != 0) {
        valid = false;
    }
    if (!valid) {
        free(bytes);
        return NULL;
    }
    bytes[received] = '\0';
    return bytes;
}

static bool rehab_sensor_parse_finalized_anchor(const char *session_id,
                                                const char *json,
                                                int64_t *out_end_epoch)
{
    cJSON *root;
    const cJSON *task_name;
    const cJSON *csv_file;
    const cJSON *start_time;
    const cJSON *end_time;
    const cJSON *start_seq;
    const cJSON *end_seq;
    const cJSON *end_reason;
    int64_t start_epoch = 0;
    int64_t end_epoch = 0;
    uint64_t start = 0;
    uint64_t end = 0;
    char start_date[9];

    if (!rehab_sensor_session_id_valid(session_id) || !json || !out_end_epoch) {
        return false;
    }
    root = cJSON_ParseWithOpts(json, NULL, true);
    if (!rehab_sensor_exact_task_fields(root)) {
        cJSON_Delete(root);
        return false;
    }
    task_name = cJSON_GetObjectItemCaseSensitive(root, "task_name");
    csv_file = cJSON_GetObjectItemCaseSensitive(root, "csv_file");
    start_time = cJSON_GetObjectItemCaseSensitive(root, "start_time");
    end_time = cJSON_GetObjectItemCaseSensitive(root, "end_time");
    start_seq = cJSON_GetObjectItemCaseSensitive(root, "start_log_seq");
    end_seq = cJSON_GetObjectItemCaseSensitive(root, "end_log_seq");
    end_reason = cJSON_GetObjectItemCaseSensitive(root, "end_reason");
    bool valid = cJSON_IsString(task_name) &&
                 rehab_sensor_valid_task_name(task_name->valuestring) &&
                 cJSON_IsString(csv_file) && rehab_sensor_csv_name_valid(csv_file->valuestring) &&
                 cJSON_IsString(start_time) && start_time->valuestring &&
                 cJSON_IsString(end_time) && end_time->valuestring && end_time->valuestring[0] &&
                 cJSON_IsString(end_reason) && end_reason->valuestring &&
                 (strcmp(end_reason->valuestring, "user_stop") == 0 ||
                  strcmp(end_reason->valuestring, "completed") == 0) &&
                 rehab_sensor_json_uint64(start_seq, &start) &&
                 rehab_sensor_json_uint64(end_seq, &end) && end >= start &&
                 rehab_sensor_parse_iso8601_epoch(start_time->valuestring, &start_epoch) &&
                 rehab_sensor_parse_iso8601_epoch(end_time->valuestring, &end_epoch) &&
                 start_epoch >= REHAB_SENSOR_TIME_VALID_EPOCH && end_epoch >= start_epoch;
    if (valid) {
        int written = snprintf(start_date,
                               sizeof(start_date),
                               "%.4s%.2s%.2s",
                               start_time->valuestring,
                               start_time->valuestring + 5,
                               start_time->valuestring + 8);
        valid = written == 8 && memcmp(session_id, start_date, 8) == 0;
    }
    cJSON_Delete(root);
    if (valid) {
        *out_end_epoch = end_epoch;
    }
    return valid;
}

static bool rehab_sensor_find_latest_finalized_end(int64_t *out_end_epoch)
{
    DIR *directory;
    struct dirent *entry;
    rehab_anchor_scan_context_t *context;
    int64_t latest_end = 0;

    if (!out_end_epoch) {
        return false;
    }
    context = calloc(1, sizeof(*context));
    if (!context) {
        return false;
    }
    directory = opendir(REHAB_SENSOR_SESSION_ROOT);
    if (!directory) {
        free(context);
        return false;
    }
    while ((entry = readdir(directory)) != NULL) {
        struct stat info = {0};
        int64_t candidate_end = 0;

        if (!rehab_sensor_session_id_valid(entry->d_name)) {
            continue;
        }
        int session_written = snprintf(context->session_path,
                                       sizeof(context->session_path),
                                       "%s/%s",
                                       REHAB_SENSOR_SESSION_ROOT,
                                       entry->d_name);
        int task_written = snprintf(context->task_path,
                                    sizeof(context->task_path),
                                    "%s/task.json",
                                    context->session_path);
        if (session_written < 0 ||
                (size_t)session_written >= sizeof(context->session_path) ||
                task_written < 0 || (size_t)task_written >= sizeof(context->task_path) ||
                stat(context->session_path, &info) != 0 || !S_ISDIR(info.st_mode)) {
            continue;
        }
        char *json = rehab_sensor_read_bounded_task(context->task_path);
        bool valid = json && rehab_sensor_parse_finalized_anchor(entry->d_name,
                                                                  json,
                                                                  &candidate_end);
        free(json);
        if (valid && (!context->latest_id[0] ||
                strcmp(entry->d_name, context->latest_id) > 0)) {
            strlcpy(context->latest_id, entry->d_name, sizeof(context->latest_id));
            latest_end = candidate_end;
        }
    }
    closedir(directory);
    bool found = context->latest_id[0] != '\0';
    free(context);
    if (!found) {
        return false;
    }
    *out_end_epoch = latest_end;
    return true;
}

static bool rehab_sensor_parse_build_epoch(int64_t *out_epoch)
{
    static const char *const months[] = {
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    };
    const esp_app_desc_t *description = esp_app_get_description();
    char month_name[4] = {0};
    int day;
    int year;
    int hour;
    int minute;
    int second;
    int date_consumed = 0;
    int time_consumed = 0;
    int month = 0;

    if (!description || !out_epoch ||
            sscanf(description->date, "%3s %d %d %n", month_name, &day, &year,
                   &date_consumed) != 3 || description->date[date_consumed] != '\0' ||
            sscanf(description->time, "%d:%d:%d%n", &hour, &minute, &second,
                   &time_consumed) != 3 || description->time[time_consumed] != '\0') {
        return false;
    }
    for (size_t index = 0; index < sizeof(months) / sizeof(months[0]); ++index) {
        if (strcmp(month_name, months[index]) == 0) {
            month = (int)index + 1;
            break;
        }
    }
    if (month == 0 || year < 2024 || day < 1 ||
            day > rehab_sensor_days_in_month(year, month) ||
            hour < 0 || hour > 23 || minute < 0 || minute > 59 ||
            second < 0 || second > 59) {
        return false;
    }
    struct tm local = {
        .tm_sec = second,
        .tm_min = minute,
        .tm_hour = hour,
        .tm_mday = day,
        .tm_mon = month - 1,
        .tm_year = year - 1900,
        .tm_isdst = -1,
    };
    time_t epoch = mktime(&local);
    if (epoch <= 0 || (int64_t)epoch < REHAB_SENSOR_TIME_VALID_EPOCH) {
        return false;
    }
    *out_epoch = (int64_t)epoch;
    return true;
}

static bool rehab_sensor_resolve_session_clock(char date[9],
                                               char iso_time[40],
                                               bool *out_fallback,
                                               int64_t *out_start_epoch)
{
    int64_t anchor_end = 0;
    int64_t fallback_epoch = 0;
    int64_t uptime_us = esp_timer_get_time();
    int64_t uptime_seconds = uptime_us > 0 ? uptime_us / INT64_C(1000000) : 0;

    if (!date || !iso_time || !out_fallback || !out_start_epoch) {
        return false;
    }
    if (rehab_sensor_wall_clock(date, iso_time)) {
        *out_fallback = false;
        *out_start_epoch = 0;
        return true;
    }

    if (rehab_sensor_lock() == ESP_OK) {
        anchor_end = g_rehab_sensor.latest_finalized_end_epoch;
        rehab_sensor_unlock();
    }
    if (anchor_end <= 0 && rehab_sensor_find_latest_finalized_end(&anchor_end)) {
        if (rehab_sensor_lock() == ESP_OK) {
            g_rehab_sensor.latest_finalized_end_epoch = anchor_end;
            rehab_sensor_unlock();
        }
    }
    if (anchor_end > 0 && anchor_end < INT64_MAX) {
        fallback_epoch = anchor_end + 1;
    }
    if (fallback_epoch == 0 ||
            !rehab_sensor_format_epoch_local(fallback_epoch, date, iso_time)) {
        int64_t build_epoch = 0;
        if (rehab_sensor_parse_build_epoch(&build_epoch) &&
                build_epoch <= INT64_MAX - uptime_seconds) {
            fallback_epoch = build_epoch + uptime_seconds;
        } else {
            fallback_epoch = REHAB_SENSOR_TIME_VALID_EPOCH + uptime_seconds;
        }
        if (!rehab_sensor_format_epoch_local(fallback_epoch, date, iso_time)) {
            return false;
        }
    }
    *out_fallback = true;
    *out_start_epoch = fallback_epoch;
    return true;
}

static bool rehab_sensor_fallback_end_time(int64_t start_epoch,
                                           int64_t start_us,
                                           int64_t stop_us,
                                           char date[9],
                                           char iso_time[40],
                                           int64_t *out_end_epoch)
{
    int64_t elapsed_us = stop_us > start_us ? stop_us - start_us : 0;
    int64_t elapsed_seconds = (elapsed_us + INT64_C(999999)) / INT64_C(1000000);

    if (elapsed_seconds < 1) {
        elapsed_seconds = 1;
    }
    if (start_epoch < REHAB_SENSOR_TIME_VALID_EPOCH ||
            start_epoch > INT64_MAX - elapsed_seconds) {
        return false;
    }
    int64_t end_epoch = start_epoch + elapsed_seconds;
    if (!rehab_sensor_format_epoch_local(end_epoch, date, iso_time)) {
        return false;
    }
    if (out_end_epoch) {
        *out_end_epoch = end_epoch;
    }
    return true;
}

static esp_err_t rehab_sensor_ensure_session_root(void)
{
    struct stat info = {0};

    if (stat(REHAB_SENSOR_SESSION_ROOT, &info) == 0) {
        return S_ISDIR(info.st_mode) ? ESP_OK : ESP_ERR_INVALID_STATE;
    }
    if (errno != ENOENT) {
        return ESP_FAIL;
    }
    if (mkdir(REHAB_SENSOR_SESSION_ROOT, 0755) != 0) {
        return ESP_FAIL;
    }
    return ESP_OK;
}

static esp_err_t rehab_sensor_create_session_directory(const char date[9],
                                                       char *path,
                                                       size_t path_size)
{
    struct stat info = {0};

    for (unsigned sequence = 1; sequence <= 999; ++sequence) {
        int written = snprintf(path,
                               path_size,
                               "%s/%s_TASK%03u",
                               REHAB_SENSOR_SESSION_ROOT,
                               date,
                               sequence);
        if (written < 0 || (size_t)written >= path_size) {
            return ESP_ERR_INVALID_SIZE;
        }
        if (stat(path, &info) == 0) {
            continue;
        }
        if (errno != ENOENT) {
            return ESP_FAIL;
        }
        if (mkdir(path, 0755) == 0) {
            return ESP_OK;
        }
        if (errno != EEXIST) {
            return ESP_FAIL;
        }
    }
    return ESP_ERR_NOT_FOUND;
}

char *rehab_sensor_render_task_json(const char *task_name,
                                    const char *csv_name,
                                    const char *start_time,
                                    const char *end_time,
                                    uint64_t start_log_seq,
                                    uint64_t end_log_seq,
                                    const char *end_reason)
{
    cJSON *root = cJSON_CreateObject();
    char *json;

    if (!root ||
            !cJSON_AddStringToObject(root, "task_name", task_name) ||
            !cJSON_AddStringToObject(root, "csv_file", csv_name) ||
            !cJSON_AddStringToObject(root, "start_time", start_time) ||
            !cJSON_AddStringToObject(root, "end_time", end_time) ||
            !cJSON_AddNumberToObject(root, "start_log_seq", (double)start_log_seq) ||
            !cJSON_AddNumberToObject(root, "end_log_seq", (double)end_log_seq) ||
            !cJSON_AddStringToObject(root, "end_reason", end_reason)) {
        cJSON_Delete(root);
        return NULL;
    }

    json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return json;
}

static esp_err_t rehab_sensor_write_task_json_file(const char *path,
                                                   const char *task_name,
                                                   const char *csv_name,
                                                   const char *start_time,
                                                   const char *end_time,
                                                   uint64_t start_log_seq,
                                                   uint64_t end_log_seq,
                                                   const char *end_reason)
{
    char *json = rehab_sensor_render_task_json(task_name,
                                               csv_name,
                                               start_time,
                                               end_time,
                                               start_log_seq,
                                               end_log_seq,
                                               end_reason);
    FILE *file = NULL;
    esp_err_t result = ESP_FAIL;

    if (!json) {
        return ESP_ERR_NO_MEM;
    }

    file = fopen(path, "wb");
    if (!file) {
        cJSON_free(json);
        return ESP_FAIL;
    }
    size_t length = strlen(json);
    if (fwrite(json, 1, length, file) == length &&
            fwrite("\n", 1, 1, file) == 1 &&
            fflush(file) == 0 && !ferror(file) &&
            fsync(fileno(file)) == 0) {
        result = ESP_OK;
    }
    if (fclose(file) != 0) {
        result = ESP_FAIL;
    }
    cJSON_free(json);
    return result;
}

static esp_err_t rehab_sensor_replace_final_task_json(const char *task_json_path,
                                                      const char *task_name,
                                                      const char *csv_name,
                                                      const char *start_time,
                                                      const char *end_time,
                                                      uint64_t start_log_seq,
                                                      uint64_t end_log_seq,
                                                      const char *end_reason)
{
    char temporary[REHAB_TRAINING_FILE_PATH_LEN + 8];
    char backup[REHAB_TRAINING_FILE_PATH_LEN + 8];
    int written = snprintf(temporary, sizeof(temporary), "%s.tmp", task_json_path);

    if (written < 0 || (size_t)written >= sizeof(temporary)) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (rehab_sensor_write_task_json_file(temporary,
                                          task_name,
                                          csv_name,
                                          start_time,
                                          end_time,
                                          start_log_seq,
                                          end_log_seq,
                                          end_reason) != ESP_OK) {
        return ESP_FAIL;
    }
    if (rename(temporary, task_json_path) == 0) {
        return ESP_OK;
    }

    /* Some FAT VFS versions do not replace an existing destination. Preserve
     * the placeholder as a backup while completing a best-effort replacement. */
    written = snprintf(backup, sizeof(backup), "%s.bak", task_json_path);
    if (written < 0 || (size_t)written >= sizeof(backup) ||
            rename(task_json_path, backup) != 0) {
        return ESP_FAIL;
    }
    if (rename(temporary, task_json_path) != 0) {
        (void)rename(backup, task_json_path);
        return ESP_FAIL;
    }
    (void)unlink(backup);
    return ESP_OK;
}

static bool rehab_sensor_copy_imu(rehab_fused_imu_t *destination,
                                  const sensor_imu_state_t *source,
                                  uint32_t node_age,
                                  bool valid)
{
    memset(destination, 0, sizeof(*destination));
    if (!valid ||
            !isfinite(source->angle_x_deg) || !isfinite(source->angle_y_deg) ||
            !isfinite(source->angle_z_deg) || !isfinite(source->gyro_x_dps) ||
            !isfinite(source->gyro_y_dps) || !isfinite(source->gyro_z_dps) ||
            !isfinite(source->acc_x_g) || !isfinite(source->acc_y_g) ||
            !isfinite(source->acc_z_g) || !isfinite(source->lin_acc_x_g) ||
            !isfinite(source->lin_acc_y_g) || !isfinite(source->lin_acc_z_g)) {
        return !valid;
    }

    destination->valid = true;
    destination->age_ms = rehab_sensor_saturating_add_u32(source->age_ms, node_age);
    destination->roll_deg = source->angle_x_deg;
    destination->pitch_deg = source->angle_y_deg;
    destination->yaw_deg = source->angle_z_deg;
    destination->gyro_x_dps = source->gyro_x_dps;
    destination->gyro_y_dps = source->gyro_y_dps;
    destination->gyro_z_dps = source->gyro_z_dps;
    destination->acc_x_g = source->acc_x_g;
    destination->acc_y_g = source->acc_y_g;
    destination->acc_z_g = source->acc_z_g;
    destination->lin_acc_x_g = source->lin_acc_x_g;
    destination->lin_acc_y_g = source->lin_acc_y_g;
    destination->lin_acc_z_g = source->lin_acc_z_g;
    return true;
}

static bool rehab_sensor_copy_emg(rehab_fused_emg_t *destination,
                                  const sensor_emg_state_t *source,
                                  bool valid)
{
    memset(destination, 0, sizeof(*destination));
    if (!valid ||
            !isfinite(source->rms_ch1) || !isfinite(source->rms_ch2) ||
            !isfinite(source->rms_ch3) || !isfinite(source->rms_ch4) ||
            !isfinite(source->mav_ch1) || !isfinite(source->mav_ch2) ||
            !isfinite(source->mav_ch3) || !isfinite(source->mav_ch4)) {
        return !valid;
    }
    destination->valid = true;
    destination->sample_count = source->sample_count;
    destination->latest[0] = source->latest_ch1;
    destination->latest[1] = source->latest_ch2;
    destination->latest[2] = source->latest_ch3;
    destination->latest[3] = source->latest_ch4;
    destination->rms[0] = source->rms_ch1;
    destination->rms[1] = source->rms_ch2;
    destination->rms[2] = source->rms_ch3;
    destination->rms[3] = source->rms_ch4;
    destination->mav[0] = source->mav_ch1;
    destination->mav[1] = source->mav_ch2;
    destination->mav[2] = source->mav_ch3;
    destination->mav[3] = source->mav_ch4;
    destination->peak[0] = source->peak_abs_ch1;
    destination->peak[1] = source->peak_abs_ch2;
    destination->peak[2] = source->peak_abs_ch3;
    destination->peak[3] = source->peak_abs_ch4;
    return true;
}

void rehab_sensor_build_fused_row(const rehab_writer_node_t writer_nodes[2],
                                  const rehab_sensor_node_status_t node_status[2],
                                  const uint64_t session_gap[2],
                                  int64_t now_us,
                                  uint64_t log_seq,
                                  rehab_fused_row_t *row)
{
    bool online[2];

    memset(row, 0, sizeof(*row));
    row->log_seq = log_seq;
    row->time_ms = now_us > 0 ? (uint64_t)now_us / 1000u : 0;
    row->output_rate_hz = REHAB_SENSOR_OUTPUT_RATE_HZ;

    for (unsigned index = 0; index < 2; ++index) {
        row->node_rx[index] = node_status[index].rx_count;
        row->node_tx_seq[index] = writer_nodes[index].has_packet ?
                                  writer_nodes[index].packet.tx_seq :
                                  node_status[index].last_tx_seq;
        row->node_tx_gap[index] = session_gap[index];
        row->node_age_ms[index] = writer_nodes[index].has_packet &&
                                  writer_nodes[index].rx_us > 0 ?
                                  rehab_sensor_age_ms(now_us, writer_nodes[index].rx_us) :
                                  node_status[index].last_rx_age_ms;
        online[index] = writer_nodes[index].has_packet && node_status[index].online;
    }
    if (online[0]) {
        row->flags |= REHAB_FUSED_FLAG_NODE1_ONLINE;
    }
    if (online[1]) {
        row->flags |= REHAB_FUSED_FLAG_NODE2_ONLINE;
    }

    if (online[0]) {
        const sensor_packet_t *packet = &writer_nodes[0].packet;
        bool emg_valid = (packet->flags & SENSOR_FLAG_EMG_VALID) != 0 &&
                         packet->emg.sample_count > 0 &&
                         packet->emg.rms_ch1 >= 0.0f && packet->emg.rms_ch2 >= 0.0f &&
                         packet->emg.rms_ch3 >= 0.0f && packet->emg.rms_ch4 >= 0.0f &&
                         packet->emg.mav_ch1 >= 0.0f && packet->emg.mav_ch2 >= 0.0f &&
                         packet->emg.mav_ch3 >= 0.0f && packet->emg.mav_ch4 >= 0.0f &&
                         packet->emg.peak_abs_ch1 >= 0 && packet->emg.peak_abs_ch2 >= 0 &&
                         packet->emg.peak_abs_ch3 >= 0 && packet->emg.peak_abs_ch4 >= 0;
        if (!rehab_sensor_copy_emg(&row->emg, &packet->emg, emg_valid)) {
            row->sanitized_node_mask |= 1u << 0;
        }
        if (row->emg.valid) {
            row->flags |= REHAB_FUSED_FLAG_EMG_VALID;
        }
        if (!rehab_sensor_copy_imu(&row->imu[0],
                                   &packet->imu1,
                                   row->node_age_ms[0],
                                   (packet->flags & SENSOR_FLAG_IMU1_VALID) != 0 &&
                                   packet->imu1.age_ms <= REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS)) {
            row->sanitized_node_mask |= 1u << 0;
        }
        if (!rehab_sensor_copy_imu(&row->imu[1],
                                   &packet->imu2,
                                   row->node_age_ms[0],
                                   (packet->flags & SENSOR_FLAG_IMU2_VALID) != 0 &&
                                   packet->imu2.age_ms <= REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS)) {
            row->sanitized_node_mask |= 1u << 0;
        }
    }
    if (online[1]) {
        const sensor_packet_t *packet = &writer_nodes[1].packet;
        if (!rehab_sensor_copy_imu(&row->imu[2],
                                   &packet->imu1,
                                   row->node_age_ms[1],
                                   (packet->flags & SENSOR_FLAG_IMU1_VALID) != 0 &&
                                   packet->imu1.age_ms <= REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS)) {
            row->sanitized_node_mask |= 1u << 1;
        }
        if (!rehab_sensor_copy_imu(&row->imu[3],
                                   &packet->imu2,
                                   row->node_age_ms[1],
                                   (packet->flags & SENSOR_FLAG_IMU2_VALID) != 0 &&
                                   packet->imu2.age_ms <= REHAB_SENSOR_NODE_ONLINE_TIMEOUT_MS)) {
            row->sanitized_node_mask |= 1u << 1;
        }
    }

    if (row->imu[0].valid) row->flags |= REHAB_FUSED_FLAG_IMU1_VALID;
    if (row->imu[1].valid) row->flags |= REHAB_FUSED_FLAG_IMU2_VALID;
    if (row->imu[2].valid) row->flags |= REHAB_FUSED_FLAG_IMU3_VALID;
    if (row->imu[3].valid) row->flags |= REHAB_FUSED_FLAG_IMU4_VALID;
}

int rehab_sensor_format_csv_row(char *buffer,
                                size_t buffer_size,
                                const rehab_fused_row_t *row)
{
    if (!buffer || buffer_size == 0 || !row) {
        return -1;
    }
    return snprintf(
        buffer,
        buffer_size,
        "%" PRIu64 ",%" PRIu64 ",%u,0x%08" PRIx32 ","
        "%" PRIu64 ",%" PRIu32 ",%" PRIu64 ",%" PRIu32 ","
        "%" PRIu64 ",%" PRIu32 ",%" PRIu64 ",%" PRIu32 ","
        "%d,%" PRId32 ",%" PRId32 ",%" PRId32 ",%" PRId32 ","
        "%.2f,%.2f,%.2f,%.2f,"
        "%.2f,%.2f,%.2f,%.2f,"
        "%" PRId32 ",%" PRId32 ",%" PRId32 ",%" PRId32 ",%u,"
        "%d,%" PRIu32 ",%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,"
        "%d,%" PRIu32 ",%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,"
        "%d,%" PRIu32 ",%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,"
        "%d,%" PRIu32 ",%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f",
        row->log_seq, row->time_ms, (unsigned)row->output_rate_hz, row->flags,
        row->node_rx[0], row->node_tx_seq[0], row->node_tx_gap[0], row->node_age_ms[0],
        row->node_rx[1], row->node_tx_seq[1], row->node_tx_gap[1], row->node_age_ms[1],
        row->emg.valid ? 1 : 0,
        row->emg.latest[0], row->emg.latest[1], row->emg.latest[2], row->emg.latest[3],
        row->emg.rms[0], row->emg.rms[1], row->emg.rms[2], row->emg.rms[3],
        row->emg.mav[0], row->emg.mav[1], row->emg.mav[2], row->emg.mav[3],
        row->emg.peak[0], row->emg.peak[1], row->emg.peak[2], row->emg.peak[3],
        (unsigned)row->emg.sample_count,
        row->imu[0].valid ? 1 : 0, row->imu[0].age_ms,
        row->imu[0].roll_deg, row->imu[0].pitch_deg, row->imu[0].yaw_deg,
        row->imu[0].gyro_x_dps, row->imu[0].gyro_y_dps, row->imu[0].gyro_z_dps,
        row->imu[0].acc_x_g, row->imu[0].acc_y_g, row->imu[0].acc_z_g,
        row->imu[0].lin_acc_x_g, row->imu[0].lin_acc_y_g, row->imu[0].lin_acc_z_g,
        row->imu[1].valid ? 1 : 0, row->imu[1].age_ms,
        row->imu[1].roll_deg, row->imu[1].pitch_deg, row->imu[1].yaw_deg,
        row->imu[1].gyro_x_dps, row->imu[1].gyro_y_dps, row->imu[1].gyro_z_dps,
        row->imu[1].acc_x_g, row->imu[1].acc_y_g, row->imu[1].acc_z_g,
        row->imu[1].lin_acc_x_g, row->imu[1].lin_acc_y_g, row->imu[1].lin_acc_z_g,
        row->imu[2].valid ? 1 : 0, row->imu[2].age_ms,
        row->imu[2].roll_deg, row->imu[2].pitch_deg, row->imu[2].yaw_deg,
        row->imu[2].gyro_x_dps, row->imu[2].gyro_y_dps, row->imu[2].gyro_z_dps,
        row->imu[2].acc_x_g, row->imu[2].acc_y_g, row->imu[2].acc_z_g,
        row->imu[2].lin_acc_x_g, row->imu[2].lin_acc_y_g, row->imu[2].lin_acc_z_g,
        row->imu[3].valid ? 1 : 0, row->imu[3].age_ms,
        row->imu[3].roll_deg, row->imu[3].pitch_deg, row->imu[3].yaw_deg,
        row->imu[3].gyro_x_dps, row->imu[3].gyro_y_dps, row->imu[3].gyro_z_dps,
        row->imu[3].acc_x_g, row->imu[3].acc_y_g, row->imu[3].acc_z_g,
        row->imu[3].lin_acc_x_g, row->imu[3].lin_acc_y_g, row->imu[3].lin_acc_z_g);
}

bool rehab_sensor_writer_allows_periodic_row(rehab_training_state_t state)
{
    return state == REHAB_TRAINING_STATE_RECORDING;
}

bool rehab_sensor_last_completed_matches(bool exists,
                                         const rehab_training_result_t *last_completed,
                                         rehab_training_state_t state,
                                         uint32_t generation)
{
    return exists && last_completed &&
           (state == REHAB_TRAINING_STATE_IDLE ||
            state == REHAB_TRAINING_STATE_ERROR) &&
           last_completed->generation == generation;
}

void rehab_sensor_fill_result_locked(rehab_training_result_t *result, bool accepted)
{
    rehab_training_result_t snapshot = g_rehab_sensor.last_result;

    memset(result, 0, sizeof(*result));
    if (rehab_sensor_last_completed_matches(g_rehab_sensor.last_result_exists,
                                            &snapshot,
                                            g_rehab_sensor.state,
                                            g_rehab_sensor.generation)) {
        *result = snapshot;
        result->accepted = accepted;
        return;
    }
    result->accepted = accepted;
    result->state = g_rehab_sensor.state;
    result->generation = g_rehab_sensor.generation;
    strlcpy(result->task_name, g_rehab_sensor.task_name, sizeof(result->task_name));
    strlcpy(result->session_path, g_rehab_sensor.session_path, sizeof(result->session_path));
    strlcpy(result->csv_path, g_rehab_sensor.csv_path, sizeof(result->csv_path));
    strlcpy(result->task_json_path,
            g_rehab_sensor.task_json_path,
            sizeof(result->task_json_path));
    result->rows_written = g_rehab_sensor.rows_written;
    result->start_log_seq = g_rehab_sensor.start_log_seq;
    result->end_log_seq = g_rehab_sensor.end_log_seq;
    result->duration_ms = g_rehab_sensor.session_start_us > 0 ?
        rehab_sensor_age_ms(esp_timer_get_time(), g_rehab_sensor.session_start_us) : 0;
    result->queue_drop_count = g_rehab_sensor.queue_drop_count;
    result->write_error_count = g_rehab_sensor.write_error_count;
    strlcpy(result->end_reason,
            g_rehab_sensor.requested_end_reason,
            sizeof(result->end_reason));
}

static void rehab_sensor_set_result_error(rehab_training_result_t *result,
                                          const char *code,
                                          const char *message)
{
    strlcpy(result->error_code, code, sizeof(result->error_code));
    strlcpy(result->error_message, message, sizeof(result->error_message));
}

static void rehab_sensor_cleanup_failed_start(rehab_start_context_t *context)
{
    if (!context) {
        return;
    }
    if (context->csv_file) {
        fclose(context->csv_file);
        context->csv_file = NULL;
    }
    free(context->stdio_buffer);
    context->stdio_buffer = NULL;
    if (context->task_json_created) {
        (void)unlink(context->task_json_path);
    }
    if (context->csv_created) {
        (void)unlink(context->csv_path);
    }
    if (context->session_created) {
        (void)rmdir(context->session_path);
    }
}

static esp_err_t rehab_sensor_start_failure(rehab_start_context_t *context,
                                            rehab_training_result_t *result,
                                            esp_err_t err,
                                            int system_errno,
                                            const char *code,
                                            const char *message)
{
    rehab_sensor_cleanup_failed_start(context);
    if (rehab_sensor_lock() == ESP_OK) {
        if (g_rehab_sensor.state == REHAB_TRAINING_STATE_STARTING) {
            g_rehab_sensor.state = REHAB_TRAINING_STATE_IDLE;
        }
        g_rehab_sensor.session_clock_fallback = false;
        g_rehab_sensor.session_start_epoch = 0;
        g_rehab_sensor.session_stop_us = 0;
        rehab_sensor_set_error_locked(err, system_errno, message);
        rehab_sensor_fill_result_locked(result, false);
        rehab_sensor_set_result_error(result, code, message);
        rehab_sensor_unlock();
    }
    return err;
}

esp_err_t rehab_training_start(const char *task_name,
                               rehab_training_result_t *result)
{
    rehab_start_context_t *context;
    char date[9] = {0};
    struct stat storage_info = {0};
    esp_err_t err;

    if (!result) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(result, 0, sizeof(*result));
    if (!rehab_sensor_valid_task_name(task_name)) {
        rehab_sensor_set_result_error(result,
                                      "invalid_task_name",
                                      "task_name must be non-empty valid UTF-8 without control characters and at most 96 bytes");
        return ESP_ERR_INVALID_ARG;
    }
    if (!g_rehab_sensor.mutex) {
        rehab_sensor_set_result_error(result, "not_initialized", "sensor recording is not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (rehab_sensor_lock() != ESP_OK) {
        rehab_sensor_set_result_error(result, "state_unavailable", "sensor recording state is unavailable");
        return ESP_ERR_TIMEOUT;
    }
    if (!g_rehab_sensor.initialized) {
        rehab_sensor_fill_result_locked(result, false);
        rehab_sensor_set_result_error(result, "not_initialized", "sensor recording is not initialized");
        rehab_sensor_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    if (g_rehab_sensor.state != REHAB_TRAINING_STATE_IDLE) {
        rehab_sensor_fill_result_locked(result, false);
        rehab_sensor_set_result_error(result,
                                      g_rehab_sensor.state == REHAB_TRAINING_STATE_RECORDING ?
                                      "already_recording" : "state_not_idle",
                                      "training start requires idle state");
        rehab_sensor_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    if (strcmp(g_rehab_sensor.storage_base, "/sdcard") != 0) {
        rehab_sensor_set_error_locked(ESP_ERR_INVALID_STATE, 0, "sd_not_ready");
        rehab_sensor_fill_result_locked(result, false);
        rehab_sensor_set_result_error(result, "sd_not_ready", "formal training requires /sdcard storage");
        rehab_sensor_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    rehab_sensor_unlock();

    if (stat("/sdcard", &storage_info) != 0 || !S_ISDIR(storage_info.st_mode)) {
        int saved_errno = errno;
        rehab_sensor_set_result_error(result,
                                      "sd_not_ready",
                                      "formal training requires a mounted /sdcard");
        if (rehab_sensor_lock() == ESP_OK) {
            rehab_sensor_set_error_locked(ESP_ERR_INVALID_STATE, saved_errno, "sd_not_ready");
            rehab_sensor_fill_result_locked(result, false);
            rehab_sensor_set_result_error(result,
                                          "sd_not_ready",
                                          "formal training requires a mounted /sdcard");
            rehab_sensor_unlock();
        }
        return ESP_ERR_INVALID_STATE;
    }

    context = calloc(1, sizeof(*context));
    if (!context) {
        rehab_sensor_set_result_error(result, "out_of_memory", "unable to allocate start context");
        return ESP_ERR_NO_MEM;
    }
    if (!rehab_sensor_resolve_session_clock(date,
                                            context->start_time,
                                            &context->clock_fallback,
                                            &context->start_epoch)) {
        free(context);
        if (rehab_sensor_lock() == ESP_OK) {
            rehab_sensor_set_error_locked(ESP_ERR_INVALID_STATE,
                                          0,
                                          "session_clock_unavailable");
            rehab_sensor_unlock();
        }
        rehab_sensor_set_result_error(result,
                                      "session_clock_unavailable",
                                      "unable to create a legal session-local timestamp");
        return ESP_ERR_INVALID_STATE;
    }
    if (context->clock_fallback) {
        ESP_LOGW(TAG, "wall clock unavailable; using session-local fallback");
    }

    if (rehab_sensor_lock() != ESP_OK) {
        free(context);
        return ESP_ERR_TIMEOUT;
    }
    rehab_sensor_refresh_health_locked(esp_timer_get_time());
    if (g_rehab_sensor.state != REHAB_TRAINING_STATE_IDLE) {
        rehab_sensor_fill_result_locked(result, false);
        rehab_sensor_set_result_error(result, "already_recording", "another training start is active");
        rehab_sensor_unlock();
        free(context);
        return ESP_ERR_INVALID_STATE;
    }
    if (!g_rehab_sensor.required_sensors_ok) {
        rehab_sensor_set_error_locked(ESP_ERR_INVALID_STATE, 0, "sensors_not_ready");
        rehab_sensor_fill_result_locked(result, false);
        rehab_sensor_set_result_error(result, "sensors_not_ready", "all required S3 sensors must be online, valid, and updating");
        rehab_sensor_unlock();
        free(context);
        return ESP_ERR_INVALID_STATE;
    }
    if (!g_rehab_sensor.writer_ready || !g_rehab_sensor.writer_alive) {
        rehab_sensor_set_error_locked(ESP_ERR_INVALID_STATE, 0, "writer_not_ready");
        rehab_sensor_fill_result_locked(result, false);
        rehab_sensor_set_result_error(result, "writer_not_ready", "training writer is not ready");
        rehab_sensor_unlock();
        free(context);
        return ESP_ERR_INVALID_STATE;
    }

    g_rehab_sensor.state = REHAB_TRAINING_STATE_STARTING;
    g_rehab_sensor.generation++;
    strlcpy(g_rehab_sensor.task_name, task_name, sizeof(g_rehab_sensor.task_name));
    rehab_sensor_clear_error_locked();
    rehab_sensor_unlock();

    err = rehab_sensor_ensure_session_root();
    if (err != ESP_OK) {
        int saved_errno = errno;
        err = rehab_sensor_start_failure(context,
                                         result,
                                         err,
                                         saved_errno,
                                         "file_create_failed",
                                         "unable to create or access /sdcard/rehab_sessions");
        free(context);
        return err;
    }
    err = rehab_sensor_create_session_directory(date,
                                                context->session_path,
                                                sizeof(context->session_path));
    if (err != ESP_OK) {
        int saved_errno = errno;
        err = rehab_sensor_start_failure(context,
                                         result,
                                         err,
                                         saved_errno,
                                         "file_create_failed",
                                         "unable to create a unique daily training directory");
        free(context);
        return err;
    }
    context->session_created = true;

    /* The session directory exists before the per-session queue and counters reset. */
    if (rehab_sensor_lock() != ESP_OK) {
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_ERR_TIMEOUT,
                                         0,
                                         "state_unavailable",
                                         "unable to prepare the training session state");
        free(context);
        return err;
    }
    if (g_rehab_sensor.state != REHAB_TRAINING_STATE_STARTING) {
        rehab_sensor_unlock();
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_ERR_INVALID_STATE,
                                         0,
                                         "state_not_starting",
                                         "training start state changed before session preparation");
        free(context);
        return err;
    }
    g_rehab_sensor.recording = false;
    rehab_sensor_unlock();

    xQueueReset(g_rehab_sensor.queue);
    if (rehab_sensor_lock() != ESP_OK) {
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_ERR_TIMEOUT,
                                         0,
                                         "state_unavailable",
                                         "unable to reset the training session counters");
        free(context);
        return err;
    }
    if (g_rehab_sensor.state != REHAB_TRAINING_STATE_STARTING) {
        rehab_sensor_unlock();
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_ERR_INVALID_STATE,
                                         0,
                                         "state_not_starting",
                                         "training start state changed during session preparation");
        free(context);
        return err;
    }
    g_rehab_sensor.session_path[0] = '\0';
    g_rehab_sensor.csv_path[0] = '\0';
    g_rehab_sensor.csv_name[0] = '\0';
    g_rehab_sensor.task_json_path[0] = '\0';
    g_rehab_sensor.start_time[0] = '\0';
    g_rehab_sensor.requested_end_reason[0] = '\0';
    g_rehab_sensor.session_clock_fallback = false;
    g_rehab_sensor.session_start_epoch = 0;
    g_rehab_sensor.session_stop_us = 0;
    g_rehab_sensor.rows_written = 0;
    g_rehab_sensor.end_log_seq = 0;
    g_rehab_sensor.queue_drop_count = 0;
    g_rehab_sensor.queue_high_water = 0;
    g_rehab_sensor.write_error_count = 0;
    g_rehab_sensor.enqueue_inflight = 0;
    g_rehab_sensor.anomaly_active = false;
    g_rehab_sensor.anomaly_over_10s = false;
    g_rehab_sensor.anomaly_start_us = 0;
    g_rehab_sensor.nodes[0].session_gap_count = 0;
    g_rehab_sensor.nodes[1].session_gap_count = 0;
    context->start_log_seq = g_rehab_sensor.next_log_seq;
    g_rehab_sensor.start_log_seq = context->start_log_seq;
    rehab_sensor_unlock();

    int written = snprintf(context->csv_name,
                           sizeof(context->csv_name),
                           "DATA_SEQ%010" PRIu64 ".csv",
                           context->start_log_seq);
    int csv_path_written = snprintf(context->csv_path,
                                    sizeof(context->csv_path),
                                    "%s/%s",
                                    context->session_path,
                                    context->csv_name);
    int task_path_written = snprintf(context->task_json_path,
                                     sizeof(context->task_json_path),
                                     "%s/task.json",
                                     context->session_path);
    if (written < 0 || (size_t)written >= sizeof(context->csv_name) ||
            csv_path_written < 0 ||
            (size_t)csv_path_written >= sizeof(context->csv_path) ||
            task_path_written < 0 ||
            (size_t)task_path_written >= sizeof(context->task_json_path)) {
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_ERR_INVALID_SIZE,
                                         0,
                                         "file_create_failed",
                                         "training path exceeds fixed path limits");
        free(context);
        return err;
    }

    context->stdio_buffer = malloc(REHAB_SENSOR_STDIO_BUFFER_SIZE);
    if (!context->stdio_buffer) {
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_ERR_NO_MEM,
                                         0,
                                         "out_of_memory",
                                         "unable to allocate the CSV stdio buffer");
        free(context);
        return err;
    }
    context->csv_file = fopen(context->csv_path, "wb");
    if (!context->csv_file) {
        int saved_errno = errno;
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_FAIL,
                                         saved_errno,
                                         "file_create_failed",
                                         "unable to create the training CSV");
        free(context);
        return err;
    }
    context->csv_created = true;
    if (setvbuf(context->csv_file,
                context->stdio_buffer,
                _IOFBF,
                REHAB_SENSOR_STDIO_BUFFER_SIZE) != 0) {
        int saved_errno = errno;
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_FAIL,
                                         saved_errno,
                                         "file_create_failed",
                                         "unable to configure the CSV stdio buffer");
        free(context);
        return err;
    }
    size_t header_length = strlen(REHAB_FUSED_CSV_HEADER);
    if (fwrite(REHAB_FUSED_CSV_HEADER, 1, header_length, context->csv_file) != header_length ||
            fwrite("\n", 1, 1, context->csv_file) != 1 ||
            fflush(context->csv_file) != 0 || ferror(context->csv_file)) {
        int saved_errno = errno;
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_FAIL,
                                         saved_errno,
                                         "file_create_failed",
                                         "unable to write and flush the CSV header");
        free(context);
        return err;
    }

    context->task_json_created = true;
    err = rehab_sensor_write_task_json_file(context->task_json_path,
                                            task_name,
                                            context->csv_name,
                                            context->start_time,
                                            "",
                                            context->start_log_seq,
                                            0,
                                            "");
    if (err != ESP_OK) {
        int saved_errno = errno;
        err = rehab_sensor_start_failure(context,
                                         result,
                                         err,
                                         saved_errno,
                                         "file_create_failed",
                                         "unable to create the seven-field task.json placeholder");
        free(context);
        return err;
    }
    if (rehab_sensor_lock() != ESP_OK) {
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_ERR_TIMEOUT,
                                         0,
                                         "state_unavailable",
                                         "unable to commit the training session state");
        free(context);
        return err;
    }
    if (g_rehab_sensor.state != REHAB_TRAINING_STATE_STARTING ||
            !g_rehab_sensor.writer_ready) {
        rehab_sensor_unlock();
        err = rehab_sensor_start_failure(context,
                                         result,
                                         ESP_ERR_INVALID_STATE,
                                         0,
                                         "writer_not_ready",
                                         "writer became unavailable before recording could start");
        free(context);
        return err;
    }

    strlcpy(g_rehab_sensor.task_name, task_name, sizeof(g_rehab_sensor.task_name));
    strlcpy(g_rehab_sensor.session_path,
            context->session_path,
            sizeof(g_rehab_sensor.session_path));
    strlcpy(g_rehab_sensor.csv_path, context->csv_path, sizeof(g_rehab_sensor.csv_path));
    strlcpy(g_rehab_sensor.csv_name, context->csv_name, sizeof(g_rehab_sensor.csv_name));
    strlcpy(g_rehab_sensor.task_json_path,
            context->task_json_path,
            sizeof(g_rehab_sensor.task_json_path));
    strlcpy(g_rehab_sensor.start_time,
            context->start_time,
            sizeof(g_rehab_sensor.start_time));
    g_rehab_sensor.requested_end_reason[0] = '\0';
    g_rehab_sensor.csv_file = context->csv_file;
    g_rehab_sensor.stdio_buffer = context->stdio_buffer;
    g_rehab_sensor.writer_seed[0].has_packet = g_rehab_sensor.nodes[0].has_latest;
    g_rehab_sensor.writer_seed[0].packet = g_rehab_sensor.nodes[0].latest;
    g_rehab_sensor.writer_seed[0].rx_us = g_rehab_sensor.nodes[0].last_rx_us;
    g_rehab_sensor.writer_seed[1].has_packet = g_rehab_sensor.nodes[1].has_latest;
    g_rehab_sensor.writer_seed[1].packet = g_rehab_sensor.nodes[1].latest;
    g_rehab_sensor.writer_seed[1].rx_us = g_rehab_sensor.nodes[1].last_rx_us;
    g_rehab_sensor.session_start_us = esp_timer_get_time();
    g_rehab_sensor.session_stop_us = 0;
    g_rehab_sensor.session_start_epoch = context->start_epoch;
    g_rehab_sensor.session_clock_fallback = context->clock_fallback;
    g_rehab_sensor.writer_last_progress_ms =
        (uint64_t)g_rehab_sensor.session_start_us / 1000u;
    g_rehab_sensor.state = REHAB_TRAINING_STATE_RECORDING;
    g_rehab_sensor.recording = true;
    rehab_sensor_fill_result_locked(result, true);
    rehab_sensor_unlock();

    context->csv_file = NULL;
    context->stdio_buffer = NULL;
    xTaskNotifyGive(g_rehab_sensor.writer_task);
    ESP_LOGI(TAG,
             "training start generation=%" PRIu32 " session=%s csv=%s",
             result->generation,
             result->session_path,
             result->csv_path);
    free(context);
    return ESP_OK;
}

static void rehab_writer_apply_item(rehab_writer_context_t *context,
                                    const rehab_record_item_t *item)
{
    unsigned node_index;

    if (item->generation != context->generation) {
        return;
    }
    node_index = item->packet.node_id == 1 ? 0 : 1;
    context->nodes[node_index].has_packet = true;
    context->nodes[node_index].packet = item->packet;
    context->nodes[node_index].rx_us = item->rx_us;
}

static void rehab_writer_mark_error(esp_err_t err, int system_errno, const char *message)
{
    if (rehab_sensor_lock() == ESP_OK) {
        g_rehab_sensor.recording = false;
        g_rehab_sensor.state = REHAB_TRAINING_STATE_ERROR;
        g_rehab_sensor.write_error_count++;
        strlcpy(g_rehab_sensor.requested_end_reason,
                "write_error",
                sizeof(g_rehab_sensor.requested_end_reason));
        rehab_sensor_set_error_locked(err, system_errno, message);
        rehab_sensor_unlock();
    }
    ESP_LOGE(TAG, "writer error=%s errno=%d", message, system_errno);
}

static esp_err_t rehab_writer_write_row(rehab_writer_context_t *context, int64_t now_us)
{
    uint64_t log_seq;
    FILE *csv_file;
    int length;

    if (rehab_sensor_lock() != ESP_OK) {
        return ESP_ERR_TIMEOUT;
    }
    rehab_sensor_refresh_health_locked(now_us);
    context->node_status[0] = g_rehab_sensor.nodes[0].public_status;
    context->node_status[1] = g_rehab_sensor.nodes[1].public_status;
    context->session_gap[0] = g_rehab_sensor.nodes[0].session_gap_count;
    context->session_gap[1] = g_rehab_sensor.nodes[1].session_gap_count;
    log_seq = g_rehab_sensor.next_log_seq;
    csv_file = g_rehab_sensor.csv_file;
    rehab_sensor_unlock();

    if (!csv_file) {
        rehab_writer_mark_error(ESP_ERR_INVALID_STATE, 0, "csv_file_not_open");
        return ESP_ERR_INVALID_STATE;
    }
    rehab_sensor_build_fused_row(context->nodes,
                                 context->node_status,
                                 context->session_gap,
                                 now_us,
                                 log_seq,
                                 &context->row);
    length = rehab_sensor_format_csv_row(context->line,
                                         sizeof(context->line),
                                         &context->row);
    if (length < 0 || (size_t)length >= sizeof(context->line)) {
        rehab_writer_mark_error(ESP_ERR_INVALID_SIZE, 0, "csv_line_truncated");
        return ESP_ERR_INVALID_SIZE;
    }
    if (fwrite(context->line, 1, (size_t)length, csv_file) != (size_t)length ||
            fwrite("\n", 1, 1, csv_file) != 1 || ferror(csv_file)) {
        int saved_errno = errno;
        rehab_writer_mark_error(ESP_FAIL, saved_errno, "csv_write_failed");
        return ESP_FAIL;
    }

    if (rehab_sensor_lock() == ESP_OK) {
        if (context->row.sanitized_node_mask & (1u << 0)) {
            g_rehab_sensor.nodes[0].public_status.non_finite_count++;
        }
        if (context->row.sanitized_node_mask & (1u << 1)) {
            g_rehab_sensor.nodes[1].public_status.non_finite_count++;
        }
        g_rehab_sensor.rows_written++;
        g_rehab_sensor.end_log_seq = log_seq;
        g_rehab_sensor.next_log_seq = log_seq + 1;
        g_rehab_sensor.writer_last_progress_ms = (uint64_t)now_us / 1000u;
        rehab_sensor_unlock();
    }
    context->last_row_us = now_us;

    if (now_us - context->last_flush_us >= REHAB_SENSOR_FLUSH_INTERVAL_MS * 1000LL) {
        if (fflush(csv_file) != 0 || ferror(csv_file)) {
            int saved_errno = errno;
            rehab_writer_mark_error(ESP_FAIL, saved_errno, "csv_flush_failed");
            return ESP_FAIL;
        }
        context->last_flush_us = now_us;
    }
    if (now_us - context->last_fsync_us >= REHAB_SENSOR_FSYNC_INTERVAL_MS * 1000LL) {
        if (fflush(csv_file) != 0 || fsync(fileno(csv_file)) != 0) {
            int saved_errno = errno;
            rehab_writer_mark_error(ESP_FAIL, saved_errno, "csv_fsync_failed");
            return ESP_FAIL;
        }
        context->last_fsync_us = now_us;
    }
    return ESP_OK;
}

static void rehab_writer_finish_session(rehab_writer_context_t *context)
{
    FILE *csv_file;
    char *stdio_buffer;
    char date[9] = {0};
    uint64_t start_seq;
    uint64_t end_seq;
    uint64_t rows;
    bool failed;
    bool final_task_replaced = false;
    int64_t final_end_epoch = 0;
    TaskHandle_t waiter;
    rehab_training_finalized_cb_t finalized_callback;
    void *finalized_user_ctx;

    if (rehab_sensor_lock() != ESP_OK) {
        return;
    }
    csv_file = g_rehab_sensor.csv_file;
    stdio_buffer = g_rehab_sensor.stdio_buffer;
    strlcpy(context->final_task_name,
            g_rehab_sensor.task_name,
            sizeof(context->final_task_name));
    strlcpy(context->final_session_path,
            g_rehab_sensor.session_path,
            sizeof(context->final_session_path));
    strlcpy(context->final_csv_name,
            g_rehab_sensor.csv_name,
            sizeof(context->final_csv_name));
    strlcpy(context->final_task_json_path,
            g_rehab_sensor.task_json_path,
            sizeof(context->final_task_json_path));
    strlcpy(context->final_start_time,
            g_rehab_sensor.start_time,
            sizeof(context->final_start_time));
    strlcpy(context->final_end_reason,
            g_rehab_sensor.state == REHAB_TRAINING_STATE_ERROR ?
            "write_error" : g_rehab_sensor.requested_end_reason,
            sizeof(context->final_end_reason));
    start_seq = g_rehab_sensor.start_log_seq;
    end_seq = g_rehab_sensor.end_log_seq;
    rows = g_rehab_sensor.rows_written;
    failed = g_rehab_sensor.state == REHAB_TRAINING_STATE_ERROR;
    context->final_clock_fallback = g_rehab_sensor.session_clock_fallback;
    context->final_start_epoch = g_rehab_sensor.session_start_epoch;
    context->final_start_us = g_rehab_sensor.session_start_us;
    context->final_stop_us = g_rehab_sensor.session_stop_us;
    rehab_sensor_unlock();

    if (csv_file) {
        if (fflush(csv_file) != 0 || ferror(csv_file) || fsync(fileno(csv_file)) != 0) {
            failed = true;
            rehab_writer_mark_error(ESP_FAIL, errno, "csv_final_sync_failed");
        }
        if (fclose(csv_file) != 0) {
            failed = true;
            rehab_writer_mark_error(ESP_FAIL, errno, "csv_close_failed");
        }
    } else {
        failed = true;
    }
    free(stdio_buffer);

    if (rows == 0) {
        failed = true;
        strlcpy(context->final_end_reason,
                "write_error",
                sizeof(context->final_end_reason));
        rehab_writer_mark_error(ESP_FAIL, 0, "no_csv_rows_written");
    }
    bool end_time_available;
    if (context->final_clock_fallback) {
        int64_t stop_us = context->final_stop_us > 0 ?
                          context->final_stop_us : esp_timer_get_time();
        end_time_available = rehab_sensor_fallback_end_time(context->final_start_epoch,
                                                            context->final_start_us,
                                                            stop_us,
                                                            date,
                                                            context->final_end_time,
                                                            &final_end_epoch);
    } else {
        end_time_available = rehab_sensor_wall_clock(date, context->final_end_time);
        if (end_time_available) {
            (void)rehab_sensor_parse_iso8601_epoch(context->final_end_time,
                                                   &final_end_epoch);
        }
    }
    if (!end_time_available) {
        failed = true;
        strlcpy(context->final_end_reason,
                "write_error",
                sizeof(context->final_end_reason));
        rehab_writer_mark_error(ESP_FAIL, 0, "end_time_not_available");
    }
    if (failed) {
        strlcpy(context->final_end_reason,
                "write_error",
                sizeof(context->final_end_reason));
    }
    if (rehab_sensor_replace_final_task_json(context->final_task_json_path,
                                             context->final_task_name,
                                             context->final_csv_name,
                                             context->final_start_time,
                                             context->final_end_time,
                                             start_seq,
                                             rows > 0 ? end_seq : 0,
                                             context->final_end_reason) == ESP_OK) {
        final_task_replaced = true;
    } else {
        failed = true;
        strlcpy(context->final_end_reason,
                "write_error",
                sizeof(context->final_end_reason));
        rehab_writer_mark_error(ESP_FAIL, errno, "task_json_replace_failed");
        (void)rehab_sensor_replace_final_task_json(context->final_task_json_path,
                                                   context->final_task_name,
                                                   context->final_csv_name,
                                                   context->final_start_time,
                                                   context->final_end_time,
                                                   start_seq,
                                                   rows > 0 ? end_seq : 0,
                                                   context->final_end_reason);
    }

    if (rehab_sensor_lock() != ESP_OK) {
        return;
    }
    g_rehab_sensor.csv_file = NULL;
    g_rehab_sensor.stdio_buffer = NULL;
    g_rehab_sensor.recording = false;
    g_rehab_sensor.state = failed ? REHAB_TRAINING_STATE_ERROR : REHAB_TRAINING_STATE_IDLE;
    strlcpy(g_rehab_sensor.requested_end_reason,
            context->final_end_reason,
            sizeof(g_rehab_sensor.requested_end_reason));
    rehab_sensor_fill_result_locked(&g_rehab_sensor.last_result, true);
    g_rehab_sensor.last_result_exists = true;
    g_rehab_sensor.last_result.state = g_rehab_sensor.state;
    g_rehab_sensor.last_result.duration_ms =
        rehab_sensor_age_ms(esp_timer_get_time(), g_rehab_sensor.session_start_us);
    strlcpy(g_rehab_sensor.last_result.end_reason,
            context->final_end_reason,
            sizeof(g_rehab_sensor.last_result.end_reason));
    if (failed) {
        rehab_sensor_set_result_error(&g_rehab_sensor.last_result,
                                      "write_error",
                                      g_rehab_sensor.last_error[0] ?
                                      g_rehab_sensor.last_error : "training writer failed");
    }
    if (!failed && final_task_replaced && final_end_epoch > 0) {
        g_rehab_sensor.latest_finalized_end_epoch = final_end_epoch;
    }
    g_rehab_sensor.session_clock_fallback = false;
    g_rehab_sensor.session_start_epoch = 0;
    g_rehab_sensor.session_stop_us = 0;
    waiter = g_rehab_sensor.stop_waiter;
    g_rehab_sensor.stop_waiter = NULL;
    finalized_callback = s_finalized_callback;
    finalized_user_ctx = s_finalized_user_ctx;
    rehab_sensor_unlock();

    if (!failed && finalized_callback) {
        const char *separator = strrchr(context->final_session_path, '/');
        const char *session_id = separator ? separator + 1 : context->final_session_path;
        finalized_callback(session_id,
                           context->final_session_path,
                           finalized_user_ctx);
    }
    if (waiter) {
        xTaskNotifyGive(waiter);
    }
    if (!failed) {
        (void)rehab_business_audio_notify(
            REHAB_BUSINESS_AUDIO_TRAINING_COMPLETED);
    }
    ESP_LOGI(TAG,
             "training stop state=%s rows=%" PRIu64 " start=%" PRIu64
             " end=%" PRIu64 " reason=%s",
             failed ? "error" : "idle",
             rows,
             start_seq,
             rows > 0 ? end_seq : 0,
             context->final_end_reason);
}

esp_err_t rehab_training_register_finalized_callback(
    rehab_training_finalized_cb_t callback,
    void *user_ctx)
{
    if (!callback || !g_rehab_sensor.mutex) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = rehab_sensor_lock();
    if (err != ESP_OK) {
        return err;
    }
    if (s_finalized_callback && s_finalized_callback != callback) {
        rehab_sensor_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    s_finalized_callback = callback;
    s_finalized_user_ctx = user_ctx;
    rehab_sensor_unlock();
    return ESP_OK;
}

void rehab_training_writer_task(void *arg)
{
    rehab_writer_context_t *context = calloc(1, sizeof(*context));
    (void)arg;

    if (!context) {
        rehab_writer_mark_error(ESP_ERR_NO_MEM, 0, "writer_context_allocation_failed");
        if (rehab_sensor_lock() == ESP_OK) {
            g_rehab_sensor.writer_alive = false;
            g_rehab_sensor.writer_ready = false;
            rehab_sensor_unlock();
        }
        vTaskDelete(NULL);
        return;
    }

    for (;;) {
        (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        if (rehab_sensor_lock() != ESP_OK) {
            continue;
        }
        if (g_rehab_sensor.state != REHAB_TRAINING_STATE_RECORDING &&
                g_rehab_sensor.state != REHAB_TRAINING_STATE_STOPPING) {
            rehab_sensor_unlock();
            continue;
        }
        memset(context, 0, sizeof(*context));
        context->nodes[0] = g_rehab_sensor.writer_seed[0];
        context->nodes[1] = g_rehab_sensor.writer_seed[1];
        context->generation = g_rehab_sensor.generation;
        context->next_tick_us = g_rehab_sensor.session_start_us;
        context->last_flush_us = g_rehab_sensor.session_start_us;
        context->last_fsync_us = g_rehab_sensor.session_start_us;
        rehab_sensor_unlock();

        for (;;) {
            while (xQueueReceive(g_rehab_sensor.queue, &context->item, 0) == pdTRUE) {
                rehab_writer_apply_item(context, &context->item);
            }

            int64_t now_us = esp_timer_get_time();
            rehab_training_state_t state;
            uint32_t inflight;
            uint64_t rows;
            if (rehab_sensor_lock() != ESP_OK) {
                vTaskDelay(1);
                continue;
            }
            state = g_rehab_sensor.state;
            inflight = g_rehab_sensor.enqueue_inflight;
            rows = g_rehab_sensor.rows_written;
            rehab_sensor_unlock();

            if (rehab_sensor_writer_allows_periodic_row(state)) {
                if (now_us >= context->next_tick_us) {
                    if (rehab_writer_write_row(context, now_us) != ESP_OK) {
                        continue;
                    }
                    do {
                        context->next_tick_us += REHAB_SENSOR_OUTPUT_PERIOD_MS * 1000LL;
                    } while (context->next_tick_us <= now_us);
                }
                int64_t wait_us = context->next_tick_us - esp_timer_get_time();
                TickType_t wait_ticks = wait_us > 1000 ?
                    pdMS_TO_TICKS((uint32_t)(wait_us / 1000)) : 1;
                if (xQueueReceive(g_rehab_sensor.queue,
                                  &context->item,
                                  wait_ticks) == pdTRUE) {
                    rehab_writer_apply_item(context, &context->item);
                }
                continue;
            }

            if (state == REHAB_TRAINING_STATE_STOPPING ||
                    state == REHAB_TRAINING_STATE_ERROR) {
                if (inflight != 0 || uxQueueMessagesWaiting(g_rehab_sensor.queue) != 0) {
                    vTaskDelay(1);
                    continue;
                }
                if (state == REHAB_TRAINING_STATE_STOPPING &&
                        (rows == 0 || now_us >= context->next_tick_us)) {
                    (void)rehab_writer_write_row(context, now_us);
                }
                rehab_writer_finish_session(context);
                break;
            }
            break;
        }
    }
}

esp_err_t rehab_training_stop(const char *reason,
                              uint32_t wait_timeout_ms,
                              rehab_training_result_t *result)
{
    const char *effective_reason = reason && reason[0] ? reason : "user_stop";
    bool wait_for_completion = false;
    TaskHandle_t current_task = xTaskGetCurrentTaskHandle();

    if (!result) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(result, 0, sizeof(*result));
    if (!rehab_sensor_valid_end_reason(effective_reason)) {
        rehab_sensor_set_result_error(result,
                                      "invalid_reason",
                                      "reason must be user_stop, completed, write_error, or sensor_timeout");
        return ESP_ERR_INVALID_ARG;
    }
    if (!g_rehab_sensor.mutex) {
        rehab_sensor_set_result_error(result, "not_initialized", "sensor recording is not initialized");
        return ESP_ERR_INVALID_STATE;
    }
    if (rehab_sensor_lock() != ESP_OK) {
        return ESP_ERR_TIMEOUT;
    }
    if (!g_rehab_sensor.initialized) {
        rehab_sensor_fill_result_locked(result, false);
        rehab_sensor_set_result_error(result, "not_initialized", "sensor recording is not initialized");
        rehab_sensor_unlock();
        return ESP_ERR_INVALID_STATE;
    }

    if (g_rehab_sensor.state == REHAB_TRAINING_STATE_IDLE ||
            g_rehab_sensor.state == REHAB_TRAINING_STATE_ERROR) {
        if (g_rehab_sensor.last_result_exists &&
                (g_rehab_sensor.state == REHAB_TRAINING_STATE_IDLE ||
                 g_rehab_sensor.csv_file == NULL)) {
            *result = g_rehab_sensor.last_result;
            result->accepted = true;
        } else {
            rehab_sensor_fill_result_locked(result, true);
        }
        rehab_sensor_unlock();
        return ESP_OK;
    }
    if (g_rehab_sensor.state == REHAB_TRAINING_STATE_STARTING) {
        rehab_sensor_fill_result_locked(result, false);
        rehab_sensor_set_result_error(result, "state_starting", "training start is still being prepared");
        rehab_sensor_unlock();
        return ESP_ERR_INVALID_STATE;
    }
    if (g_rehab_sensor.state == REHAB_TRAINING_STATE_RECORDING) {
        g_rehab_sensor.session_stop_us = esp_timer_get_time();
        g_rehab_sensor.recording = false;
        g_rehab_sensor.state = REHAB_TRAINING_STATE_STOPPING;
        strlcpy(g_rehab_sensor.requested_end_reason,
                effective_reason,
                sizeof(g_rehab_sensor.requested_end_reason));
    }
    if (wait_timeout_ms > 0 && !g_rehab_sensor.stop_waiter) {
        g_rehab_sensor.stop_waiter = current_task;
        wait_for_completion = true;
    }
    rehab_sensor_fill_result_locked(result, true);
    rehab_sensor_unlock();
    xTaskNotifyGive(g_rehab_sensor.writer_task);

    if (wait_for_completion) {
        (void)ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(wait_timeout_ms));
    }
    if (rehab_sensor_lock() != ESP_OK) {
        return ESP_ERR_TIMEOUT;
    }
    rehab_sensor_fill_result_locked(result, true);
    if (g_rehab_sensor.state == REHAB_TRAINING_STATE_STOPPING) {
        result->state = REHAB_TRAINING_STATE_STOPPING;
    }
    if (g_rehab_sensor.stop_waiter == current_task) {
        g_rehab_sensor.stop_waiter = NULL;
    }
    rehab_sensor_unlock();
    return ESP_OK;
}
