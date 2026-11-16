#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "rehab_audio_output.h"

#define REHAB_AUDIO_PROMPT_COUNT     12U
#define REHAB_AUDIO_PROMPT_MAX_BYTES (1024U * 1024U)

typedef struct {
    rehab_audio_output_prompt_t prompt;
    const char *filename;
    const char *media_type;
    uint8_t *data;
    size_t size;
    bool available;
} rehab_audio_prompt_entry_t;

typedef struct {
    rehab_audio_prompt_entry_t entries[REHAB_AUDIO_PROMPT_COUNT];
    size_t available_count;
    size_t total_bytes;
} rehab_audio_prompt_store_t;

void rehab_audio_prompt_store_preload(rehab_audio_prompt_store_t *store);
void rehab_audio_prompt_store_deinit(rehab_audio_prompt_store_t *store);
bool rehab_audio_prompt_store_is_valid_prompt(
    rehab_audio_output_prompt_t prompt);
const rehab_audio_prompt_entry_t *rehab_audio_prompt_store_get(
    const rehab_audio_prompt_store_t *store,
    rehab_audio_output_prompt_t prompt);
