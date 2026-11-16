#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>

#include "rehab_task_decision.h"

typedef enum {
    MODE_STARTED,
    MODE_BUSY,
    MODE_REJECTED,
    MODE_TERMINAL,
    MODE_CONCURRENT,
} fake_mode_t;

struct fake_decision {
    pthread_mutex_t lock;
    fake_mode_t mode;
    unsigned start_calls;
    unsigned status_calls;
    uint32_t generation;
    bool active;
    rehab_task_decision_start_source_t entry_source;
};

const char *esp_err_to_name(esp_err_t err)
{
    return err == ESP_OK ? "ESP_OK" :
        (err == ESP_ERR_INVALID_STATE ? "ESP_ERR_INVALID_STATE" :
         (err == ESP_ERR_TIMEOUT ? "ESP_ERR_TIMEOUT" : "ESP_FAIL"));
}

const char *rehab_task_decision_coordinator_state_name(
    rehab_task_decision_coordinator_state_t state)
{
    switch (state) {
    case REHAB_TASK_DECISION_COORDINATOR_IDLE: return "IDLE";
    case REHAB_TASK_DECISION_COORDINATOR_PREPARING: return "PREPARING";
    case REHAB_TASK_DECISION_COORDINATOR_FAILED: return "FAILED";
    case REHAB_TASK_DECISION_COORDINATOR_CANCELLED: return "CANCELLED";
    default: return "OTHER";
    }
}

esp_err_t rehab_task_decision_coordinator_start(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t *out_generation)
{
    pthread_mutex_lock(&handle->lock);
    handle->start_calls++;
    *out_generation = 0;
    esp_err_t err = ESP_ERR_INVALID_STATE;
    if (handle->mode == MODE_STARTED) {
        handle->generation = 1;
        handle->active = true;
        handle->entry_source = source;
        *out_generation = 1;
        err = ESP_OK;
    } else if (handle->mode == MODE_TERMINAL) {
        handle->generation = 2;
        handle->active = false;
        handle->entry_source = source;
        *out_generation = 2;
    } else if (handle->mode == MODE_CONCURRENT && !handle->active) {
        handle->generation++;
        handle->active = true;
        handle->entry_source = source;
        *out_generation = handle->generation;
        err = ESP_OK;
    }
    pthread_mutex_unlock(&handle->lock);
    return err;
}

esp_err_t rehab_task_decision_coordinator_start_root(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t *out_generation)
{
    return rehab_task_decision_coordinator_start(
        handle, source, out_generation);
}

esp_err_t rehab_task_decision_coordinator_get_status(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_coordinator_status_t *out_status)
{
    pthread_mutex_lock(&handle->lock);
    handle->status_calls++;
    memset(out_status, 0, sizeof(*out_status));
    out_status->generation = handle->generation;
    out_status->active = handle->active;
    if (handle->mode == MODE_TERMINAL) {
        out_status->state = REHAB_TASK_DECISION_COORDINATOR_FAILED;
        strcpy(out_status->last_error, "llm_not_configured");
    } else if (handle->mode == MODE_REJECTED) {
        out_status->state = REHAB_TASK_DECISION_COORDINATOR_IDLE;
        strcpy(out_status->last_error, "training_active");
    } else {
        out_status->state = REHAB_TASK_DECISION_COORDINATOR_PREPARING;
        strcpy(out_status->last_error, "none");
    }
    if (out_status->generation > 0) {
        snprintf(out_status->decision_id, sizeof(out_status->decision_id),
                 "decision-%u", out_status->generation);
    }
    pthread_mutex_unlock(&handle->lock);
    return ESP_OK;
}

static void reset(struct fake_decision *decision, fake_mode_t mode)
{
    pthread_mutex_lock(&decision->lock);
    decision->mode = mode;
    decision->start_calls = 0;
    decision->status_calls = 0;
    decision->generation = mode == MODE_BUSY ? 7 : 0;
    decision->active = mode == MODE_BUSY;
    decision->entry_source = REHAB_TASK_DECISION_START_SOURCE_UNKNOWN;
    pthread_mutex_unlock(&decision->lock);
}

static void assert_case(struct fake_decision *decision, fake_mode_t mode,
                        rehab_task_decision_start_outcome_t expected,
                        bool created, uint32_t generation)
{
    reset(decision, mode);
    rehab_task_decision_start_result_t result = {0};
    rehab_task_decision_coordinator_status_t status = {0};
    assert(rehab_task_decision_start_confirmed(
        decision, REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI,
        &result, &status) == ESP_OK);
    assert(result.outcome == expected);
    assert(result.created_new_generation == created);
    assert(result.generation == generation);
    assert(status.generation == generation);
    assert(decision->start_calls == 1);
    assert(decision->status_calls == 1);
    if (created) {
        assert(decision->entry_source ==
               REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI);
    }
}

