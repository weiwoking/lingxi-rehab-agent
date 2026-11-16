#pragma once

#include <stddef.h>
#include <stdint.h>

typedef struct {
    const uint8_t *data;
    size_t size;
    size_t offset;
} rehab_audio_memory_cursor_t;

void rehab_audio_memory_cursor_init(rehab_audio_memory_cursor_t *cursor,
                                    const uint8_t *data,
                                    size_t size);
size_t rehab_audio_memory_cursor_read(rehab_audio_memory_cursor_t *cursor,
                                      uint8_t *output,
                                      size_t requested);
