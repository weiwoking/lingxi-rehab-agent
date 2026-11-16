#pragma once

typedef struct {
    const char *name;
    void *collect;
    void *user_ctx;
    unsigned flags;
} claw_core_context_provider_t;
