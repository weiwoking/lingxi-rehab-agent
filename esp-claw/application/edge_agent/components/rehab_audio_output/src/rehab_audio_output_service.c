#include "rehab_audio_output_priv.h"

#include <stdlib.h>
#include <string.h>

#include "esp_check.h"
#include "esp_log.h"

static const char *TAG = "rehab_audio";

static esp_err_t count_utf8_codepoints(const char *text, size_t *out_count)
{
    const unsigned char *cursor = (const unsigned char *)text;
    size_t count = 0;

    if (!text || !out_count) {
        return ESP_ERR_INVALID_ARG;
    }
    while (*cursor) {
        size_t width;
        if (*cursor < 0x80U) {
            width = 1;
        } else if ((*cursor & 0xE0U) == 0xC0U && *cursor >= 0xC2U) {
            width = 2;
        } else if ((*cursor & 0xF0U) == 0xE0U) {
            width = 3;
        } else if ((*cursor & 0xF8U) == 0xF0U && *cursor <= 0xF4U) {
            width = 4;
        } else {
            return ESP_ERR_INVALID_ARG;
        }
        for (size_t i = 1; i < width; i++) {
            if ((cursor[i] & 0xC0U) != 0x80U) {
                return ESP_ERR_INVALID_ARG;
            }
        }
        if ((width == 3 && cursor[0] == 0xE0U && cursor[1] < 0xA0U) ||
            (width == 3 && cursor[0] == 0xEDU && cursor[1] >= 0xA0U) ||
            (width == 4 && cursor[0] == 0xF0U && cursor[1] < 0x90U) ||
            (width == 4 && cursor[0] == 0xF4U && cursor[1] >= 0x90U)) {
            return ESP_ERR_INVALID_ARG;
        }
        cursor += width;
        count++;
    }
    *out_count = count;
    return ESP_OK;
}

static bool text_is_all_whitespace(const char *text)
{
    const unsigned char *cursor = (const unsigned char *)text;
    while (*cursor) {
        if (*cursor >= 0x80U ||
            (*cursor != ' ' && *cursor != '\t' && *cursor != '\r' &&
             *cursor != '\n' && *cursor != '\f' && *cursor != '\v')) {
            return false;
        }
        cursor++;
    }
    return true;
}

static void set_error_locked(rehab_audio_output_handle_t handle,
                             esp_err_t error,
                             const char *reason)
{
    handle->last_error = error;
    strlcpy(handle->last_error_reason, reason ? reason : "",
            sizeof(handle->last_error_reason));
}

static bool generation_is_current_locked(rehab_audio_output_handle_t handle,
                                         uint32_t generation)
{
    return handle->desired && handle->generation == generation;
}

static bool generation_matches_locked(rehab_audio_output_handle_t handle,
                                      uint32_t generation)
{
    return handle->generation == generation;
}

static esp_err_t submit_command(rehab_audio_output_handle_t handle,
                                rehab_audio_command_type_t type,
                                const char *text,
                                rehab_audio_output_prompt_t prompt,
                                const char *request_id,
                                rehab_audio_output_state_t accepted_state,
                                uint32_t *out_generation)
{
    const char *effective_request_id =
        request_id && request_id[0]
            ? request_id
            : (type == REHAB_AUDIO_COMMAND_TEXT
                   ? "audio-request"
                   : (type == REHAB_AUDIO_COMMAND_PROMPT ? "audio-prompt"
                                                         : "audio-stop"));

    xSemaphoreTake(handle->submit_mutex, portMAX_DELAY);
    rehab_audio_command_t *command = &handle->pending_command;
    command->type = type;
    if (type == REHAB_AUDIO_COMMAND_TEXT) {
        strlcpy(handle->pending_text, text, sizeof(handle->pending_text));
        command->payload.text = handle->pending_text;
    } else if (type == REHAB_AUDIO_COMMAND_PROMPT) {
        command->payload.prompt = prompt;
    }
    strlcpy(command->request_id, effective_request_id,
            sizeof(command->request_id));

    xSemaphoreTake(handle->mutex, portMAX_DELAY);
    command->generation = ++handle->generation;
    handle->desired = type != REHAB_AUDIO_COMMAND_STOP;
    handle->state = accepted_state;
    handle->last_error = ESP_OK;
    handle->last_error_reason[0] = '\0';
    strlcpy(handle->request_id, command->request_id,
            sizeof(handle->request_id));
    xSemaphoreGive(handle->mutex);

    uint32_t generation = command->generation;
    if (xQueueOverwrite(handle->command_queue, &generation) != pdPASS) {
        xSemaphoreTake(handle->mutex, portMAX_DELAY);
        if (handle->generation == generation) {
            handle->desired = false;
            handle->state = REHAB_AUDIO_OUTPUT_IDLE;
            set_error_locked(handle, ESP_FAIL, "command_queue_failed");
        }
        xSemaphoreGive(handle->mutex);
        xSemaphoreGive(handle->submit_mutex);
        return ESP_FAIL;
    }
    if (out_generation) {
        *out_generation = generation;
    }
    xSemaphoreGive(handle->submit_mutex);
    return ESP_OK;
}

