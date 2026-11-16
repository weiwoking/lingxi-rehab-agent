// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_transfer_priv.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_console.h"

static int print_status(rehab_scan_transfer_handle_t handle)
{
    rehab_scan_transfer_status_t status;
    esp_err_t err = rehab_scan_transfer_get_status(handle, &status);
    if (err != ESP_OK) {
        printf("error=%s code=%d\n", esp_err_to_name(err), (int)err);
        return 1;
    }
    printf("state=%s scan_id=%s service_job_id=%s pipeline_job_id=%s "
           "bytes_sent=%" PRIu64 " result_bytes=%" PRIu64 " polls=%u "
           "http_status=%d last_error=%d last_errno=%d aborted=%d "
           "error_code=%s result_path=%s\n",
           rehab_scan_transfer_state_name(status.state), status.scan_id,
           status.service_job_id, status.pipeline_job_id, status.bytes_sent,
           status.result_bytes, (unsigned)status.poll_count,
           status.last_http_status, (int)status.last_error, status.last_errno,
           status.aborted, status.error_code, status.result_path);
    return 0;
}

static int console_handler(void *context, int argc, char **argv)
{
    rehab_scan_transfer_handle_t handle = context;
    if (handle == NULL || (argc != 2 && argc != 3)) {
        printf("error=usage usage='rehab_scan_send <latest|scan_id|status|abort|upload-only scan_id>'\n");
        return 1;
    }
    if (argc == 3) {
        if (strcmp(argv[1], "upload-only") != 0) {
            printf("error=usage usage='rehab_scan_send upload-only <scan_id>'\n");
            return 1;
        }
        char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
        esp_err_t err = rehab_scan_transfer_start_mode(
            handle, argv[2], REHAB_SCAN_TRANSFER_MODE_UPLOAD_ONLY,
            scan_id, sizeof(scan_id));
        if (err != ESP_OK) {
            printf("error=%s code=%d\n", esp_err_to_name(err), (int)err);
            return 1;
        }
        printf("scan_id=%s mode=upload_only state=uploading\n", scan_id);
        return 0;
    }
    if (strcmp(argv[1], "status") == 0) return print_status(handle);
    if (strcmp(argv[1], "abort") == 0) {
        esp_err_t err = rehab_scan_transfer_abort(handle);
        if (err != ESP_OK) {
            printf("state=inactive error=%s code=%d\n", esp_err_to_name(err), (int)err);
            return 1;
        }
        printf("state=aborting\n");
        return 0;
    }
    char scan_id[REHAB_SCAN_TRANSFER_SCAN_ID_MAX];
    esp_err_t err = rehab_scan_transfer_start(handle, argv[1], scan_id, sizeof(scan_id));
    if (err != ESP_OK) {
        printf("error=%s code=%d\n", esp_err_to_name(err), (int)err);
        return 1;
    }
    printf("scan_id=%s state=uploading\n", scan_id);
    return 0;
}

esp_err_t rehab_scan_transfer_console_register(rehab_scan_transfer_handle_t handle)
{
    if (handle == NULL || handle->console_registered) return ESP_ERR_INVALID_ARG;
    const esp_console_cmd_t command = {
        .command = "rehab_scan_send",
        .help = "Stream a completed scan to the PC service and store its result",
        .hint = "<latest|scan_id|status|abort|upload-only scan_id>",
        .func_w_context = console_handler,
        .context = handle,
    };
    esp_err_t err = esp_console_cmd_register(&command);
    if (err == ESP_OK) handle->console_registered = true;
    return err;
}
