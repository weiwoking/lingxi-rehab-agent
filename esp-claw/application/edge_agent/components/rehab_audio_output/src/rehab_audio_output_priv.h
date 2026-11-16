#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "rehab_audio_prompt_store.h"
#include "rehab_audio_output.h"

#define REHAB_AUDIO_TTS_URL_LEN       320U
#define REHAB_AUDIO_ERROR_REASON_LEN   96U
typedef enum {
    REHAB_AUDIO_COMMAND_TEXT = 0,
    REHAB_AUDIO_COMMAND_PROMPT,
    REHAB_AUDIO_COMMAND_STOP,
} rehab_audio_command_type_t;

typedef enum {
    REHAB_AUDIO_PLAYER_STARTED = 0,
    REHAB_AUDIO_PLAYER_FINISHED,
    REHAB_AUDIO_PLAYER_STOPPED,
    REHAB_AUDIO_PLAYER_ERROR,
} rehab_audio_player_event_t;

typedef void (*rehab_audio_player_event_cb_t)(
    uint32_t generation,
    rehab_audio_player_event_t event,
    void *user_ctx);

typedef struct rehab_audio_player_adapter_t *rehab_audio_player_adapter_handle_t;

typedef struct {
    rehab_audio_command_type_t type;
    union {
        const char *text;
        rehab_audio_output_prompt_t prompt;
    } payload;
    char request_id[REHAB_AUDIO_OUTPUT_REQUEST_ID_LEN];
    uint32_t generation;
} rehab_audio_command_t;

_Static_assert(sizeof(rehab_audio_command_t) <= 128U,
               "audio command metadata must remain stack-light");

typedef struct {
    char audio_url[REHAB_AUDIO_TTS_URL_LEN];
    char mime_type[32];
} rehab_audio_tts_result_t;

struct rehab_audio_output_t {
    /* mutex protects generation/status; submit_mutex orders public commands. */
    SemaphoreHandle_t mutex;
    SemaphoreHandle_t submit_mutex;
    QueueHandle_t command_queue;
    TaskHandle_t worker_task;
    rehab_audio_player_adapter_handle_t player;
    /* submitters own pending; only audio_worker owns active and tts_result. */
    rehab_audio_command_t pending_command;
    rehab_audio_command_t active_command;
    char pending_text[REHAB_AUDIO_OUTPUT_MAX_TEXT_BYTES + 1U];
    char active_text[REHAB_AUDIO_OUTPUT_MAX_TEXT_BYTES + 1U];
    rehab_audio_tts_result_t tts_result;
    rehab_audio_prompt_store_t prompt_store;
    char tts_base_url[REHAB_AUDIO_TTS_URL_LEN];
    uint32_t tts_timeout_ms;
    rehab_audio_output_state_t state;
    uint32_t generation;
    bool desired;
    bool deleting;
    char request_id[REHAB_AUDIO_OUTPUT_REQUEST_ID_LEN];
    esp_err_t last_error;
    char last_error_reason[REHAB_AUDIO_ERROR_REASON_LEN];
};

esp_err_t rehab_audio_tts_synthesize(const char *base_url,
                                     uint32_t timeout_ms,
                                     const char *text,
                                     const char *request_id,
                                     rehab_audio_tts_result_t *out_result);

esp_err_t rehab_audio_player_adapter_create(
    const rehab_audio_output_config_t *config,
    rehab_audio_player_event_cb_t event_cb,
    void *user_ctx,
    rehab_audio_player_adapter_handle_t *out_handle);
esp_err_t rehab_audio_player_adapter_play(
    rehab_audio_player_adapter_handle_t handle,
    const char *uri,
    uint32_t generation);
esp_err_t rehab_audio_player_adapter_play_memory(
    rehab_audio_player_adapter_handle_t handle,
    const uint8_t *data,
    size_t size,
    const char *filename,
    uint32_t generation);
esp_err_t rehab_audio_player_adapter_stop(
    rehab_audio_player_adapter_handle_t handle);
esp_err_t rehab_audio_player_adapter_delete(
    rehab_audio_player_adapter_handle_t handle);