static void player_event(uint32_t generation,
                         rehab_audio_player_event_t event,
                         void *user_ctx)
{
    rehab_audio_output_handle_t handle = user_ctx;
    if (!handle || !handle->mutex) {
        return;
    }

    xSemaphoreTake(handle->mutex, portMAX_DELAY);
    if (!generation_is_current_locked(handle, generation)) {
        xSemaphoreGive(handle->mutex);
        ESP_LOGD(TAG, "discarded stale player event generation=%lu event=%d",
                 (unsigned long)generation, event);
        return;
    }
    switch (event) {
    case REHAB_AUDIO_PLAYER_STARTED:
        handle->state = REHAB_AUDIO_OUTPUT_PLAYING;
        ESP_LOGI(TAG, "playback start generation=%lu request_id=%s",
                 (unsigned long)generation, handle->request_id);
        break;
    case REHAB_AUDIO_PLAYER_FINISHED:
    case REHAB_AUDIO_PLAYER_STOPPED:
        handle->state = REHAB_AUDIO_OUTPUT_IDLE;
        handle->desired = false;
        ESP_LOGI(TAG, "playback end generation=%lu request_id=%s",
                 (unsigned long)generation, handle->request_id);
        break;
    case REHAB_AUDIO_PLAYER_ERROR:
    default:
        handle->state = REHAB_AUDIO_OUTPUT_ERROR;
        handle->desired = false;
        set_error_locked(handle, ESP_FAIL, "player_error");
        ESP_LOGE(TAG, "playback error generation=%lu request_id=%s",
                 (unsigned long)generation, handle->request_id);
        break;
    }
    xSemaphoreGive(handle->mutex);
}

