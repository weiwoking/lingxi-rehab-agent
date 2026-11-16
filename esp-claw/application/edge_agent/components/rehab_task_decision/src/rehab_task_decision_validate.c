// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"

#include <string.h>

/* Shared bounded UTF-8 validator. The former parallel Agent score/commit
 * engine was removed in stage 4A; formal scoring now has one coordinator. */
bool rehab_task_decision_text_valid(const char *text,
                                    size_t max_bytes,
                                    size_t max_codepoints,
                                    bool allow_empty)
{
    if (text == NULL) return false;
    size_t length = strnlen(text, max_bytes + 1U);
    if (length > max_bytes || (length == 0U && !allow_empty)) return false;
    const unsigned char *cursor = (const unsigned char *)text;
    size_t offset = 0U;
    size_t codepoints = 0U;
    while (offset < length) {
        unsigned char first = cursor[offset];
        size_t width = 0U;
        uint32_t codepoint = 0U;
        if (first <= 0x7FU) {
            width = 1U;
            codepoint = first;
        } else if (first >= 0xC2U && first <= 0xDFU) {
            width = 2U;
            codepoint = first & 0x1FU;
        } else if (first >= 0xE0U && first <= 0xEFU) {
            width = 3U;
            codepoint = first & 0x0FU;
        } else if (first >= 0xF0U && first <= 0xF4U) {
            width = 4U;
            codepoint = first & 0x07U;
        } else {
            return false;
        }
        if (width > length - offset) return false;
        for (size_t i = 1U; i < width; ++i) {
            if ((cursor[offset + i] & 0xC0U) != 0x80U) return false;
            codepoint = (codepoint << 6U) |
                (uint32_t)(cursor[offset + i] & 0x3FU);
        }
        if ((width == 3U &&
             ((first == 0xE0U && cursor[offset + 1U] < 0xA0U) ||
              (first == 0xEDU && cursor[offset + 1U] >= 0xA0U))) ||
            (width == 4U &&
             ((first == 0xF0U && cursor[offset + 1U] < 0x90U) ||
              (first == 0xF4U && cursor[offset + 1U] > 0x8FU)))) {
            return false;
        }
        if ((codepoint <= 0x1FU && codepoint != '\t' &&
             codepoint != '\n' && codepoint != '\r') ||
            (codepoint >= 0x7FU && codepoint <= 0x9FU)) {
            return false;
        }
        offset += width;
        if (++codepoints > max_codepoints) return false;
    }
    return true;
}
