#pragma once

#include <stdarg.h>
#include <stdio.h>

static inline void host_esp_log(const char *tag, const char *format, ...)
{
    va_list args;
    va_start(args, format);
    fprintf(stdout, "%s ", tag);
    vfprintf(stdout, format, args);
    fputc('\n', stdout);
    va_end(args);
}

#define ESP_LOGI(tag, format, ...) \
    host_esp_log((tag), (format), ##__VA_ARGS__)