typedef struct {
    struct fake_decision *decision;
    rehab_task_decision_start_source_t source;
    rehab_task_decision_start_result_t result;
} thread_call_t;

static void *thread_start(void *arg)
{
    thread_call_t *call = arg;
    assert(rehab_task_decision_start_confirmed(
        call->decision, call->source, &call->result, NULL) == ESP_OK);
    return NULL;
}

int main(void)
{
    struct fake_decision decision = {
        .lock = PTHREAD_MUTEX_INITIALIZER,
    };
    assert_case(&decision, MODE_STARTED,
                REHAB_TASK_DECISION_START_OUTCOME_STARTED, true, 1);
    assert_case(&decision, MODE_BUSY,
                REHAB_TASK_DECISION_START_OUTCOME_ATTACHED_BUSY, false, 7);
    assert_case(&decision, MODE_REJECTED,
                REHAB_TASK_DECISION_START_OUTCOME_REJECTED, false, 0);
    assert_case(&decision, MODE_TERMINAL,
                REHAB_TASK_DECISION_START_OUTCOME_TERMINAL_FAILURE, true, 2);

    assert(strcmp(rehab_task_decision_start_source_name(
        REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI), "local_ui") == 0);
    assert(strcmp(rehab_task_decision_start_source_name(
        REHAB_TASK_DECISION_START_SOURCE_WECHAT), "wechat") == 0);
    assert(strcmp(rehab_task_decision_start_source_name(
        REHAB_TASK_DECISION_START_SOURCE_CONSOLE), "console") == 0);
    assert(strcmp(rehab_task_decision_start_source_name(
        REHAB_TASK_DECISION_START_SOURCE_AGENT), "unknown") == 0);
    assert(strcmp(rehab_task_decision_start_source_name(
        REHAB_TASK_DECISION_START_SOURCE_UNKNOWN), "unknown") == 0);

    const rehab_task_decision_start_source_t factual_sources[] = {
        REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI,
        REHAB_TASK_DECISION_START_SOURCE_WECHAT,
        REHAB_TASK_DECISION_START_SOURCE_CONSOLE,
        REHAB_TASK_DECISION_START_SOURCE_UNKNOWN,
    };
    for (size_t i = 0; i < sizeof(factual_sources) /
                               sizeof(factual_sources[0]); i++) {
        reset(&decision, MODE_STARTED);
        rehab_task_decision_start_result_t result = {0};
        assert(rehab_task_decision_start_confirmed(
            &decision, factual_sources[i], &result, NULL) == ESP_OK);
        assert(result.source == factual_sources[i]);
        assert(decision.entry_source == factual_sources[i]);
    }
    reset(&decision, MODE_STARTED);
    rehab_task_decision_start_result_t legacy_agent_result = {0};
    assert(rehab_task_decision_start_confirmed(
        &decision, REHAB_TASK_DECISION_START_SOURCE_AGENT,
        &legacy_agent_result, NULL) == ESP_OK);
    assert(legacy_agent_result.source ==
           REHAB_TASK_DECISION_START_SOURCE_AGENT);
    assert(decision.entry_source ==
           REHAB_TASK_DECISION_START_SOURCE_UNKNOWN);

    reset(&decision, MODE_CONCURRENT);
    thread_call_t calls[3] = {
        { .decision = &decision,
          .source = REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI },
        { .decision = &decision,
          .source = REHAB_TASK_DECISION_START_SOURCE_WECHAT },
        { .decision = &decision,
          .source = REHAB_TASK_DECISION_START_SOURCE_CONSOLE },
    };
    pthread_t threads[3];
    for (size_t i = 0; i < 3; i++) {
        assert(pthread_create(&threads[i], NULL, thread_start, &calls[i]) == 0);
    }
    unsigned started = 0;
    unsigned attached = 0;
    for (size_t i = 0; i < 3; i++) {
        assert(pthread_join(threads[i], NULL) == 0);
        started += calls[i].result.outcome ==
            REHAB_TASK_DECISION_START_OUTCOME_STARTED;
        attached += calls[i].result.outcome ==
            REHAB_TASK_DECISION_START_OUTCOME_ATTACHED_BUSY;
        assert(calls[i].result.generation == 1);
    }
    assert(started == 1 && attached == 2);
    for (size_t i = 0; i < 3; i++) {
        if (calls[i].result.outcome ==
            REHAB_TASK_DECISION_START_OUTCOME_STARTED) {
            assert(decision.entry_source == calls[i].source);
        }
    }
    assert(decision.generation == 1);
    assert(decision.start_calls == 3 && decision.status_calls == 3);
    pthread_mutex_destroy(&decision.lock);
    puts("confirmed-start host tests passed");
    return 0;
}
