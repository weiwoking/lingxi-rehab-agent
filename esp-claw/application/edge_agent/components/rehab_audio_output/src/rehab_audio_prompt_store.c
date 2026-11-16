#include "rehab_audio_prompt_store.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#include "claw_paths.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"

#define REHAB_AUDIO_PROMPT_DIRECTORY "rehab/audio"
#define REHAB_AUDIO_PROMPT_PATH_MAX  256U

static const char *TAG = "rehab_audio_preload";

typedef struct {
    rehab_audio_output_prompt_t prompt;
    const char *filename;
    const char *media_type;
} prompt_definition_t;

static const prompt_definition_t s_prompt_definitions[] = {
    {REHAB_AUDIO_OUTPUT_PROMPT_PERSON_FOLLOW_STARTED,
     "person_follow_started.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_PERSON_FOLLOW_ARRIVED,
     "person_follow_arrived.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_ENV_SCAN_STARTED,
     "env_scan_started.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_ENV_SCAN_COMPLETED,
     "env_scan_completed.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_DECISION_STARTED,
     "decision_started.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_CUP_TRANSFER_L1,
     "training_ot_cup_transfer_l1.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_CUP_DRINK_L2,
     "training_ot_cup_drink_l2.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_CUP_DRINK_L3,
     "training_ot_cup_drink_l3.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_BALL_TRANSFER_L1,
     "training_ot_ball_transfer_l1.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_BALL_TRANSFER_L2,
     "training_ot_ball_transfer_l2.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_GENERIC,
     "training_generic.mp3", "audio/mpeg"},
    {REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_COMPLETED,
     "training_completed.mp3", "audio/mpeg"},
};

_Static_assert(sizeof(s_prompt_definitions) / sizeof(s_prompt_definitions[0]) ==
                   REHAB_AUDIO_PROMPT_COUNT,
               "all fixed business prompts must have preload definitions");

static void initialize_entries(rehab_audio_prompt_store_t *store)
{
    memset(store, 0, sizeof(*store));
    for (size_t i = 0; i < REHAB_AUDIO_PROMPT_COUNT; ++i) {
        store->entries[i].prompt = s_prompt_definitions[i].prompt;
        store->entries[i].filename = s_prompt_definitions[i].filename;
        store->entries[i].media_type = s_prompt_definitions[i].media_type;
    }
}

static void log_failure(const rehab_audio_prompt_entry_t *entry,
                        const char *stage,
                        esp_err_t error,
                        int saved_errno,
                        size_t bytes)
{
    ESP_LOGW(TAG,
             "BUSINESS_PROMPT_PRELOAD_FAILED prompt=%d filename=%s bytes=%u "
             "memory=psram available=0 stage=%s error=%s errno=%d",
             entry->prompt, entry->filename, (unsigned)bytes, stage,
             esp_err_to_name(error), saved_errno);
}

