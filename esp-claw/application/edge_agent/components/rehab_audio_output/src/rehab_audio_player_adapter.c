#include "rehab_audio_output_priv.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dev_audio_codec.h"
#include "esp_asrc.h"
#include "esp_audio_simple_player.h"
#include "esp_board_manager.h"
#include "esp_board_manager_defs.h"
#include "esp_codec_dev.h"
#include "esp_log.h"
#include "rehab_audio_memory_source.h"

#define ASRC_COMPLEXITY  3
#define ASRC_TIMEOUT_MS  1000
#define CODEC_BOOST_DB_X10_MAX  20
#define CODEC_DB_X10_SCALE      10.0f
#define VOLUME_CURVE_MIN_DB     -50.0f
#define VOLUME_CURVE_MIN_LEVEL  0
#define VOLUME_CURVE_MAX_LEVEL  100

static const char *TAG = "rehab_audio_player";

static bool source_uri_is_allowed(const char *uri)
{
    return uri != NULL && strncmp(uri, "http://", 7) == 0;
}

static bool memory_filename_is_allowed(const char *filename)
{
    if (filename == NULL || filename[0] == '\0' ||
        strchr(filename, '/') != NULL || strchr(filename, '\\') != NULL ||
        strstr(filename, "..") != NULL) {
        return false;
    }
    size_t length = strlen(filename);
    return length > 4U && strcmp(filename + length - 4U, ".mp3") == 0;
}

typedef struct {
    uint32_t sample_rate;
    uint8_t channels;
    uint8_t bits;
    uint8_t bytes_per_frame;
} audio_format_t;

typedef struct {
    bool bypass;
    esp_asrc_handle_t handle;
    uint16_t in_frame_bytes;
    uint16_t out_frame_bytes;
    uint8_t *in_buf;
    uint32_t in_buf_size;
    uint8_t *out_buf;
    uint32_t out_buf_size;
    esp_asrc_buffer_alignment_t align;
} audio_converter_t;

struct rehab_audio_player_adapter_t;

typedef struct {
    struct rehab_audio_player_adapter_t *owner;
    esp_asp_handle_t player;
    audio_converter_t converter;
    uint32_t generation;
    bool converter_ready;
    bool closing;
    bool started_sent;
    bool terminal_sent;
    rehab_audio_memory_cursor_t memory_cursor;
} player_run_t;

struct rehab_audio_player_adapter_t {
    SemaphoreHandle_t mutex;
    esp_codec_dev_handle_t codec;
    audio_format_t output_format;
    int volume;
    int codec_boost_db_x10;
    player_run_t *current;
    rehab_audio_player_event_cb_t event_cb;
    void *event_user_ctx;
};

static int configure_output_volume(
    rehab_audio_player_adapter_handle_t handle)
{
    esp_codec_dev_vol_map_t volume_map[] = {
        {
            .vol = VOLUME_CURVE_MIN_LEVEL,
            .db_value = VOLUME_CURVE_MIN_DB,
        },
        {
            .vol = VOLUME_CURVE_MAX_LEVEL,
            .db_value =
                (float)handle->codec_boost_db_x10 / CODEC_DB_X10_SCALE,
        },
    };
    esp_codec_dev_vol_curve_t volume_curve = {
        .vol_map = volume_map,
        .count = (int)(sizeof(volume_map) / sizeof(volume_map[0])),
    };
    int result = esp_codec_dev_set_vol_curve(handle->codec, &volume_curve);
    if (result != ESP_CODEC_DEV_OK) {
        return result;
    }
    return esp_codec_dev_set_out_vol(handle->codec, handle->volume);
}

static void converter_destroy(audio_converter_t *converter)
{
    if (converter->handle) {
        esp_asrc_close(converter->handle);
    }
    free(converter->in_buf);
    free(converter->out_buf);
    memset(converter, 0, sizeof(*converter));
}

