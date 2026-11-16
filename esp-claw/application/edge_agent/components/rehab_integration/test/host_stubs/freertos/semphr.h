#pragma once

typedef void *SemaphoreHandle_t;

static inline SemaphoreHandle_t xSemaphoreCreateMutex(void)
{
    static int mutex;
    return &mutex;
}

static inline int xSemaphoreTake(SemaphoreHandle_t mutex, unsigned int timeout)
{
    (void)mutex;
    (void)timeout;
    return pdTRUE;
}

static inline void xSemaphoreGive(SemaphoreHandle_t mutex)
{
    (void)mutex;
}

