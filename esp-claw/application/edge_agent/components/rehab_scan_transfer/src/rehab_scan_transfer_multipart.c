// SPDX-License-Identifier: Apache-2.0
#include "rehab_scan_transfer_priv.h"

#include <limits.h>
#include <string.h>

#include "esp_http_client.h"
#include "esp_system.h"

static esp_err_t checked_add(size_t *total, size_t value)
{
    if (SIZE_MAX - *total < value) return ESP_ERR_INVALID_SIZE;
    *total += value;
    return ESP_OK;
}

static int header_length(const rehab_scan_transfer_part_t *part,
                         const char *boundary)
{
    return snprintf(NULL, 0,
        "--%s\r\nContent-Disposition: form-data; name=\"%s\"; "
        "filename=\"%s\"\r\nContent-Type: %s\r\n\r\n",
        boundary, part->field, part->filename, part->content_type);
}

esp_err_t rehab_scan_transfer_make_boundary(char *out, size_t out_size)
{
    if (out == NULL || out_size == 0) return ESP_ERR_INVALID_ARG;
    uint32_t a = esp_random();
    uint32_t b = esp_random();
    uint32_t c = esp_random();
    int count = snprintf(out, out_size, "----deltar-p4-%08lx%08lx%08lx",
                         (unsigned long)a, (unsigned long)b, (unsigned long)c);
    return count > 0 && (size_t)count < out_size ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

esp_err_t rehab_scan_transfer_multipart_length(
    const rehab_scan_transfer_plan_t *plan, const char *boundary,
    size_t maximum, size_t *out_length)
{
    if (plan == NULL || boundary == NULL || out_length == NULL ||
        boundary[0] == '\0' || strchr(boundary, '\r') || strchr(boundary, '\n')) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t total = 0;
    for (size_t i = 0; i < plan->part_count; ++i) {
        int header = header_length(&plan->parts[i], boundary);
        if (header <= 0 || plan->parts[i].size > SIZE_MAX ||
            checked_add(&total, (size_t)header) != ESP_OK ||
            checked_add(&total, (size_t)plan->parts[i].size) != ESP_OK ||
            checked_add(&total, 2U) != ESP_OK) return ESP_ERR_INVALID_SIZE;
    }
    size_t closing = strlen(boundary) + 6U; /* -- boundary -- CRLF */
    if (checked_add(&total, closing) != ESP_OK || total > maximum || total > INT_MAX) {
        return ESP_ERR_INVALID_SIZE;
    }
    *out_length = total;
    return ESP_OK;
}

static esp_err_t write_all(rehab_scan_transfer_handle_t handle,
                           esp_http_client_handle_t client,
                           const void *bytes, size_t length)
{
    const uint8_t *cursor = bytes;
    while (length > 0) {
        if (rehab_scan_transfer_abort_requested(handle)) return ESP_ERR_INVALID_STATE;
        size_t request = length > INT_MAX ? INT_MAX : length;
        int written = esp_http_client_write(client, (const char *)cursor, (int)request);
        if (written <= 0 || (size_t)written > request) return ESP_FAIL;
        cursor += written;
        length -= (size_t)written;
        rehab_scan_transfer_add_sent(handle, (size_t)written);
    }
    return ESP_OK;
}

esp_err_t rehab_scan_transfer_write_multipart(
    rehab_scan_transfer_handle_t handle, void *opaque_client,
    const rehab_scan_transfer_plan_t *plan, const char *boundary,
    uint8_t *buffer, size_t buffer_size, size_t expected_length)
{
    if (handle == NULL || opaque_client == NULL || plan == NULL || boundary == NULL ||
        buffer == NULL || buffer_size == 0) return ESP_ERR_INVALID_ARG;
    esp_http_client_handle_t client = opaque_client;
    size_t emitted = 0;
    for (size_t i = 0; i < plan->part_count; ++i) {
        const rehab_scan_transfer_part_t *part = &plan->parts[i];
        struct stat status;
        if (stat(part->path, &status) != 0 || !S_ISREG(status.st_mode) ||
            status.st_size < 0 || (uint64_t)status.st_size != part->size) {
            return ESP_ERR_INVALID_SIZE;
        }
        int header = snprintf((char *)buffer, buffer_size,
            "--%s\r\nContent-Disposition: form-data; name=\"%s\"; "
            "filename=\"%s\"\r\nContent-Type: %s\r\n\r\n",
            boundary, part->field, part->filename, part->content_type);
        if (header <= 0 || (size_t)header >= buffer_size) return ESP_ERR_INVALID_SIZE;
        esp_err_t err = write_all(handle, client, buffer, (size_t)header);
        if (err != ESP_OK) return err;
        emitted += (size_t)header;
        FILE *file = fopen(part->path, "rb");
        if (file == NULL) return ESP_ERR_NOT_FOUND;
        uint64_t remaining = part->size;
        while (remaining > 0) {
            if (rehab_scan_transfer_abort_requested(handle)) {
                fclose(file);
                return ESP_ERR_INVALID_STATE;
            }
            size_t wanted = remaining > buffer_size ? buffer_size : (size_t)remaining;
            size_t got = fread(buffer, 1, wanted, file);
            if (got != wanted) {
                fclose(file);
                return ESP_FAIL;
            }
            err = write_all(handle, client, buffer, got);
            if (err != ESP_OK) {
                fclose(file);
                return err;
            }
            emitted += got;
            remaining -= got;
        }
        if (fgetc(file) != EOF) {
            fclose(file);
            return ESP_ERR_INVALID_SIZE;
        }
        if (fclose(file) != 0) return ESP_FAIL;
        err = write_all(handle, client, "\r\n", 2);
        if (err != ESP_OK) return err;
        emitted += 2;
    }
    int closing = snprintf((char *)buffer, buffer_size, "--%s--\r\n", boundary);
    if (closing <= 0 || (size_t)closing >= buffer_size) return ESP_ERR_INVALID_SIZE;
    esp_err_t err = write_all(handle, client, buffer, (size_t)closing);
    emitted += err == ESP_OK ? (size_t)closing : 0;
    return err == ESP_OK && emitted == expected_length ? ESP_OK :
           err != ESP_OK ? err : ESP_ERR_INVALID_SIZE;
}