static esp_err_t format_complete(audio_format_t *format)
{
    if (!format || !format->sample_rate || !format->channels ||
        (format->bits != 16U && format->bits != 24U &&
         format->bits != 32U)) {
        return ESP_ERR_INVALID_ARG;
    }
    format->bytes_per_frame =
        (uint8_t)(format->channels * ((format->bits + 7U) / 8U));
    return format->bytes_per_frame ? ESP_OK : ESP_ERR_INVALID_ARG;
}

static esp_err_t converter_create(audio_converter_t *converter,
                                  const audio_format_t *source,
                                  const audio_format_t *destination)
{
    converter_destroy(converter);
    converter->in_frame_bytes = source->bytes_per_frame;
    converter->out_frame_bytes = destination->bytes_per_frame;
    if (source->sample_rate == destination->sample_rate &&
        source->channels == destination->channels &&
        source->bits == destination->bits) {
        converter->bypass = true;
        return ESP_OK;
    }

    esp_asrc_cfg_t config = {
        .src_info = {
            .sample_rate = source->sample_rate,
            .channel = source->channels,
            .bits_per_sample = source->bits,
        },
        .dest_info = {
            .sample_rate = destination->sample_rate,
            .channel = destination->channels,
            .bits_per_sample = destination->bits,
        },
        .weight = NULL,
        .weight_len = 0,
        .perf_type = ESP_ASRC_PERF_TYPE_AUTO,
        .complexity = ASRC_COMPLEXITY,
        .timeout_ms = ASRC_TIMEOUT_MS,
    };
    if (esp_asrc_open(&config, &converter->handle) != ESP_ASRC_ERR_OK ||
        esp_asrc_get_buffer_alignment(&converter->align) != ESP_ASRC_ERR_OK ||
        esp_asrc_get_bytes_per_sample(converter->handle,
                                      &converter->in_frame_bytes,
                                      &converter->out_frame_bytes) !=
            ESP_ASRC_ERR_OK) {
        converter_destroy(converter);
        return ESP_FAIL;
    }
    return ESP_OK;
}

static esp_err_t ensure_buffer(uint8_t **buffer,
                               uint32_t *current_size,
                               uint32_t requested,
                               uint8_t address_alignment,
                               uint8_t size_alignment)
{
    if (*current_size >= requested) {
        return ESP_OK;
    }
    uint32_t allocated = 0;
    uint8_t *replacement = esp_asrc_align_alloc(
        requested, address_alignment, size_alignment, &allocated);
    if (!replacement) {
        return ESP_ERR_NO_MEM;
    }
    free(*buffer);
    *buffer = replacement;
    *current_size = allocated;
    return ESP_OK;
}

static esp_err_t converter_process(audio_converter_t *converter,
                                   const uint8_t *input,
                                   uint32_t input_bytes,
                                   uint8_t **output,
                                   uint32_t *output_bytes)
{
    if (converter->bypass) {
        *output = (uint8_t *)input;
        *output_bytes = input_bytes;
        return ESP_OK;
    }
    if (!converter->in_frame_bytes ||
        input_bytes % converter->in_frame_bytes != 0U) {
        return ESP_ERR_INVALID_SIZE;
    }
    uint32_t input_frames = input_bytes / converter->in_frame_bytes;
    uint32_t output_frames = 0;
    if (esp_asrc_get_out_sample_num(converter->handle, input_frames,
                                    &output_frames) != ESP_ASRC_ERR_OK ||
        !output_frames) {
        return ESP_FAIL;
    }
    uint32_t requested_output = output_frames * converter->out_frame_bytes;
    if (ensure_buffer(&converter->in_buf, &converter->in_buf_size, input_bytes,
                      converter->align.inbuf_addr_align,
                      converter->align.inbuf_size_align) != ESP_OK ||
        ensure_buffer(&converter->out_buf, &converter->out_buf_size,
                      requested_output,
                      converter->align.outbuf_addr_align,
                      converter->align.outbuf_size_align) != ESP_OK) {
        return ESP_ERR_NO_MEM;
    }
    memcpy(converter->in_buf, input, input_bytes);
    output_frames = converter->out_buf_size / converter->out_frame_bytes;
    if (esp_asrc_process(converter->handle, converter->in_buf, input_frames,
                         converter->out_buf, &output_frames) !=
        ESP_ASRC_ERR_OK) {
        return ESP_FAIL;
    }
    *output = converter->out_buf;
    *output_bytes = output_frames * converter->out_frame_bytes;
    return ESP_OK;
}

