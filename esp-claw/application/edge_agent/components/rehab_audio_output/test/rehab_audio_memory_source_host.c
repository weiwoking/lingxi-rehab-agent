#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "rehab_audio_memory_source.h"

int main(void)
{
    static const uint8_t data[] = {0, 1, 2, 3, 4, 5, 6};
    uint8_t output[8] = {0};
    rehab_audio_memory_cursor_t first;
    rehab_audio_memory_cursor_t second;

    rehab_audio_memory_cursor_init(&first, data, sizeof(data));
    assert(rehab_audio_memory_cursor_read(&first, output, 3U) == 3U);
    assert(memcmp(output, data, 3U) == 0);
    assert(first.offset == 3U);
    assert(rehab_audio_memory_cursor_read(&first, output, sizeof(output)) == 4U);
    assert(memcmp(output, data + 3U, 4U) == 0);
    assert(first.offset == sizeof(data));
    assert(rehab_audio_memory_cursor_read(&first, output, 1U) == 0U);

    rehab_audio_memory_cursor_init(&second, data, sizeof(data));
    assert(second.offset == 0U);
    assert(rehab_audio_memory_cursor_read(&second, output, 2U) == 2U);
    assert(output[0] == 0U && output[1] == 1U);
    assert(first.offset == sizeof(data));

    rehab_audio_memory_cursor_init(&first, data, sizeof(data));
    assert(first.offset == 0U);
    assert(rehab_audio_memory_cursor_read(&first, output, sizeof(data)) ==
           sizeof(data));
    return 0;
}
