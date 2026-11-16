#pragma once

#include "esp_err.h"
#include "rehab_audio_output.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    REHAB_BUSINESS_AUDIO_PERSON_FOLLOW_STARTED = 0,
    REHAB_BUSINESS_AUDIO_PERSON_FOLLOW_ARRIVED,
    REHAB_BUSINESS_AUDIO_ENV_SCAN_STARTED,
    REHAB_BUSINESS_AUDIO_ENV_SCAN_COMPLETED,
    REHAB_BUSINESS_AUDIO_DECISION_STARTED,
    REHAB_BUSINESS_AUDIO_TRAINING_COMPLETED,
} rehab_business_audio_event_t;

/* Optional boot-time registration. Business operations remain valid without it. */
esp_err_t rehab_business_audio_register_output(
    rehab_audio_output_handle_t output);

/* Best-effort, non-waiting submission to the existing latest-wins audio worker. */
esp_err_t rehab_business_audio_notify(rehab_business_audio_event_t event);

/* Formal task IDs have fixed prompts; unknown/legacy IDs use one generic prompt. */
esp_err_t rehab_business_audio_training_started(const char *task_id);

#ifdef __cplusplus
}
#endif