static int player_output(uint8_t *data, int data_size, void *ctx)
{
    player_run_t *run = ctx;
    if (!run || !run->owner || data_size <= 0) {
        return 0;
    }
    rehab_audio_player_adapter_handle_t owner = run->owner;
    xSemaphoreTake(owner->mutex, portMAX_DELAY);
    if (run->closing || owner->current != run) {
        xSemaphoreGive(owner->mutex);
        return 0;
    }
    if (!run->converter_ready) {
        if (converter_create(&run->converter, &owner->output_format,
                             &owner->output_format) != ESP_OK) {
            xSemaphoreGive(owner->mutex);
            return 0;
        }
        run->converter_ready = true;
    }
    uint8_t *output = NULL;
    uint32_t output_bytes = 0;
    esp_err_t err = converter_process(&run->converter, data,
                                      (uint32_t)data_size, &output,
                                      &output_bytes);
    int codec_result = err == ESP_OK
                           ? esp_codec_dev_write(owner->codec, output,
                                                 (int)output_bytes)
                           : ESP_CODEC_DEV_WRONG_STATE;
    xSemaphoreGive(owner->mutex);
    return codec_result == ESP_CODEC_DEV_OK ? data_size : 0;
}

static int player_input(uint8_t *data, int data_size, void *ctx)
{
    player_run_t *run = ctx;
    if (run == NULL || data == NULL || data_size <= 0) {
        return 0;
    }
    return (int)rehab_audio_memory_cursor_read(
        &run->memory_cursor, data, (size_t)data_size);
}

static int player_event(esp_asp_event_pkt_t *packet, void *ctx)
{
    player_run_t *run = ctx;
    if (!run || !run->owner || !packet) {
        return 0;
    }
    rehab_audio_player_adapter_handle_t owner = run->owner;
    rehab_audio_player_event_t event = REHAB_AUDIO_PLAYER_ERROR;
    bool notify = false;

    xSemaphoreTake(owner->mutex, portMAX_DELAY);
    if (run->closing || owner->current != run) {
        xSemaphoreGive(owner->mutex);
        return 0;
    }
    if (packet->type == ESP_ASP_EVENT_TYPE_MUSIC_INFO &&
        packet->payload_size == sizeof(esp_asp_music_info_t)) {
        esp_asp_music_info_t *info = packet->payload;
        audio_format_t source = {
            .sample_rate = (uint32_t)info->sample_rate,
            .channels = (uint8_t)info->channels,
            .bits = (uint8_t)info->bits,
        };
        if (format_complete(&source) != ESP_OK ||
            converter_create(&run->converter, &source,
                             &owner->output_format) != ESP_OK) {
            event = REHAB_AUDIO_PLAYER_ERROR;
            notify = !run->terminal_sent;
            run->terminal_sent = true;
        } else {
            run->converter_ready = true;
        }
    } else if (packet->type == ESP_ASP_EVENT_TYPE_STATE &&
               packet->payload_size == sizeof(esp_asp_state_t)) {
        esp_asp_state_t state = *(esp_asp_state_t *)packet->payload;
        if (state == ESP_ASP_STATE_RUNNING && !run->started_sent) {
            run->started_sent = true;
            event = REHAB_AUDIO_PLAYER_STARTED;
            notify = true;
        } else if (state == ESP_ASP_STATE_FINISHED && !run->terminal_sent) {
            run->terminal_sent = true;
            event = REHAB_AUDIO_PLAYER_FINISHED;
            notify = true;
        } else if (state == ESP_ASP_STATE_ERROR && !run->terminal_sent) {
            run->terminal_sent = true;
            event = REHAB_AUDIO_PLAYER_ERROR;
            notify = true;
        }
    }
    uint32_t generation = run->generation;
    rehab_audio_player_event_cb_t callback = owner->event_cb;
    void *user_ctx = owner->event_user_ctx;
    xSemaphoreGive(owner->mutex);

    if (notify && callback) {
        callback(generation, event, user_ctx);
    }
    return 0;
}

