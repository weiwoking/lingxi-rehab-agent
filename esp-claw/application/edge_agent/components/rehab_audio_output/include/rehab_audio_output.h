#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_AUDIO_OUTPUT_MAX_CODEPOINTS 300U
#define REHAB_AUDIO_OUTPUT_MAX_TEXT_BYTES (REHAB_AUDIO_OUTPUT_MAX_CODEPOINTS * 4U)
#define REHAB_AUDIO_OUTPUT_REQUEST_ID_LEN  96U

typedef struct rehab_audio_output_t *rehab_audio_output_handle_t;

typedef enum {
    REHAB_AUDIO_OUTPUT_IDLE = 0,
    REHAB_AUDIO_OUTPUT_REQUESTING_TTS,
    REHAB_AUDIO_OUTPUT_OPENING_STREAM,
    REHAB_AUDIO_OUTPUT_PLAYING,
    REHAB_AUDIO_OUTPUT_STOPPING,
    REHAB_AUDIO_OUTPUT_ERROR,
} rehab_audio_output_state_t;

typedef enum {
    REHAB_AUDIO_OUTPUT_PROMPT_PERSON_FOLLOW_STARTED = 0,
    REHAB_AUDIO_OUTPUT_PROMPT_PERSON_FOLLOW_ARRIVED,
    REHAB_AUDIO_OUTPUT_PROMPT_ENV_SCAN_STARTED,
    REHAB_AUDIO_OUTPUT_PROMPT_ENV_SCAN_COMPLETED,
    REHAB_AUDIO_OUTPUT_PROMPT_DECISION_STARTED,
    REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_CUP_TRANSFER_L1,
    REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_CUP_DRINK_L2,
    REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_CUP_DRINK_L3,
    REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_BALL_TRANSFER_L1,
    REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_BALL_TRANSFER_L2,
    REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_GENERIC,
    REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_COMPLETED,
} rehab_audio_output_prompt_t;

typedef struct {
    const char *tts_base_url;
    uint32_t tts_timeout_ms;
    uint32_t output_sample_rate;
    uint8_t output_channels;
    uint8_t output_bits;
    int output_volume;
    int output_codec_boost_db_x10;
    uint32_t worker_stack_size;
    unsigned worker_priority;
} rehab_audio_output_config_t;

#define REHAB_AUDIO_OUTPUT_CONFIG_DEFAULT() { \
    .tts_base_url = CONFIG_REHAB_AUDIO_TTS_BASE_URL, \
    .tts_timeout_ms = CONFIG_REHAB_AUDIO_TTS_TIMEOUT_MS, \
    .output_sample_rate = 48000U, \
    .output_channels = 1U, \
    .output_bits = 16U, \
    .output_volume = CONFIG_REHAB_AUDIO_OUTPUT_VOLUME, \
    .output_codec_boost_db_x10 = \
        CONFIG_REHAB_AUDIO_OUTPUT_CODEC_BOOST_DB_X10, \
    .worker_stack_size = 8192U, \
    .worker_priority = 5U, \
}

typedef struct {
    rehab_audio_output_state_t state;
    uint32_t generation;
    bool desired;
    char request_id[REHAB_AUDIO_OUTPUT_REQUEST_ID_LEN];
    esp_err_t last_error;
    char last_error_reason[96];
} rehab_audio_output_status_t;

esp_err_t rehab_audio_output_create(const rehab_audio_output_config_t *config,
                                    rehab_audio_output_handle_t *out_handle);
esp_err_t rehab_audio_output_delete(rehab_audio_output_handle_t handle);
esp_err_t rehab_audio_output_speak_text(rehab_audio_output_handle_t handle,
                                        const char *text,
                                        const char *request_id,
                                        uint32_t *out_generation);
esp_err_t rehab_audio_output_play_prompt(
    rehab_audio_output_handle_t handle,
    rehab_audio_output_prompt_t prompt,
    const char *request_id,
    uint32_t *out_generation);
esp_err_t rehab_audio_output_stop(rehab_audio_output_handle_t handle,
                                  uint32_t *out_generation);
esp_err_t rehab_audio_output_get_status(rehab_audio_output_handle_t handle,
                                        rehab_audio_output_status_t *out_status);
const char *rehab_audio_output_state_name(rehab_audio_output_state_t state);

esp_err_t rehab_audio_output_register_capabilities(
    rehab_audio_output_handle_t handle);
esp_err_t rehab_audio_output_provision_router_rules(void);

#ifdef __cplusplus
}
#endif
