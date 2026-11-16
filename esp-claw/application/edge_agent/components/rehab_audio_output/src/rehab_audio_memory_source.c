#include "rehab_audio_memory_source.h"

#include <string.h>

void rehab_audio_memory_cursor_init(rehab_audio_memory_cursor_t *cursor,
                                    const uint8_t *data,
                                    size_t size)
{
    if (cursor == NULL) {
        return;
    }
    cursor->data = data;
    cursor->size = data != NULL ? size : 0U;
    cursor->offset = 0U;
}

size_t rehab_audio_memory_cursor_read(rehab_audio_memory_cursor_t *cursor,
                                      uint8_t *output,
                                      size_t requested)
{
    if (cursor == NULL || output == NULL || requested == 0U ||
        cursor->data == NULL || cursor->offset >= cursor->size) {
        return 0U;
    }
    size_t remaining = cursor->size - cursor->offset;
    size_t count = requested < remaining ? requested : remaining;
    memcpy(output, cursor->data + cursor->offset, count);
    cursor->offset += count;
    return count;
}
