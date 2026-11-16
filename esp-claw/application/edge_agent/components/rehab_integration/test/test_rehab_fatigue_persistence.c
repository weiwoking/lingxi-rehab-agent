/* SPDX-License-Identifier: Apache-2.0 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "rehab_fatigue.h"

const char *rehab_test_data_root;
static time_t s_test_now;

time_t rehab_test_time(time_t *destination)
{
    if (destination != NULL) {
        *destination = s_test_now;
    }
    return s_test_now;
}

static time_t local_epoch(int year, int month, int day, int hour, int minute)
{
    struct tm local = {
        .tm_year = year - 1900,
        .tm_mon = month - 1,
        .tm_mday = day,
        .tm_hour = hour,
        .tm_min = minute,
        .tm_isdst = -1,
    };
    time_t epoch = mktime(&local);
    assert(epoch > 0);
    return epoch;
}

static void make_dir(const char *path)
{
    int result = mkdir(path, 0755);
    assert(result == 0);
}

static void path_for(char *out, size_t out_size, const char *leaf)
{
    int written = snprintf(out, out_size, "%s/%s", rehab_test_data_root, leaf);
    assert(written > 0 && (size_t)written < out_size);
}

static void expect_unavailable(void)
{
    rehab_fatigue_status_t status;
    rehab_fatigue_gate_t gate;
    assert(rehab_fatigue_init() != ESP_OK);
    assert(rehab_fatigue_get_status(&status) == ESP_OK);
    assert(!status.available);
    assert(rehab_fatigue_before_training(&gate) != ESP_OK);
    assert(!gate.available && !gate.blocked);
}

int main(int argc, char **argv)
{
    assert(argc == 3);
    const char *mode = argv[1];
    rehab_test_data_root = argv[2];
    s_test_now = local_epoch(2026, 8, 7, 10, 0);

    if (strcmp(mode, "create") == 0) {
        make_dir(rehab_test_data_root);
        assert(rehab_fatigue_init() == ESP_OK);
        rehab_fatigue_status_t status;
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(status.available && status.fatigue == 0);
        assert(status.count_today == 0 && status.counted_session_count == 0);
        assert(rehab_fatigue_on_score_succeeded("20260722_TASK005", 60.0) ==
               ESP_OK);
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(status.fatigue == 8 && status.count_today == 1 &&
               status.counted_session_count == 1);
        assert(rehab_fatigue_on_training_started() == ESP_OK);
        assert(rehab_fatigue_get_status(&status) == ESP_OK && status.decay_paused);
        assert(rehab_fatigue_on_training_stopped() == ESP_OK);
        assert(rehab_fatigue_get_status(&status) == ESP_OK && !status.decay_paused);
    } else if (strcmp(mode, "rollover") == 0) {
        make_dir(rehab_test_data_root);
        assert(rehab_fatigue_init() == ESP_OK);
        assert(rehab_fatigue_on_score_succeeded("20260807_TASK004", 60.0) ==
               ESP_OK);
        assert(rehab_fatigue_on_score_succeeded("20260807_TASK099", 40.0) ==
               ESP_OK);
        rehab_fatigue_status_t status;
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(strcmp(status.count_date, "20260807") == 0);
        assert(status.count_today == 2 && status.fatigue == 13);

        s_test_now = local_epoch(2026, 8, 8, 0, 1);
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(status.time_trusted);
        assert(strcmp(status.count_date, "20260808") == 0);
        assert(status.count_today == 0);
        assert(rehab_fatigue_on_score_succeeded("20260808_TASK099", 60.0) ==
               ESP_OK);
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(status.count_today == 1 && status.latest_delta == 8);
    } else if (strcmp(mode, "untrusted") == 0) {
        s_test_now = 0;
        make_dir(rehab_test_data_root);
        assert(rehab_fatigue_init() == ESP_OK);
        rehab_fatigue_status_t status;
        rehab_fatigue_gate_t gate;
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(status.available && !status.time_trusted);
        assert(status.count_date[0] == '\0' && status.count_today == 0);
        assert(rehab_fatigue_on_score_succeeded("20991231_TASK099", 60.0) ==
               ESP_OK);
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(status.fatigue == 0 && status.count_date[0] == '\0' &&
               status.count_today == 0 && status.counted_session_count == 0);
        assert(rehab_fatigue_before_training(&gate) == ESP_OK);
        assert(gate.available && !gate.blocked);
    } else if (strcmp(mode, "untrusted_existing_day") == 0) {
        make_dir(rehab_test_data_root);
        assert(rehab_fatigue_init() == ESP_OK);
        rehab_fatigue_status_t status;
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(strcmp(status.count_date, "20260807") == 0);

        s_test_now = 0;
        assert(rehab_fatigue_on_score_succeeded("20991231_TASK099", 60.0) ==
               ESP_OK);
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(!status.time_trusted);
        assert(strcmp(status.count_date, "20260807") == 0);
        assert(status.count_today == 1 && status.fatigue == 8 &&
               strcmp(status.latest_session_id, "20991231_TASK099") == 0);

        s_test_now = local_epoch(2026, 8, 8, 0, 1);
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(status.time_trusted);
        assert(strcmp(status.count_date, "20260808") == 0);
        assert(status.count_today == 0);
    } else if (strcmp(mode, "duplicate") == 0) {
        assert(rehab_fatigue_init() == ESP_OK);
        assert(rehab_fatigue_on_score_succeeded("20260722_TASK005", 60.0) ==
               ESP_OK);
        rehab_fatigue_status_t status;
        assert(rehab_fatigue_get_status(&status) == ESP_OK);
        assert(status.available && status.fatigue == 8 &&
               status.count_today == 1 && status.counted_session_count == 1);
    } else if (strcmp(mode, "corrupt") == 0) {
        make_dir(rehab_test_data_root);
        char path[256];
        path_for(path, sizeof(path), "rehab");
        make_dir(path);
        path_for(path, sizeof(path), "rehab/fatigue_state.json");
        FILE *file = fopen(path, "wb");
        assert(file != NULL);
        assert(fwrite("{broken", 1, 7, file) == 7);
        assert(fclose(file) == 0);
        expect_unavailable();
        file = fopen(path, "rb");
        assert(file != NULL);
        char bytes[8] = {0};
        assert(fread(bytes, 1, 7, file) == 7);
        assert(fclose(file) == 0);
        assert(strcmp(bytes, "{broken") == 0);
    } else if (strcmp(mode, "unwritable") == 0) {
        make_dir(rehab_test_data_root);
        char path[256];
        path_for(path, sizeof(path), "rehab");
        FILE *file = fopen(path, "wb");
        assert(file != NULL && fclose(file) == 0);
        expect_unavailable();
    } else {
        assert(!"unknown mode");
    }
    printf("rehab fatigue persistence %s: passed\n", mode);
    return 0;
}

