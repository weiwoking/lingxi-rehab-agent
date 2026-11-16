// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_capture_priv.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_console.h"

static bool parse_u32(const char *text, uint32_t *out_value)
{
    if (text == NULL || text[0] == '\0' || out_value == NULL || text[0] == '-') {
        return false;
    }
    errno = 0;
    char *end = NULL;
    unsigned long value = strtoul(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value > UINT32_MAX) {
        return false;
    }
    *out_value = (uint32_t)value;
    return true;
}

static int console_start(rehab_scan_capture_handle_t handle, int argc, char **argv)
{
    rehab_scan_capture_request_t request = {
        .frame_count = handle->config.default_frame_count,
        .interval_ms = handle->config.default_interval_ms,
    };
    for (int i = 2; i < argc; i += 2) {
        if (i + 1 >= argc) {
            printf("error=invalid_arguments usage=rehab_scan_start_options\n");
            return 1;
        }
        if (strcmp(argv[i], "--frames") == 0) {
            if (!parse_u32(argv[i + 1], &request.frame_count)) {
                printf("error=invalid_frames\n");
                return 1;
            }
        } else if (strcmp(argv[i], "--interval-ms") == 0) {
            if (!parse_u32(argv[i + 1], &request.interval_ms)) {
                printf("error=invalid_interval_ms\n");
                return 1;
            }
        } else {
            printf("error=unknown_option option=%s\n", argv[i]);
            return 1;
        }
    }
    char scan_id[REHAB_SCAN_CAPTURE_SCAN_ID_MAX];
    esp_err_t err = rehab_scan_capture_start(handle, &request,
                                             scan_id, sizeof(scan_id));
    if (err != ESP_OK) {
        printf("error=%s code=%d\n", esp_err_to_name(err), (int)err);
        return 1;
    }
    printf("scan_id=%s state=capturing\n", scan_id);
    return 0;
}

static int console_status(rehab_scan_capture_handle_t handle)
{
    rehab_scan_capture_status_t status;
    esp_err_t err = rehab_scan_capture_get_status(handle, &status);
    if (err != ESP_OK) {
        printf("error=%s code=%d\n", esp_err_to_name(err), (int)err);
        return 1;
    }
    printf("state=%s scan_id=%s requested=%u committed=%u last_error=%d "
           "last_errno=%d failed_frame=%u aborted=%d path=%s\n",
           rehab_scan_capture_state_name(status.state), status.scan_id,
           (unsigned)status.requested_frames,
           (unsigned)status.committed_frames, (int)status.last_error,
           status.last_errno, (unsigned)status.failed_frame_sequence,
           status.aborted, status.final_path);
    return 0;
}

static int scan_console_handler(void *context, int argc, char **argv)
{
    rehab_scan_capture_handle_t handle = context;
    if (handle == NULL || argc < 2) {
        printf("error=usage usage='rehab_scan <start|status|abort>'\n");
        return 1;
    }
    if (strcmp(argv[1], "start") == 0) {
        return console_start(handle, argc, argv);
    }
    if (strcmp(argv[1], "status") == 0 && argc == 2) {
        return console_status(handle);
    }
    if (strcmp(argv[1], "abort") == 0 && argc == 2) {
        esp_err_t err = rehab_scan_capture_abort(handle);
        if (err != ESP_OK) {
            printf("state=inactive error=%s code=%d\n", esp_err_to_name(err), (int)err);
            return 1;
        }
        printf("state=aborting\n");
        return 0;
    }
    printf("error=usage usage='rehab_scan <start|status|abort>'\n");
    return 1;
}

esp_err_t rehab_scan_capture_console_register(rehab_scan_capture_handle_t handle)
{
    if (handle == NULL || handle->console_registered) {
        return ESP_ERR_INVALID_ARG;
    }
    const esp_console_cmd_t command = {
        .command = "rehab_scan",
        .help = "Capture an atomic Camera+ToF scan: start/status/abort",
        .hint = "<start [--frames N] [--interval-ms N]|status|abort>",
        .func_w_context = scan_console_handler,
        .context = handle,
    };
    esp_err_t err = esp_console_cmd_register(&command);
    if (err == ESP_OK) {
        handle->console_registered = true;
    }
    return err;
}