static void preload_entry(rehab_audio_prompt_entry_t *entry)
{
    char relative[REHAB_AUDIO_PROMPT_PATH_MAX];
    char path[REHAB_AUDIO_PROMPT_PATH_MAX];
    int length = snprintf(relative, sizeof(relative), "%s/%s",
                          REHAB_AUDIO_PROMPT_DIRECTORY, entry->filename);
    if (length <= 0 || length >= (int)sizeof(relative)) {
        log_failure(entry, "format_path", ESP_ERR_INVALID_SIZE, 0, 0U);
        return;
    }
    esp_err_t err = claw_paths_join(CLAW_PATH_DATA, relative, path,
                                    sizeof(path));
    if (err != ESP_OK) {
        log_failure(entry, "resolve_path", err, 0, 0U);
        return;
    }

    struct stat info = {0};
    if (stat(path, &info) != 0) {
        log_failure(entry, "stat", ESP_ERR_NOT_FOUND, errno, 0U);
        return;
    }
    if (!S_ISREG(info.st_mode) || info.st_size <= 0 ||
        (uint64_t)info.st_size > REHAB_AUDIO_PROMPT_MAX_BYTES) {
        log_failure(entry, "size_check", ESP_ERR_INVALID_SIZE, 0,
                    info.st_size > 0 ? (size_t)info.st_size : 0U);
        return;
    }

    size_t size = (size_t)info.st_size;
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        log_failure(entry, "open", ESP_FAIL, errno, size);
        return;
    }
    uint8_t *data = heap_caps_malloc(size,
                                     MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (data == NULL) {
        fclose(file);
        log_failure(entry, "psram_alloc", ESP_ERR_NO_MEM, 0, size);
        return;
    }
    errno = 0;
    size_t read_bytes = fread(data, 1U, size, file);
    int read_errno = errno;
    bool close_ok = fclose(file) == 0;
    if (read_bytes != size || !close_ok) {
        heap_caps_free(data);
        log_failure(entry, read_bytes != size ? "read" : "close", ESP_FAIL,
                    read_errno != 0 ? read_errno : errno, read_bytes);
        return;
    }

    entry->data = data;
    entry->size = size;
    entry->available = true;
    ESP_LOGI(TAG,
             "BUSINESS_PROMPT_PRELOAD_OK prompt=%d filename=%s bytes=%u "
             "memory=psram available=1 media_type=%s",
             entry->prompt, entry->filename, (unsigned)entry->size,
             entry->media_type);
}

void rehab_audio_prompt_store_preload(rehab_audio_prompt_store_t *store)
{
    if (store == NULL) {
        return;
    }
    initialize_entries(store);
    size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    int64_t started_us = esp_timer_get_time();
    ESP_LOGI(TAG,
             "BUSINESS_PROMPT_PRELOAD_BEGIN count=%u psram_free_before=%u",
             (unsigned)REHAB_AUDIO_PROMPT_COUNT, (unsigned)psram_before);

    for (size_t i = 0; i < REHAB_AUDIO_PROMPT_COUNT; ++i) {
        preload_entry(&store->entries[i]);
        if (store->entries[i].available) {
            ++store->available_count;
            store->total_bytes += store->entries[i].size;
        }
    }

    size_t psram_after = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    size_t psram_delta =
        psram_before >= psram_after ? psram_before - psram_after : 0U;
    uint64_t elapsed_ms =
        (uint64_t)(esp_timer_get_time() - started_us) / 1000U;
    ESP_LOGI(TAG,
             "BUSINESS_PROMPT_PRELOAD_SUMMARY available=%u total=%u "
             "total_bytes=%u psram_free_before=%u psram_free_after=%u "
             "psram_delta=%u elapsed_ms=%llu",
             (unsigned)store->available_count,
             (unsigned)REHAB_AUDIO_PROMPT_COUNT,
             (unsigned)store->total_bytes, (unsigned)psram_before,
             (unsigned)psram_after, (unsigned)psram_delta,
             (unsigned long long)elapsed_ms);
}

void rehab_audio_prompt_store_deinit(rehab_audio_prompt_store_t *store)
{
    if (store == NULL) {
        return;
    }
    for (size_t i = 0; i < REHAB_AUDIO_PROMPT_COUNT; ++i) {
        heap_caps_free(store->entries[i].data);
    }
    memset(store, 0, sizeof(*store));
}

bool rehab_audio_prompt_store_is_valid_prompt(
    rehab_audio_output_prompt_t prompt)
{
    for (size_t i = 0; i < REHAB_AUDIO_PROMPT_COUNT; ++i) {
        if (s_prompt_definitions[i].prompt == prompt) {
            return true;
        }
    }
    return false;
}

const rehab_audio_prompt_entry_t *rehab_audio_prompt_store_get(
    const rehab_audio_prompt_store_t *store,
    rehab_audio_output_prompt_t prompt)
{
    if (store == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < REHAB_AUDIO_PROMPT_COUNT; ++i) {
        if (store->entries[i].prompt == prompt) {
            return &store->entries[i];
        }
    }
    return NULL;
}
