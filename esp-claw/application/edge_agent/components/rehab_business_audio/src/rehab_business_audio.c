#include "rehab_business_audio.h"

#include <string.h>

#include "esp_log.h"

static const char *TAG = "rehab_business_audio";
static rehab_audio_output_handle_t s_output;

typedef struct {
    rehab_business_audio_event_t event;
    rehab_audio_output_prompt_t prompt;
    const char *request_id;
} event_prompt_t;

static const event_prompt_t s_events[] = {
    {REHAB_BUSINESS_AUDIO_PERSON_FOLLOW_STARTED,
     REHAB_AUDIO_OUTPUT_PROMPT_PERSON_FOLLOW_STARTED,
     "business-person-follow-started"},
    {REHAB_BUSINESS_AUDIO_PERSON_FOLLOW_ARRIVED,
     REHAB_AUDIO_OUTPUT_PROMPT_PERSON_FOLLOW_ARRIVED,
     "business-person-follow-arrived"},
    {REHAB_BUSINESS_AUDIO_ENV_SCAN_STARTED,
     REHAB_AUDIO_OUTPUT_PROMPT_ENV_SCAN_STARTED,
     "business-env-scan-started"},
    {REHAB_BUSINESS_AUDIO_ENV_SCAN_COMPLETED,
     REHAB_AUDIO_OUTPUT_PROMPT_ENV_SCAN_COMPLETED,
     "business-env-scan-completed"},
    {REHAB_BUSINESS_AUDIO_DECISION_STARTED,
     REHAB_AUDIO_OUTPUT_PROMPT_DECISION_STARTED,
     "business-decision-started"},
    {REHAB_BUSINESS_AUDIO_TRAINING_COMPLETED,
     REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_COMPLETED,
     "business-training-completed"},
};

typedef struct {
    const char *task_id;
    rehab_audio_output_prompt_t prompt;
} task_prompt_t;

static const task_prompt_t s_task_prompts[] = {
    {"OT_CUP_TRANSFER_L1",
     REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_CUP_TRANSFER_L1},
    {"OT_CUP_DRINK_L2",
     REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_CUP_DRINK_L2},
    {"OT_CUP_DRINK_L3",
     REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_CUP_DRINK_L3},
    {"OT_BALL_TRANSFER_L1",
     REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_BALL_TRANSFER_L1},
    {"OT_BALL_TRANSFER_L2",
     REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_OT_BALL_TRANSFER_L2},
};

static esp_err_t submit(rehab_audio_output_prompt_t prompt,
                        const char *request_id)
{
    if (s_output == NULL) {
        ESP_LOGW(TAG, "prompt skipped: audio output unavailable request_id=%s",
                 request_id);
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = rehab_audio_output_play_prompt(
        s_output, prompt, request_id, NULL);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "prompt submit failed request_id=%s err=%s",
                 request_id, esp_err_to_name(err));
    }
    return err;
}

esp_err_t rehab_business_audio_register_output(
    rehab_audio_output_handle_t output)
{
    if (output == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    s_output = output;
    return ESP_OK;
}

esp_err_t rehab_business_audio_notify(rehab_business_audio_event_t event)
{
    for (size_t i = 0; i < sizeof(s_events) / sizeof(s_events[0]); ++i) {
        if (s_events[i].event == event) {
            return submit(s_events[i].prompt, s_events[i].request_id);
        }
    }
    return ESP_ERR_INVALID_ARG;
}

esp_err_t rehab_business_audio_training_started(const char *task_id)
{
    if (task_id != NULL) {
        for (size_t i = 0;
             i < sizeof(s_task_prompts) / sizeof(s_task_prompts[0]); ++i) {
            if (strcmp(task_id, s_task_prompts[i].task_id) == 0) {
                return submit(s_task_prompts[i].prompt,
                              "business-training-started");
            }
        }
    }
    return submit(REHAB_AUDIO_OUTPUT_PROMPT_TRAINING_GENERIC,
                  "business-training-started-generic");
}