static void audio_worker(void *arg)
{
    rehab_audio_output_handle_t handle = arg;
    uint32_t queued_generation;

    while (!handle->deleting) {
        if (xQueueReceive(handle->command_queue, &queued_generation,
                          pdMS_TO_TICKS(250)) != pdTRUE) {
            continue;
        }

        xSemaphoreTake(handle->submit_mutex, portMAX_DELAY);
        if (handle->pending_command.generation != queued_generation) {
            xSemaphoreGive(handle->submit_mutex);
            continue;
        }
        handle->active_command = handle->pending_command;
        if (handle->active_command.type == REHAB_AUDIO_COMMAND_TEXT) {
            strlcpy(handle->active_text, handle->pending_text,
                    sizeof(handle->active_text));
            handle->active_command.payload.text = handle->active_text;
        }
        xSemaphoreGive(handle->submit_mutex);
        rehab_audio_command_t *command = &handle->active_command;

        xSemaphoreTake(handle->mutex, portMAX_DELAY);
        bool current = command->type == REHAB_AUDIO_COMMAND_STOP
                           ? generation_matches_locked(handle,
                                                       command->generation)
                           : generation_is_current_locked(handle,
                                                          command->generation);
        xSemaphoreGive(handle->mutex);
        if (!current) {
            continue;
        }

        /* Playback replacement belongs to the worker, never the submitter. */
        esp_err_t err = rehab_audio_player_adapter_stop(handle->player);
        if (command->type == REHAB_AUDIO_COMMAND_STOP) {
            xSemaphoreTake(handle->mutex, portMAX_DELAY);
            if (generation_matches_locked(handle, command->generation)) {
                handle->state = REHAB_AUDIO_OUTPUT_IDLE;
                handle->desired = false;
                if (err != ESP_OK) {
                    set_error_locked(handle, err, "stop_failed");
                }
            }
            xSemaphoreGive(handle->mutex);
            ESP_LOGI(TAG, "stop processed generation=%lu err=%s",
                     (unsigned long)command->generation,
                     esp_err_to_name(err));
            continue;
        }

        rehab_audio_tts_result_t *result = &handle->tts_result;
        memset(result, 0, sizeof(*result));
        const char *audio_uri = NULL;
        const rehab_audio_prompt_entry_t *prompt_entry = NULL;
        err = ESP_OK;
        if (command->type == REHAB_AUDIO_COMMAND_TEXT) {
            err = rehab_audio_tts_synthesize(
                handle->tts_base_url, handle->tts_timeout_ms,
                command->payload.text, command->request_id, result);
            audio_uri = result->audio_url;
        } else if (command->type == REHAB_AUDIO_COMMAND_PROMPT) {
            prompt_entry = rehab_audio_prompt_store_get(
                &handle->prompt_store, command->payload.prompt);
            if (prompt_entry == NULL || !prompt_entry->available) {
                err = ESP_ERR_NOT_FOUND;
            }
        } else {
            err = ESP_ERR_INVALID_ARG;
        }

        xSemaphoreTake(handle->mutex, portMAX_DELAY);
        current = generation_is_current_locked(handle, command->generation);
        if (!current) {
            xSemaphoreGive(handle->mutex);
            ESP_LOGI(TAG, "discarded stale audio command generation=%lu",
                     (unsigned long)command->generation);
            continue;
        }
        if (err != ESP_OK) {
            handle->state = REHAB_AUDIO_OUTPUT_IDLE;
            handle->desired = false;
            const char *reason = command->type == REHAB_AUDIO_COMMAND_TEXT
                                     ? "tts_service_unavailable"
                                     : "prompt_not_available";
            set_error_locked(handle, err, reason);
            xSemaphoreGive(handle->mutex);
            ESP_LOGE(TAG,
                     "audio source failed generation=%lu request_id=%s "
                     "type=%d err=%s",
                     (unsigned long)command->generation, command->request_id,
                     command->type, esp_err_to_name(err));
            continue;
        }
        handle->state = REHAB_AUDIO_OUTPUT_OPENING_STREAM;
        xSemaphoreGive(handle->mutex);

        if (command->type == REHAB_AUDIO_COMMAND_PROMPT) {
            ESP_LOGI(TAG,
                     "P4 source open generation=%lu request_id=%s "
                     "source=memory/raw memory=psram filename=%s bytes=%u",
                     (unsigned long)command->generation, command->request_id,
                     prompt_entry->filename, (unsigned)prompt_entry->size);
            err = rehab_audio_player_adapter_play_memory(
                handle->player, prompt_entry->data, prompt_entry->size,
                prompt_entry->filename, command->generation);
        } else {
            ESP_LOGI(TAG,
                     "P4 source open generation=%lu request_id=%s "
                     "source=text/http",
                     (unsigned long)command->generation,
                     command->request_id);
            err = rehab_audio_player_adapter_play(
                handle->player, audio_uri, command->generation);
        }

        xSemaphoreTake(handle->mutex, portMAX_DELAY);
        current = generation_is_current_locked(handle, command->generation);
        if (err != ESP_OK && current) {
            handle->state = REHAB_AUDIO_OUTPUT_IDLE;
            handle->desired = false;
            set_error_locked(handle, err, "stream_open_failed");
        }
        xSemaphoreGive(handle->mutex);
        if (err == ESP_OK && !current) {
            ESP_LOGI(TAG,
                     "stopping source opened by stale command generation=%lu",
                     (unsigned long)command->generation);
            rehab_audio_player_adapter_stop(handle->player);
        }
    }
    handle->worker_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t rehab_audio_output_create(const rehab_audio_output_config_t *config,
                                    rehab_audio_output_handle_t *out_handle)
{
    rehab_audio_output_handle_t handle = NULL;
    BaseType_t task_ok;

    ESP_RETURN_ON_FALSE(config && out_handle && config->tts_base_url &&
                            config->tts_base_url[0],
                        ESP_ERR_INVALID_ARG, TAG, "invalid create arguments");
    ESP_RETURN_ON_FALSE(config->output_volume >= 0 &&
                            config->output_volume <= 100,
                        ESP_ERR_INVALID_ARG, TAG, "invalid output volume");

    handle = calloc(1, sizeof(*handle));
    ESP_RETURN_ON_FALSE(handle, ESP_ERR_NO_MEM, TAG, "allocation failed");
    handle->mutex = xSemaphoreCreateMutex();
    handle->submit_mutex = xSemaphoreCreateMutex();
    handle->command_queue = xQueueCreate(1, sizeof(uint32_t));
    if (!handle->mutex || !handle->submit_mutex || !handle->command_queue ||
        strlcpy(handle->tts_base_url, config->tts_base_url,
                sizeof(handle->tts_base_url)) >= sizeof(handle->tts_base_url)) {
        rehab_audio_output_delete(handle);
        return ESP_ERR_NO_MEM;
    }
    size_t base_url_length = strlen(handle->tts_base_url);
    while (base_url_length > 7U &&
           handle->tts_base_url[base_url_length - 1U] == '/') {
        handle->tts_base_url[--base_url_length] = '\0';
    }
    handle->tts_timeout_ms = config->tts_timeout_ms;
    handle->state = REHAB_AUDIO_OUTPUT_IDLE;
    rehab_audio_prompt_store_preload(&handle->prompt_store);

    esp_err_t err = rehab_audio_player_adapter_create(
        config, player_event, handle, &handle->player);
    if (err != ESP_OK) {
        rehab_audio_output_delete(handle);
        return err;
    }

    task_ok = xTaskCreate(audio_worker, "rehab_audio",
                          config->worker_stack_size, handle,
                          config->worker_priority, &handle->worker_task);
    if (task_ok != pdPASS) {
        rehab_audio_output_delete(handle);
        return ESP_ERR_NO_MEM;
    }
    *out_handle = handle;
    return ESP_OK;
}

esp_err_t rehab_audio_output_delete(rehab_audio_output_handle_t handle)
{
    if (!handle) {
        return ESP_OK;
    }
    handle->deleting = true;
    if (handle->worker_task) {
        TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(1500);
        while (handle->worker_task && xTaskGetTickCount() < deadline) {
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        if (handle->worker_task) {
            vTaskDelete(handle->worker_task);
            handle->worker_task = NULL;
        }
    }
    rehab_audio_player_adapter_delete(handle->player);
    rehab_audio_prompt_store_deinit(&handle->prompt_store);
    if (handle->command_queue) {
        vQueueDelete(handle->command_queue);
    }
    if (handle->submit_mutex) {
        vSemaphoreDelete(handle->submit_mutex);
    }
    if (handle->mutex) {
        vSemaphoreDelete(handle->mutex);
    }
    free(handle);
    return ESP_OK;
}

esp_err_t rehab_audio_output_speak_text(rehab_audio_output_handle_t handle,
                                        const char *text,
                                        const char *request_id,
                                        uint32_t *out_generation)
{
    size_t codepoints = 0;
    size_t bytes;
    uint32_t generation;

    ESP_RETURN_ON_FALSE(handle && text, ESP_ERR_INVALID_ARG, TAG,
                        "invalid speak arguments");
    bytes = strlen(text);
    ESP_RETURN_ON_FALSE(bytes > 0 && !text_is_all_whitespace(text),
                        ESP_ERR_INVALID_ARG, TAG, "text is empty");
    ESP_RETURN_ON_ERROR(count_utf8_codepoints(text, &codepoints), TAG,
                        "text is not valid UTF-8");
    ESP_RETURN_ON_FALSE(codepoints <= REHAB_AUDIO_OUTPUT_MAX_CODEPOINTS &&
                            bytes <= REHAB_AUDIO_OUTPUT_MAX_TEXT_BYTES,
                        ESP_ERR_INVALID_SIZE, TAG, "text is too long");

    esp_err_t err = submit_command(
        handle, REHAB_AUDIO_COMMAND_TEXT, text,
        REHAB_AUDIO_OUTPUT_PROMPT_PERSON_FOLLOW_STARTED, request_id,
        REHAB_AUDIO_OUTPUT_REQUESTING_TTS, &generation);
    if (err != ESP_OK) {
        return err;
    }
    if (out_generation) {
        *out_generation = generation;
    }
    ESP_LOGI(TAG,
             "request accepted generation=%lu request_id=%s codepoints=%u",
             (unsigned long)generation,
             request_id && request_id[0] ? request_id : "audio-request",
             (unsigned)codepoints);
    return ESP_OK;
}

esp_err_t rehab_audio_output_play_prompt(
    rehab_audio_output_handle_t handle,
    rehab_audio_output_prompt_t prompt,
    const char *request_id,
    uint32_t *out_generation)
{
    uint32_t generation;

    if (!handle || !rehab_audio_prompt_store_is_valid_prompt(prompt)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = submit_command(
        handle, REHAB_AUDIO_COMMAND_PROMPT, NULL, prompt, request_id,
        REHAB_AUDIO_OUTPUT_OPENING_STREAM, &generation);
    if (err != ESP_OK) {
        return err;
    }
    if (out_generation) {
        *out_generation = generation;
    }
    ESP_LOGI(TAG,
             "prompt accepted generation=%lu request_id=%s prompt=%d",
             (unsigned long)generation,
             request_id && request_id[0] ? request_id : "audio-prompt",
             prompt);
    return ESP_OK;
}

esp_err_t rehab_audio_output_stop(rehab_audio_output_handle_t handle,
                                  uint32_t *out_generation)
{
    ESP_RETURN_ON_FALSE(handle, ESP_ERR_INVALID_ARG, TAG, "handle is NULL");
    return submit_command(
        handle, REHAB_AUDIO_COMMAND_STOP, NULL,
        REHAB_AUDIO_OUTPUT_PROMPT_PERSON_FOLLOW_STARTED, "audio-stop",
        REHAB_AUDIO_OUTPUT_STOPPING, out_generation);
}

esp_err_t rehab_audio_output_get_status(rehab_audio_output_handle_t handle,
                                        rehab_audio_output_status_t *out_status)
{
    ESP_RETURN_ON_FALSE(handle && out_status, ESP_ERR_INVALID_ARG, TAG,
                        "invalid status arguments");
    xSemaphoreTake(handle->mutex, portMAX_DELAY);
    out_status->state = handle->state;
    out_status->generation = handle->generation;
    out_status->desired = handle->desired;
    out_status->last_error = handle->last_error;
    strlcpy(out_status->request_id, handle->request_id,
            sizeof(out_status->request_id));
    strlcpy(out_status->last_error_reason, handle->last_error_reason,
            sizeof(out_status->last_error_reason));
    xSemaphoreGive(handle->mutex);
    return ESP_OK;
}

const char *rehab_audio_output_state_name(rehab_audio_output_state_t state)
{
    switch (state) {
    case REHAB_AUDIO_OUTPUT_REQUESTING_TTS: return "REQUESTING_TTS";
    case REHAB_AUDIO_OUTPUT_OPENING_STREAM: return "OPENING_STREAM";
    case REHAB_AUDIO_OUTPUT_PLAYING: return "PLAYING";
    case REHAB_AUDIO_OUTPUT_STOPPING: return "STOPPING";
    case REHAB_AUDIO_OUTPUT_ERROR: return "ERROR";
    case REHAB_AUDIO_OUTPUT_IDLE:
    default:
        return "IDLE";
    }
}
