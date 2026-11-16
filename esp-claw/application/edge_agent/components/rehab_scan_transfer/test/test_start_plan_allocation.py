#!/usr/bin/env python3
"""Host-control-flow tests for rehab_scan_transfer_start's temporary plan."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
SOURCE = COMPONENT / "src" / "rehab_scan_transfer.c"


def extract_start_function(source: str) -> str:
    marker = "esp_err_t rehab_scan_transfer_start_mode("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("unterminated rehab_scan_transfer_start")


def static_checks(function: str) -> None:
    assert not re.search(r"\brehab_scan_transfer_plan_t\s+plan\b", function)
    assert function.count("heap_caps_calloc(") == 2
    assert "MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT" in function
    assert "heap_caps_calloc(1, sizeof(*plan), MALLOC_CAP_8BIT)" in function
    assert "rehab_scan_transfer_select_and_validate(handle, selector, plan)" in function
    assert function.count("heap_caps_free(plan);") == 1
    assert "handle->plan = *plan;" in function
    assert function.index("handle->plan = *plan;") < function.index("xTaskCreate(")
    assert function.index("heap_caps_free(plan);") > function.index("xTaskCreate(")


HARNESS_PREFIX = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define REHAB_SCAN_TRANSFER_SCAN_ID_MAX 129
#define REHAB_SCAN_TRANSFER_PATH_MAX 384
#define REHAB_SCAN_TRANSFER_FIELD_MAX 48
#define REHAB_SCAN_TRANSFER_MAX_PARTS 34U
#define REHAB_SCAN_TRANSFER_ERROR_CODE_MAX 48
#define MALLOC_CAP_SPIRAM 1U
#define MALLOC_CAP_8BIT 2U

#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM -2
#define ESP_ERR_INVALID_ARG -3
#define ESP_ERR_INVALID_STATE -4
#define ESP_ERR_INVALID_SIZE -5
#define TEST_SELECT_ERROR -6
#define pdPASS 1
#define pdFAIL 0

typedef int esp_err_t;
typedef int BaseType_t;
typedef void *SemaphoreHandle_t;
typedef void *TaskHandle_t;

typedef enum {
    REHAB_SCAN_TRANSFER_STATE_IDLE,
    REHAB_SCAN_TRANSFER_STATE_UPLOADING,
    REHAB_SCAN_TRANSFER_STATE_WAITING_PROCESSING,
    REHAB_SCAN_TRANSFER_STATE_RECEIVING_RESULT,
    REHAB_SCAN_TRANSFER_STATE_COMPLETED,
    REHAB_SCAN_TRANSFER_STATE_FAILED,
    REHAB_SCAN_TRANSFER_STATE_ABORTING,
} rehab_scan_transfer_state_t;

typedef enum {
    REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE = 0,
    REHAB_SCAN_TRANSFER_MODE_UPLOAD_ONLY,
    REHAB_SCAN_TRANSFER_MODE_AUTO_DECISION,
} rehab_scan_transfer_mode_t;

typedef struct {
    char path[REHAB_SCAN_TRANSFER_PATH_MAX];
    char field[REHAB_SCAN_TRANSFER_FIELD_MAX];
    const char *filename;
    const char *content_type;
    uint64_t size;
} rehab_scan_transfer_part_t;

typedef struct {
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    char scan_path[REHAB_SCAN_TRANSFER_PATH_MAX];
    uint32_t frame_count;
    size_t part_count;
    uint64_t raw_bytes;
    rehab_scan_transfer_part_t parts[REHAB_SCAN_TRANSFER_MAX_PARTS];
} rehab_scan_transfer_plan_t;

typedef struct {
    uint32_t worker_stack_size;
    unsigned worker_priority;
} rehab_scan_transfer_config_t;

typedef struct {
    rehab_scan_transfer_state_t state;
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    uint32_t generation;
    esp_err_t last_error;
    char error_code[REHAB_SCAN_TRANSFER_ERROR_CODE_MAX];
} rehab_scan_transfer_status_t;

struct rehab_scan_transfer {
    rehab_scan_transfer_config_t config;
    char latest_scene_result_path[REHAB_SCAN_TRANSFER_PATH_MAX];
    SemaphoreHandle_t lock;
    TaskHandle_t worker;
    bool abort_requested;
    rehab_scan_transfer_mode_t mode;
    uint32_t requested_generation;
    rehab_scan_transfer_status_t status;
    rehab_scan_transfer_plan_t plan;
};
typedef struct rehab_scan_transfer *rehab_scan_transfer_handle_t;

typedef struct { bool sta_connected; } wifi_manager_status_t;

static int g_alloc_calls;
static unsigned g_alloc_caps[2];
static bool g_fail_first_alloc;
static bool g_fail_second_alloc;
static void *g_live_allocation;
static size_t g_live_bytes;
static int g_free_calls;
static int g_double_free;
static int g_lock_calls;
static int g_fail_lock_call;
static int g_active_calls;
static bool g_second_state_conflict;
static int g_select_calls;
static esp_err_t g_select_result;
static BaseType_t g_task_result;
static int g_task_calls;
static rehab_scan_transfer_plan_t g_expected_plan;
static rehab_scan_transfer_plan_t g_worker_snapshot;

static void expect(bool condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", message);
        exit(1);
    }
}

static size_t strlcpy(char *destination, const char *source, size_t size)
{
    size_t length = strlen(source);
    if (size != 0) {
        size_t copied = length < size - 1 ? length : size - 1;
        memcpy(destination, source, copied);
        destination[copied] = '\0';
    }
    return length;
}

static void *heap_caps_calloc(size_t count, size_t size, unsigned caps)
{
    int call = ++g_alloc_calls;
    if (call <= 2) {
        g_alloc_caps[call - 1] = caps;
    }
    if ((call == 1 && g_fail_first_alloc) || (call == 2 && g_fail_second_alloc)) {
        return NULL;
    }
    void *memory = calloc(count, size);
    expect(memory != NULL, "host calloc failed");
    expect(g_live_allocation == NULL, "more than one temporary plan is live");
    g_live_allocation = memory;
    g_live_bytes = count * size;
    return memory;
}

static void heap_caps_free(void *memory)
{
    ++g_free_calls;
    if (memory == NULL || memory != g_live_allocation) {
        g_double_free = 1;
        return;
    }
    memset(memory, 0xa5, g_live_bytes);
    free(memory);
    g_live_allocation = NULL;
    g_live_bytes = 0;
}

static void wifi_manager_get_status(wifi_manager_status_t *status)
{
    status->sta_connected = true;
}

static bool lock_take(rehab_scan_transfer_handle_t handle)
{
    (void)handle;
    ++g_lock_calls;
    return g_lock_calls != g_fail_lock_call;
}

static void xSemaphoreGive(SemaphoreHandle_t lock)
{
    (void)lock;
}

static bool rehab_scan_transfer_is_active(rehab_scan_transfer_state_t state)
{
    (void)state;
    ++g_active_calls;
    return g_second_state_conflict && g_active_calls == 2;
}

static void fill_plan(rehab_scan_transfer_plan_t *plan)
{
    memset(plan, 0, sizeof(*plan));
    strlcpy(plan->scan_id, "scan_20260718_123456_999", sizeof(plan->scan_id));
    strlcpy(plan->scan_path, "/sdcard/rehab/scans/scan_20260718_123456_999", sizeof(plan->scan_path));
    plan->frame_count = 5;
    plan->part_count = REHAB_SCAN_TRANSFER_MAX_PARTS;
    plan->raw_bytes = UINT64_C(2312852);
    for (size_t index = 0; index < REHAB_SCAN_TRANSFER_MAX_PARTS; ++index) {
        snprintf(plan->parts[index].path, sizeof(plan->parts[index].path),
                 "/sdcard/part_%02zu.bin", index);
        snprintf(plan->parts[index].field, sizeof(plan->parts[index].field),
                 "frame_%02zu_field", index);
        plan->parts[index].filename = "payload.bin";
        plan->parts[index].content_type = "application/octet-stream";
        plan->parts[index].size = UINT64_C(460800) + index;
    }
}

static esp_err_t rehab_scan_transfer_select_and_validate(
    rehab_scan_transfer_handle_t handle, const char *selector,
    rehab_scan_transfer_plan_t *plan)
{
    (void)handle;
    (void)selector;
    ++g_select_calls;
    if (g_select_result != ESP_OK) {
        return g_select_result;
    }
    fill_plan(plan);
    g_expected_plan = *plan;
    return ESP_OK;
}

static void transfer_worker(void *argument)
{
    (void)argument;
}

static BaseType_t xTaskCreate(void (*task)(void *), const char *name,
                              uint32_t stack_size, void *argument,
                              unsigned priority, TaskHandle_t *out_worker)
{
    (void)task;
    (void)name;
    (void)stack_size;
    (void)priority;
    ++g_task_calls;
    g_worker_snapshot = ((rehab_scan_transfer_handle_t)argument)->plan;
    if (g_task_result == pdPASS) {
        *out_worker = (TaskHandle_t)(uintptr_t)1;
    }
    return g_task_result;
}

static void reset_case(struct rehab_scan_transfer *handle)
{
    memset(handle, 0, sizeof(*handle));
    handle->config.worker_stack_size = 12288;
    handle->config.worker_priority = 5;
    handle->lock = (SemaphoreHandle_t)(uintptr_t)1;
    g_alloc_calls = 0;
    memset(g_alloc_caps, 0, sizeof(g_alloc_caps));
    g_fail_first_alloc = false;
    g_fail_second_alloc = false;
    g_live_allocation = NULL;
    g_live_bytes = 0;
    g_free_calls = 0;
    g_double_free = 0;
    g_lock_calls = 0;
    g_fail_lock_call = 0;
    g_active_calls = 0;
    g_second_state_conflict = false;
    g_select_calls = 0;
    g_select_result = ESP_OK;
    g_task_result = pdPASS;
    g_task_calls = 0;
    memset(&g_expected_plan, 0, sizeof(g_expected_plan));
    memset(&g_worker_snapshot, 0, sizeof(g_worker_snapshot));
}
'''


HARNESS_SUFFIX = r'''
static void assert_one_free(void)
{
    expect(g_free_calls == 1, "temporary plan was not freed exactly once");
    expect(!g_double_free, "temporary plan was double-freed");
    expect(g_live_allocation == NULL, "temporary plan leaked");
}

static void test_success(bool fallback)
{
    struct rehab_scan_transfer handle;
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    reset_case(&handle);
    g_fail_first_alloc = fallback;
    esp_err_t result = rehab_scan_transfer_start_mode(
        &handle, "latest", REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE,
        scan_id, sizeof(scan_id));
    expect(result == ESP_OK, "success path returned an error");
    expect(g_alloc_calls == (fallback ? 2 : 1), "unexpected allocation call count");
    expect(g_alloc_caps[0] == (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT),
           "first allocation was not PSRAM-preferred");
    if (fallback) {
        expect(g_alloc_caps[1] == MALLOC_CAP_8BIT, "fallback was not 8-bit internal RAM");
    }
    expect(g_task_calls == 1, "worker was not created");
    expect(memcmp(&handle.plan, &g_expected_plan, sizeof(handle.plan)) == 0,
           "handle plan did not retain the complete validated plan");
    expect(memcmp(&g_worker_snapshot, &g_expected_plan, sizeof(g_worker_snapshot)) == 0,
           "worker creation happened before the complete plan copy");
    expect(strcmp(scan_id, g_expected_plan.scan_id) == 0, "scan id copy differs");
    assert_one_free();
}

static void test_double_allocation_failure(void)
{
    struct rehab_scan_transfer handle;
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    reset_case(&handle);
    g_fail_first_alloc = true;
    g_fail_second_alloc = true;
    esp_err_t result = rehab_scan_transfer_start_mode(
        &handle, "latest", REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE,
        scan_id, sizeof(scan_id));
    expect(result == ESP_ERR_NO_MEM, "double allocation failure result differs");
    expect(g_alloc_calls == 2, "double allocation failure did not try both heaps");
    expect(g_select_calls == 0 && g_task_calls == 0, "work continued after allocation failure");
    expect(g_free_calls == 0 && g_live_allocation == NULL, "failed allocations were freed incorrectly");
}

static void test_error_exits(void)
{
    struct rehab_scan_transfer handle;
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    esp_err_t result;

    reset_case(&handle);
    g_select_result = TEST_SELECT_ERROR;
    result = rehab_scan_transfer_start_mode(
        &handle, "latest", REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE,
        scan_id, sizeof(scan_id));
    expect(result == TEST_SELECT_ERROR, "select/validate error changed");
    assert_one_free();

    reset_case(&handle);
    result = rehab_scan_transfer_start_mode(
        &handle, "latest", REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE,
        scan_id, 4);
    expect(result == ESP_ERR_INVALID_SIZE, "small out_scan_id result differs");
    assert_one_free();

    reset_case(&handle);
    g_fail_lock_call = 2;
    result = rehab_scan_transfer_start_mode(
        &handle, "latest", REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE,
        scan_id, sizeof(scan_id));
    expect(result == ESP_FAIL, "second lock failure result differs");
    assert_one_free();

    reset_case(&handle);
    g_second_state_conflict = true;
    result = rehab_scan_transfer_start_mode(
        &handle, "latest", REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE,
        scan_id, sizeof(scan_id));
    expect(result == ESP_ERR_INVALID_STATE, "second state conflict result differs");
    assert_one_free();

    reset_case(&handle);
    g_task_result = pdFAIL;
    result = rehab_scan_transfer_start_mode(
        &handle, "latest", REHAB_SCAN_TRANSFER_MODE_FULL_PIPELINE,
        scan_id, sizeof(scan_id));
    expect(result == ESP_ERR_NO_MEM, "worker creation failure result differs");
    expect(memcmp(&handle.plan, &g_expected_plan, sizeof(handle.plan)) == 0,
           "validated plan was not copied before worker creation failure");
    expect(handle.worker == NULL, "failed worker handle was not cleared");
    expect(handle.status.state == REHAB_SCAN_TRANSFER_STATE_FAILED,
           "worker creation failure state differs");
    assert_one_free();
}

int main(void)
{
    expect(sizeof(rehab_scan_transfer_plan_t) > 7U * 1024U,
           "test plan no longer represents the large object");
    test_success(false);
    test_success(true);
    test_double_allocation_failure();
    test_error_exits();
    printf("PASS: 8 start-plan allocation/control-flow cases; sizeof(plan)=%zu\n",
           sizeof(rehab_scan_transfer_plan_t));
    return 0;
}
'''


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    function = extract_start_function(source)
    static_checks(function)

    compiler = shutil.which("gcc") or shutil.which("cc")
    if compiler is None:
        raise RuntimeError("host gcc/cc is required")

    with tempfile.TemporaryDirectory(prefix="rehab_scan_transfer_start_") as temp:
        temp_path = Path(temp)
        harness = temp_path / "start_plan_harness.c"
        executable = temp_path / "start_plan_harness.exe"
        harness.write_text(HARNESS_PREFIX + "\n" + function + "\n" + HARNESS_SUFFIX,
                           encoding="utf-8")
        compile_command = [
            compiler, "-std=c17", "-Wall", "-Wextra", "-Werror",
            str(harness), "-o", str(executable),
        ]
        compiler_env = os.environ.copy()
        compiler_env["PATH"] = str(Path(compiler).parent) + os.pathsep + compiler_env["PATH"]
        compile_result = subprocess.run(
            compile_command,
            check=False, capture_output=True, text=True, env=compiler_env,
        )
        if compile_result.returncode != 0:
            raise AssertionError(
                f"host compile exited {compile_result.returncode}: "
                f"{compile_command!r}\n{compile_result.stdout}{compile_result.stderr}"
            )
        run_result = subprocess.run(
            [str(executable)], check=False, capture_output=True, text=True,
        )
        if run_result.returncode != 0:
            raise AssertionError(run_result.stdout + run_result.stderr)

    print(json.dumps({
        "verdict": "PASS",
        "static_checks": 9,
        "control_flow_cases": 8,
        "host_output": run_result.stdout.strip(),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