esp_err_t rehab_audio_player_adapter_create(
    const rehab_audio_output_config_t *config,
    rehab_audio_player_event_cb_t event_cb,
    void *user_ctx,
    rehab_audio_player_adapter_handle_t *out_handle)
{
    dev_audio_codec_handles_t *board_audio = NULL;
    rehab_audio_player_adapter_handle_t handle = NULL;

    if (!config || !out_handle) {
        return ESP_ERR_INVALID_ARG;
    }
    if (config->output_volume < VOLUME_CURVE_MIN_LEVEL ||
        config->output_volume > VOLUME_CURVE_MAX_LEVEL ||
        config->output_codec_boost_db_x10 < 0 ||
        config->output_codec_boost_db_x10 > CODEC_BOOST_DB_X10_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_handle = NULL;
    esp_err_t err = esp_board_manager_get_device_handle(
        ESP_BOARD_DEVICE_NAME_AUDIO_DAC, (void **)&board_audio);
    if (err != ESP_OK || !board_audio || !board_audio->codec_dev) {
        return err == ESP_OK ? ESP_ERR_NOT_FOUND : err;
    }

    handle = calloc(1, sizeof(*handle));
    if (!handle) {
        return ESP_ERR_NO_MEM;
    }
    handle->mutex = xSemaphoreCreateMutex();
    handle->codec = board_audio->codec_dev;
    handle->output_format = (audio_format_t){
        .sample_rate = config->output_sample_rate,
        .channels = config->output_channels,
        .bits = config->output_bits,
    };
    handle->volume = config->output_volume;
    handle->codec_boost_db_x10 = config->output_codec_boost_db_x10;
    handle->event_cb = event_cb;
    handle->event_user_ctx = user_ctx;
    if (!handle->mutex || format_complete(&handle->output_format) != ESP_OK) {
        rehab_audio_player_adapter_delete(handle);
        return ESP_ERR_NO_MEM;
    }

    esp_codec_dev_sample_info_t sample_info = {
        .sample_rate = (int)handle->output_format.sample_rate,
        .channel = handle->output_format.channels,
        .bits_per_sample = handle->output_format.bits,
    };
    if (esp_codec_dev_open(handle->codec, &sample_info) != ESP_CODEC_DEV_OK ||
        configure_output_volume(handle) != ESP_CODEC_DEV_OK ||
        esp_codec_dev_set_out_mute(handle->codec, false) != ESP_CODEC_DEV_OK) {
        rehab_audio_player_adapter_delete(handle);
        return ESP_FAIL;
    }
    *out_handle = handle;
    ESP_LOGI(TAG,
             "player ready rate=%lu channels=%u bits=%u volume=%d "
             "codec_boost=+%d.%d dB",
             (unsigned long)handle->output_format.sample_rate,
             handle->output_format.channels, handle->output_format.bits,
             handle->volume, handle->codec_boost_db_x10 / 10,
             handle->codec_boost_db_x10 % 10);
    return ESP_OK;
}

static esp_err_t player_start(
    rehab_audio_player_adapter_handle_t handle,
    const char *uri,
    const uint8_t *memory_data,
    size_t memory_size,
    uint32_t generation)
{
    if (!handle || uri == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    rehab_audio_player_adapter_stop(handle);

    player_run_t *run = calloc(1, sizeof(*run));
    if (!run) {
        return ESP_ERR_NO_MEM;
    }
    run->owner = handle;
    run->generation = generation;
    rehab_audio_memory_cursor_init(&run->memory_cursor, memory_data,
                                   memory_size);
    esp_asp_cfg_t config = {
        .out = {.cb = player_output, .user_ctx = run},
        .task_prio = 0,
        .task_stack = 0,
        .task_core = 0,
        .task_stack_in_ext = false,
    };
    if (memory_data != NULL) {
        config.in.cb = player_input;
        config.in.user_ctx = run;
    }
    if (esp_audio_simple_player_new(&config, &run->player) != ESP_GMF_ERR_OK ||
        esp_audio_simple_player_set_event(run->player, player_event, run) !=
            ESP_GMF_ERR_OK) {
        if (run->player) {
            esp_audio_simple_player_destroy(run->player);
        }
        free(run);
        return ESP_FAIL;
    }

    xSemaphoreTake(handle->mutex, portMAX_DELAY);
    handle->current = run;
    int volume_result = esp_codec_dev_set_out_vol(handle->codec, handle->volume);
    int mute_result = esp_codec_dev_set_out_mute(handle->codec, false);
    xSemaphoreGive(handle->mutex);
    if (volume_result != ESP_CODEC_DEV_OK ||
        mute_result != ESP_CODEC_DEV_OK ||
        esp_audio_simple_player_run(run->player, uri, NULL) !=
            ESP_GMF_ERR_OK) {
        rehab_audio_player_adapter_stop(handle);
        return ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t rehab_audio_player_adapter_play(
    rehab_audio_player_adapter_handle_t handle,
    const char *uri,
    uint32_t generation)
{
    if (!source_uri_is_allowed(uri)) {
        return ESP_ERR_INVALID_ARG;
    }
    return player_start(handle, uri, NULL, 0U, generation);
}

esp_err_t rehab_audio_player_adapter_play_memory(
    rehab_audio_player_adapter_handle_t handle,
    const uint8_t *data,
    size_t size,
    const char *filename,
    uint32_t generation)
{
    if (handle == NULL || data == NULL || size == 0U ||
        !memory_filename_is_allowed(filename)) {
        return ESP_ERR_INVALID_ARG;
    }
    char uri[128];
    int length = snprintf(uri, sizeof(uri), "raw://business/%s", filename);
    if (length <= 0 || length >= (int)sizeof(uri)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return player_start(handle, uri, data, size, generation);
}

esp_err_t rehab_audio_player_adapter_stop(
    rehab_audio_player_adapter_handle_t handle)
{
    if (!handle) {
        return ESP_OK;
    }
    xSemaphoreTake(handle->mutex, portMAX_DELAY);
    player_run_t *run = handle->current;
    handle->current = NULL;
    if (run) {
        run->closing = true;
    }
    xSemaphoreGive(handle->mutex);
    if (!run) {
        return ESP_OK;
    }

    esp_gmf_err_t stop_result = esp_audio_simple_player_stop(run->player);
    esp_audio_simple_player_destroy(run->player);
    converter_destroy(&run->converter);
    free(run);
    return stop_result == ESP_GMF_ERR_OK ||
                   stop_result == ESP_GMF_ERR_INVALID_STATE ||
                   stop_result == ESP_GMF_ERR_NOT_SUPPORT
               ? ESP_OK
               : ESP_FAIL;
}

esp_err_t rehab_audio_player_adapter_delete(
    rehab_audio_player_adapter_handle_t handle)
{
    if (!handle) {
        return ESP_OK;
    }
    rehab_audio_player_adapter_stop(handle);
    if (handle->codec) {
        esp_codec_dev_set_out_mute(handle->codec, true);
        esp_codec_dev_close(handle->codec);
    }
    if (handle->mutex) {
        vSemaphoreDelete(handle->mutex);
    }
    free(handle);
    return ESP_OK;
}
