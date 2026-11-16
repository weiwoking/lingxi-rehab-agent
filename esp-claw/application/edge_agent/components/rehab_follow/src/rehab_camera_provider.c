// SPDX-License-Identifier: Apache-2.0

#include "rehab_follow_priv.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/time.h>
#include <unistd.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_video_ioctl.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "linux/videodev2.h"
#include "sdkconfig.h"

#define CAMERA_PROVIDER_TASK_STACK       8192
#define CAMERA_PROVIDER_TASK_PRIORITY    5
#define CAMERA_STATUS_LOG_INTERVAL_US    (10LL * 1000LL * 1000LL)
#define CAMERA_PREVIEW_DRAIN_TIMEOUT_MS  7000
#define CAMERA_OUTPUT_WIDTH              640U
#define CAMERA_OUTPUT_HEIGHT             360U
#define CAMERA_OUTPUT_STRIDE             (CAMERA_OUTPUT_WIDTH * 2U)
#define CAMERA_OUTPUT_PAYLOAD_BYTES      ((size_t)CAMERA_OUTPUT_STRIDE * CAMERA_OUTPUT_HEIGHT)

typedef enum {
    CAMERA_SCALER_UNSUPPORTED = 0,
    CAMERA_SCALER_NATIVE_640X360,
    CAMERA_SCALER_SOFTWARE_2X_NEAREST,
} camera_scaler_mode_t;

typedef enum {
    CAMERA_REQUEST_NONE = 0,
    CAMERA_REQUEST_RAW_ONLY,
    CAMERA_REQUEST_JPEG,
} camera_request_kind_t;

typedef struct {
    uint8_t *allocation;
    uint8_t *data;
    size_t capacity;
    size_t length;
    bool valid;
    bool writing;
    uint32_t lease_count;
    uint64_t generation;
    int64_t timestamp_us;
} camera_raw_slot_t;

typedef struct {
    SemaphoreHandle_t lock;
    StaticSemaphore_t lock_storage;
    SemaphoreHandle_t stopped;
    StaticSemaphore_t stopped_storage;
    SemaphoreHandle_t snapshot_done;
    StaticSemaphore_t snapshot_done_storage;
    bool initialized;
    volatile bool stop_requested;
    TaskHandle_t task;

    char device_path[REHAB_FOLLOW_CAMERA_DEVICE_PATH_MAX];
    uint32_t preview_interval_ms;
    uint32_t capture_timeout_ms;
    uint32_t reopen_backoff_ms;
    uint32_t max_reopen_attempts;
    uint8_t jpeg_quality;

    rehab_follow_camera_state_t state;
    bool owner_conflict;
    char last_error[REHAB_FOLLOW_CAMERA_ERROR_MAX];
    esp_err_t last_esp_err;
    int last_errno;

    int fd;
    bool streaming;
    void *driver_buffers[REHAB_FOLLOW_CAMERA_DRIVER_BUFS];
    size_t driver_buffer_lengths[REHAB_FOLLOW_CAMERA_DRIVER_BUFS];
    uint32_t driver_buffer_count;

    uint32_t width;
    uint32_t height;
    uint32_t pixel_format;
    uint32_t bytesperline;
    uint32_t sizeimage;
    size_t source_payload_bytes;
    camera_scaler_mode_t scaler_mode;

    bool resources_ready;
    camera_raw_slot_t raw_slots[REHAB_FOLLOW_CAMERA_RAW_SLOTS];
    int reserved_raw_slot;
    camera_request_kind_t request_kind;

    uint32_t last_bytesused;
    uint64_t frame_generation;
    uint64_t capture_count;
    uint64_t dq_error_count;
    uint64_t q_error_count;
    uint64_t copy_error_count;
    uint64_t reopen_count;
    uint32_t retry_count;
    int64_t last_frame_timestamp_us;
    uint64_t last_interval_us;
    uint64_t min_interval_us;
    uint64_t max_interval_us;
    uint64_t interval_sum_us;
    uint64_t interval_sample_count;
    int64_t last_status_log_us;
    uint64_t last_jpeg_encode_us;
    uint64_t max_jpeg_encode_us;
    uint64_t jpeg_encode_sum_us;
    uint64_t jpeg_encode_sample_count;
    uint64_t last_request_jpeg_us;
    uint64_t last_loop_us;
    uint64_t max_loop_us;
    uint64_t loop_sum_us;
    uint64_t loop_sample_count;
    uint64_t last_capture_us;
    uint64_t max_capture_us;
    uint64_t capture_sum_us;
    uint64_t capture_sample_count;
    uint64_t last_downscale_us;
    uint64_t max_downscale_us;
    uint64_t downscale_sum_us;
    uint64_t downscale_sample_count;
    bool snapshot_busy;
    bool snapshot_waiter_active;
    bool snapshot_request_pending;
    bool snapshot_active;
    esp_err_t snapshot_result;
    uint64_t snapshot_request_count;
    uint64_t snapshot_success_count;
    uint64_t snapshot_error_count;
    uint64_t raw_snapshot_request_count;
    uint64_t raw_snapshot_success_count;
    uint64_t jpeg_snapshot_request_count;
    uint64_t jpeg_snapshot_success_count;
    int64_t last_snapshot_timestamp_us;
} camera_provider_t;

static const char *TAG = "rehab_camera";
static camera_provider_t s_camera = {
    .fd = -1,
    .reserved_raw_slot = -1,
    .request_kind = CAMERA_REQUEST_NONE,
    .state = REHAB_FOLLOW_CAMERA_UNINITIALIZED,
};

static bool camera_pipeline_quiescent_locked(void)
{
    if (s_camera.snapshot_busy || s_camera.snapshot_waiter_active ||
        s_camera.snapshot_request_pending || s_camera.snapshot_active) {
        return false;
    }
    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_RAW_SLOTS; ++i) {
        if (s_camera.raw_slots[i].writing ||
            s_camera.raw_slots[i].lease_count != 0) {
            return false;
        }
    }
    return true;
}

static void camera_fourcc_to_string(uint32_t pixel_format, char output[5])
{
    output[0] = (char)(pixel_format & 0xffU);
    output[1] = (char)((pixel_format >> 8) & 0xffU);
    output[2] = (char)((pixel_format >> 16) & 0xffU);
    output[3] = (char)((pixel_format >> 24) & 0xffU);
    output[4] = '\0';
    for (size_t i = 0; i < 4; ++i) {
        if ((unsigned char)output[i] < 32U || (unsigned char)output[i] > 126U) {
            output[i] = '.';
        }
    }
}

static const char *camera_scaler_mode_name(camera_scaler_mode_t mode)
{
    return mode == CAMERA_SCALER_NATIVE_640X360
               ? "native_640x360"
               : (mode == CAMERA_SCALER_SOFTWARE_2X_NEAREST
                      ? "software_2x_nearest"
                      : "unsupported");
}

static const char *camera_request_kind_name(camera_request_kind_t kind)
{
    return kind == CAMERA_REQUEST_RAW_ONLY
               ? "raw_only"
               : (kind == CAMERA_REQUEST_JPEG ? "jpeg" : "none");
}

static bool camera_raw_slots_idle_locked(void)
{
    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_RAW_SLOTS; ++i) {
        if (s_camera.raw_slots[i].writing || s_camera.raw_slots[i].lease_count != 0) {
            return false;
        }
    }
    return true;
}

static int camera_find_free_raw_slot_locked(void)
{
    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_RAW_SLOTS; ++i) {
        if (!s_camera.raw_slots[i].writing && s_camera.raw_slots[i].lease_count == 0) {
            return (int)i;
        }
    }
    return -1;
}

static bool camera_module_is_enabled(const char *enabled_modules)
{
#if defined(CONFIG_APP_CLAW_LUA_MODULE_CAMERA) && CONFIG_APP_CLAW_LUA_MODULE_CAMERA && \
    defined(CONFIG_ESP_BOARD_DEV_CAMERA_SUPPORT)
    if (enabled_modules == NULL) {
        return true;
    }

    bool has_explicit_config = false;
    for (const char *scan = enabled_modules; *scan != '\0'; ++scan) {
        if (!isspace((unsigned char)*scan)) {
            has_explicit_config = true;
            break;
        }
    }
    if (!has_explicit_config) {
        return true;
    }

    const char *cursor = enabled_modules;
    while (*cursor != '\0') {
        const char *start = cursor;
        while (*cursor != '\0' && *cursor != ',') {
            ++cursor;
        }
        const char *end = cursor;
        while (start < end && isspace((unsigned char)*start)) {
            ++start;
        }
        while (end > start && isspace((unsigned char)end[-1])) {
            --end;
        }
        if ((size_t)(end - start) == strlen("camera") &&
            strncmp(start, "camera", strlen("camera")) == 0) {
            return true;
        }
        if (*cursor == ',') {
            ++cursor;
        }
    }
    return false;
#else
    (void)enabled_modules;
    return false;
#endif
}

static void camera_set_failure(rehab_follow_camera_state_t state,
                               esp_err_t err,
                               int saved_errno,
                               const char *format,
                               ...)
{
    char message[REHAB_FOLLOW_CAMERA_ERROR_MAX];
    va_list args;
    va_start(args, format);
    (void)vsnprintf(message, sizeof(message), format, args);
    va_end(args);

    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.state = state;
    s_camera.last_esp_err = err;
    s_camera.last_errno = saved_errno;
    strlcpy(s_camera.last_error, message, sizeof(s_camera.last_error));
    uint32_t retry_count = s_camera.retry_count;
    bool fd_valid = s_camera.fd >= 0;
    bool streaming = s_camera.streaming;
    uint32_t buffer_count = s_camera.driver_buffer_count;
    xSemaphoreGive(s_camera.lock);

    ESP_LOGE(TAG,
             "CAM_ERROR state=%s operation=%s esp_err=0x%x errno=%d"
             " retry=%" PRIu32 " fd_valid=%d streaming=%d buffers=%" PRIu32,
             rehab_follow_camera_state_name(state),
             message,
             err,
             saved_errno,
             retry_count,
             fd_valid,
             streaming,
             buffer_count);
}

static void camera_set_state(rehab_follow_camera_state_t state, bool clear_error)
{
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.state = state;
    if (clear_error) {
        s_camera.last_error[0] = '\0';
        s_camera.last_esp_err = ESP_OK;
        s_camera.last_errno = 0;
    }
    xSemaphoreGive(s_camera.lock);
}

static bool camera_stop_requested(void)
{
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    bool requested = s_camera.stop_requested;
    xSemaphoreGive(s_camera.lock);
    return requested;
}

static void camera_log_heap(const char *phase)
{
    ESP_LOGI(TAG,
             "CAM_HEAP phase=%s psram_free=%u psram_min=%u psram_largest=%u"
             " internal_free=%u internal_min=%u internal_largest=%u",
             phase,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
}

static esp_err_t camera_allocate_resources(void)
{
    camera_log_heap("before_slots");
    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_RAW_SLOTS; ++i) {
        if (CAMERA_OUTPUT_PAYLOAD_BYTES > SIZE_MAX - 15U) {
            return ESP_ERR_INVALID_SIZE;
        }
        s_camera.raw_slots[i].allocation = heap_caps_malloc(
            CAMERA_OUTPUT_PAYLOAD_BYTES + 15U,
            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_camera.raw_slots[i].allocation == NULL) {
            for (size_t j = 0; j < i; ++j) {
                heap_caps_free(s_camera.raw_slots[j].allocation);
                memset(&s_camera.raw_slots[j], 0, sizeof(s_camera.raw_slots[j]));
            }
            return ESP_ERR_NO_MEM;
        }
        uintptr_t aligned = ((uintptr_t)s_camera.raw_slots[i].allocation + 15U) & ~(uintptr_t)0x0fU;
        s_camera.raw_slots[i].data = (uint8_t *)aligned;
        s_camera.raw_slots[i].capacity = CAMERA_OUTPUT_PAYLOAD_BYTES;
    }

    ESP_LOGI(TAG, "CAM_RAW_SLOTS count=2 capacity_each=%u storage=psram alignment=16",
             (unsigned)CAMERA_OUTPUT_PAYLOAD_BYTES);

    esp_err_t err = rehab_camera_preview_init(CAMERA_OUTPUT_WIDTH,
                                              CAMERA_OUTPUT_HEIGHT,
                                              CAMERA_OUTPUT_PAYLOAD_BYTES,
                                              false,
                                              s_camera.jpeg_quality);
    if (err != ESP_OK) {
        for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_RAW_SLOTS; ++i) {
            heap_caps_free(s_camera.raw_slots[i].allocation);
            memset(&s_camera.raw_slots[i], 0, sizeof(s_camera.raw_slots[i]));
        }
        return err;
    }
    ESP_LOGI(TAG,
             "CAM_JPEG_SLOTS count=2 width=%u height=%u capacity_each=%u quality=60",
             (unsigned)CAMERA_OUTPUT_WIDTH,
             (unsigned)CAMERA_OUTPUT_HEIGHT,
             (unsigned)CAMERA_OUTPUT_PAYLOAD_BYTES);

    s_camera.reserved_raw_slot = -1;
    s_camera.request_kind = CAMERA_REQUEST_NONE;
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.resources_ready = true;
    xSemaphoreGive(s_camera.lock);
    camera_log_heap("after_slots");
    return ESP_OK;
}

static esp_err_t camera_raw_slots_wait_idle(uint32_t timeout_ms)
{
    TickType_t start = xTaskGetTickCount();
    TickType_t timeout_ticks = pdMS_TO_TICKS(timeout_ms);
    do {
        xSemaphoreTake(s_camera.lock, portMAX_DELAY);
        bool idle = camera_raw_slots_idle_locked();
        xSemaphoreGive(s_camera.lock);
        if (idle) {
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    } while ((xTaskGetTickCount() - start) < timeout_ticks);
    return ESP_ERR_TIMEOUT;
}

static esp_err_t camera_release_resources(void)
{
    if (!s_camera.resources_ready) {
        return ESP_OK;
    }

    esp_err_t err = camera_raw_slots_wait_idle(CAMERA_PREVIEW_DRAIN_TIMEOUT_MS);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "CAM_RESOURCE_RELEASE raw leases did not drain err=0x%x", err);
        return err;
    }
    err = rehab_camera_preview_wait_idle(CAMERA_PREVIEW_DRAIN_TIMEOUT_MS);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "CAM_RESOURCE_RELEASE preview leases did not drain err=0x%x", err);
        return err;
    }
    err = rehab_camera_preview_deinit();
    if (err != ESP_OK) {
        return err;
    }
    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_RAW_SLOTS; ++i) {
        heap_caps_free(s_camera.raw_slots[i].allocation);
        memset(&s_camera.raw_slots[i], 0, sizeof(s_camera.raw_slots[i]));
    }
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.resources_ready = false;
    xSemaphoreGive(s_camera.lock);
    s_camera.reserved_raw_slot = -1;
    s_camera.request_kind = CAMERA_REQUEST_NONE;
    camera_log_heap("after_release");
    return ESP_OK;
}

static void camera_driver_close(void)
{
    int fd;
    bool streaming;
    void *buffers[REHAB_FOLLOW_CAMERA_DRIVER_BUFS];
    size_t lengths[REHAB_FOLLOW_CAMERA_DRIVER_BUFS];

    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    fd = s_camera.fd;
    streaming = s_camera.streaming;
    s_camera.fd = -1;
    s_camera.streaming = false;
    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_DRIVER_BUFS; ++i) {
        buffers[i] = s_camera.driver_buffers[i];
        lengths[i] = s_camera.driver_buffer_lengths[i];
        s_camera.driver_buffers[i] = NULL;
    }
    xSemaphoreGive(s_camera.lock);

    if (fd >= 0 && streaming) {
        int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        if (ioctl(fd, VIDIOC_STREAMOFF, &type) != 0) {
            ESP_LOGW(TAG, "CAM_STREAMOFF errno=%d", errno);
        }
    }

    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_DRIVER_BUFS; ++i) {
        if (buffers[i] != NULL) {
            (void)munmap(buffers[i], lengths[i]);
        }
    }
    if (fd >= 0) {
        (void)close(fd);
    }
}

static esp_err_t camera_adopt_format(const struct v4l2_pix_format *pixel)
{
    if (pixel->width == 0 || pixel->height == 0 ||
        (pixel->pixelformat != V4L2_PIX_FMT_RGB565 &&
         pixel->pixelformat != V4L2_PIX_FMT_RGB565X)) {
        return ESP_ERR_NOT_SUPPORTED;
    }

    uint64_t row_bytes = (uint64_t)pixel->width * 2U;
    uint64_t compact_bytes = row_bytes * pixel->height;
    uint32_t stride = pixel->bytesperline == 0 ? (uint32_t)row_bytes : pixel->bytesperline;
    if (row_bytes > UINT32_MAX || compact_bytes == 0 ||
        compact_bytes > SIZE_MAX || compact_bytes > INT_MAX || stride < row_bytes) {
        return ESP_ERR_INVALID_SIZE;
    }

    camera_scaler_mode_t scaler_mode = CAMERA_SCALER_UNSUPPORTED;
    if (pixel->width == CAMERA_OUTPUT_WIDTH && pixel->height == CAMERA_OUTPUT_HEIGHT) {
        scaler_mode = CAMERA_SCALER_NATIVE_640X360;
    } else if (pixel->width == CAMERA_OUTPUT_WIDTH * 2U &&
               pixel->height == CAMERA_OUTPUT_HEIGHT * 2U) {
        scaler_mode = CAMERA_SCALER_SOFTWARE_2X_NEAREST;
    } else {
        return ESP_ERR_NOT_SUPPORTED;
    }

    if (s_camera.resources_ready &&
        (s_camera.width != pixel->width || s_camera.height != pixel->height ||
         s_camera.pixel_format != pixel->pixelformat ||
         s_camera.source_payload_bytes != (size_t)compact_bytes ||
         s_camera.scaler_mode != scaler_mode)) {
        return ESP_ERR_NOT_SUPPORTED;
    }

    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.width = pixel->width;
    s_camera.height = pixel->height;
    s_camera.pixel_format = pixel->pixelformat;
    s_camera.bytesperline = stride;
    s_camera.sizeimage = pixel->sizeimage;
    s_camera.source_payload_bytes = (size_t)compact_bytes;
    s_camera.scaler_mode = scaler_mode;
    xSemaphoreGive(s_camera.lock);
    return ESP_OK;
}

static esp_err_t camera_driver_open(void)
{
    struct v4l2_format format = {0};
    struct v4l2_requestbuffers request = {0};
    esp_err_t err = ESP_FAIL;

    int opened_fd = open(s_camera.device_path, O_RDWR);
    int open_errno = opened_fd < 0 ? errno : 0;
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.fd = opened_fd;
    xSemaphoreGive(s_camera.lock);
    if (opened_fd < 0) {
        camera_set_failure(REHAB_FOLLOW_CAMERA_STARTING,
                           ESP_ERR_NOT_FOUND,
                           open_errno,
                           "open failed");
        return ESP_ERR_NOT_FOUND;
    }

    format.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(s_camera.fd, VIDIOC_G_FMT, &format) != 0) {
        int saved_errno = errno;
        camera_set_failure(REHAB_FOLLOW_CAMERA_STARTING, ESP_FAIL, saved_errno,
                           "VIDIOC_G_FMT failed");
        goto fail;
    }
    if (ioctl(s_camera.fd, VIDIOC_S_FMT, &format) != 0) {
        int saved_errno = errno;
        camera_set_failure(REHAB_FOLLOW_CAMERA_STARTING, ESP_FAIL, saved_errno,
                           "VIDIOC_S_FMT failed");
        goto fail;
    }

    err = camera_adopt_format(&format.fmt.pix);
    if (err != ESP_OK) {
        camera_set_failure(REHAB_FOLLOW_CAMERA_ERROR, err, 0,
                           "unsupported or changed camera format");
        goto fail;
    }
    err = ESP_FAIL;

    char fourcc[5];
    camera_fourcc_to_string(s_camera.pixel_format, fourcc);
    ESP_LOGI(TAG,
             "CAM_SOURCE path=%s width=%" PRIu32 " height=%" PRIu32
             " fourcc=%s format=RGB565 byte_order=%s"
             " effective_stride=%" PRIu32 " sizeimage=%" PRIu32 " payload=%u",
             s_camera.device_path,
             s_camera.width,
             s_camera.height,
             fourcc,
             s_camera.pixel_format == V4L2_PIX_FMT_RGB565X
                 ? "big_endian"
                 : "little_endian",
             s_camera.bytesperline,
             s_camera.sizeimage,
             (unsigned)s_camera.source_payload_bytes);
    ESP_LOGI(TAG,
             "CAM_OUTPUT width=%u height=%u format=RGB565 byte_order=little_endian"
             " stride=%u payload=%u scaler_mode=%s",
             (unsigned)CAMERA_OUTPUT_WIDTH,
             (unsigned)CAMERA_OUTPUT_HEIGHT,
             (unsigned)CAMERA_OUTPUT_STRIDE,
             (unsigned)CAMERA_OUTPUT_PAYLOAD_BYTES,
             camera_scaler_mode_name(s_camera.scaler_mode));

    request.count = REHAB_FOLLOW_CAMERA_DRIVER_BUFS;
    request.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    request.memory = V4L2_MEMORY_MMAP;
    int request_result = ioctl(s_camera.fd, VIDIOC_REQBUFS, &request);
    if (request_result != 0 || request.count == 0) {
        int saved_errno = request_result != 0 ? errno : 0;
        err = request.count == 0 ? ESP_ERR_NO_MEM : ESP_FAIL;
        camera_set_failure(REHAB_FOLLOW_CAMERA_STARTING, err, saved_errno,
                           "VIDIOC_REQBUFS failed");
        goto fail;
    }
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.driver_buffer_count = request.count > REHAB_FOLLOW_CAMERA_DRIVER_BUFS
                                       ? REHAB_FOLLOW_CAMERA_DRIVER_BUFS
                                       : request.count;
    memset(s_camera.driver_buffer_lengths, 0, sizeof(s_camera.driver_buffer_lengths));
    xSemaphoreGive(s_camera.lock);

    for (uint32_t i = 0; i < s_camera.driver_buffer_count; ++i) {
        struct v4l2_buffer buffer = {0};
        buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buffer.memory = V4L2_MEMORY_MMAP;
        buffer.index = i;
        if (ioctl(s_camera.fd, VIDIOC_QUERYBUF, &buffer) != 0) {
            int saved_errno = errno;
            camera_set_failure(REHAB_FOLLOW_CAMERA_STARTING, ESP_FAIL, saved_errno,
                               "VIDIOC_QUERYBUF failed");
            goto fail;
        }
        void *mapped = mmap(NULL,
                            buffer.length,
                            PROT_READ | PROT_WRITE,
                            MAP_SHARED,
                            s_camera.fd,
                            buffer.m.offset);
        if (mapped == MAP_FAILED) {
            int saved_errno = errno;
            camera_set_failure(REHAB_FOLLOW_CAMERA_NO_MEMORY, ESP_ERR_NO_MEM,
                               saved_errno, "mmap failed");
            err = ESP_ERR_NO_MEM;
            goto fail;
        }
        xSemaphoreTake(s_camera.lock, portMAX_DELAY);
        s_camera.driver_buffers[i] = mapped;
        s_camera.driver_buffer_lengths[i] = buffer.length;
        xSemaphoreGive(s_camera.lock);
        ESP_LOGI(TAG, "CAM_BUF index=%" PRIu32 " length=%u",
                 i, (unsigned)buffer.length);
    }

    if (!s_camera.resources_ready) {
        err = camera_allocate_resources();
        if (err != ESP_OK) {
            camera_set_failure(REHAB_FOLLOW_CAMERA_NO_MEMORY, err, 0,
                               "PSRAM or JPEG slot allocation failed");
            goto fail;
        }
        err = ESP_FAIL;
    }

    return ESP_OK;

fail:
    camera_driver_close();
    return err;
}

static esp_err_t camera_stream_start(void)
{
    for (uint32_t i = 0; i < s_camera.driver_buffer_count; ++i) {
        struct v4l2_buffer buffer = {0};
        buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buffer.memory = V4L2_MEMORY_MMAP;
        buffer.index = i;
        if (ioctl(s_camera.fd, VIDIOC_QBUF, &buffer) != 0) {
            int saved_errno = errno;
            xSemaphoreTake(s_camera.lock, portMAX_DELAY);
            ++s_camera.q_error_count;
            xSemaphoreGive(s_camera.lock);
            camera_set_failure(REHAB_FOLLOW_CAMERA_RUNNING, ESP_FAIL, saved_errno,
                               "snapshot VIDIOC_QBUF failed");
            return ESP_FAIL;
        }
    }

    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(s_camera.fd, VIDIOC_STREAMON, &type) != 0) {
        int saved_errno = errno;
        camera_set_failure(REHAB_FOLLOW_CAMERA_RUNNING, ESP_FAIL, saved_errno,
                           "snapshot VIDIOC_STREAMON failed");
        return ESP_FAIL;
    }
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.streaming = true;
    xSemaphoreGive(s_camera.lock);
    return ESP_OK;
}

static esp_err_t camera_stream_stop(void)
{
    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(s_camera.fd, VIDIOC_STREAMOFF, &type) != 0) {
        int saved_errno = errno;
        camera_set_failure(REHAB_FOLLOW_CAMERA_RUNNING, ESP_FAIL, saved_errno,
                           "snapshot VIDIOC_STREAMOFF failed");
        return ESP_FAIL;
    }
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.streaming = false;
    xSemaphoreGive(s_camera.lock);
    return ESP_OK;
}

static esp_err_t camera_normalize_output(const struct v4l2_buffer *buffer,
                                         camera_raw_slot_t *slot)
{
    if (buffer->index >= s_camera.driver_buffer_count ||
        s_camera.driver_buffers[buffer->index] == NULL ||
        buffer->bytesused == 0 ||
        buffer->bytesused > s_camera.driver_buffer_lengths[buffer->index] ||
        slot == NULL || slot->data == NULL ||
        slot->capacity < CAMERA_OUTPUT_PAYLOAD_BYTES) {
        return ESP_ERR_INVALID_SIZE;
    }

    size_t source_row_bytes = (size_t)s_camera.width * 2U;
    size_t stride = s_camera.bytesperline;
    if (stride < source_row_bytes) {
        return ESP_ERR_INVALID_SIZE;
    }

    size_t step = s_camera.scaler_mode == CAMERA_SCALER_SOFTWARE_2X_NEAREST ? 2U : 1U;
    size_t last_row = (CAMERA_OUTPUT_HEIGHT - 1U) * step;
    size_t last_pixel_end = (((CAMERA_OUTPUT_WIDTH - 1U) * step) + 1U) * 2U;
    if (last_row > (SIZE_MAX - last_pixel_end) / stride) {
        return ESP_ERR_INVALID_SIZE;
    }
    size_t required = last_row * stride + last_pixel_end;
    if (buffer->bytesused < required ||
        s_camera.driver_buffer_lengths[buffer->index] < required) {
        return ESP_ERR_INVALID_SIZE;
    }

    const uint8_t *source = s_camera.driver_buffers[buffer->index];
    bool swap_bytes = s_camera.pixel_format == V4L2_PIX_FMT_RGB565X;
    for (size_t output_y = 0; output_y < CAMERA_OUTPUT_HEIGHT; ++output_y) {
        const uint8_t *source_row = source + output_y * step * stride;
        uint8_t *output_row = slot->data + output_y * CAMERA_OUTPUT_STRIDE;
        if (step == 1U && !swap_bytes) {
            memcpy(output_row, source_row, CAMERA_OUTPUT_STRIDE);
            continue;
        }
        for (size_t output_x = 0; output_x < CAMERA_OUTPUT_WIDTH; ++output_x) {
            const uint8_t *source_pixel = source_row + output_x * step * 2U;
            uint8_t *output_pixel = output_row + output_x * 2U;
            if (swap_bytes) {
                output_pixel[0] = source_pixel[1];
                output_pixel[1] = source_pixel[0];
            } else {
                output_pixel[0] = source_pixel[0];
                output_pixel[1] = source_pixel[1];
            }
        }
    }
    slot->length = CAMERA_OUTPUT_PAYLOAD_BYTES;
    return ESP_OK;
}

static void camera_publish_raw(camera_raw_slot_t *slot,
                               uint32_t bytesused,
                               int64_t timestamp_us)
{
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    ++s_camera.frame_generation;
    slot->generation = s_camera.frame_generation;
    slot->timestamp_us = timestamp_us;
    slot->valid = true;
    s_camera.last_bytesused = bytesused;
    ++s_camera.capture_count;

    if (s_camera.last_frame_timestamp_us > 0 &&
        timestamp_us > s_camera.last_frame_timestamp_us) {
        uint64_t interval = (uint64_t)(timestamp_us - s_camera.last_frame_timestamp_us);
        s_camera.last_interval_us = interval;
        if (s_camera.min_interval_us == 0 || interval < s_camera.min_interval_us) {
            s_camera.min_interval_us = interval;
        }
        if (interval > s_camera.max_interval_us) {
            s_camera.max_interval_us = interval;
        }
        if (UINT64_MAX - s_camera.interval_sum_us < interval) {
            s_camera.interval_sum_us = interval;
            s_camera.interval_sample_count = 1;
        } else {
            s_camera.interval_sum_us += interval;
            ++s_camera.interval_sample_count;
        }
    }
    s_camera.last_frame_timestamp_us = timestamp_us;
    xSemaphoreGive(s_camera.lock);
}

static void camera_record_timing(uint64_t sample_us,
                                 uint64_t *last_us,
                                 uint64_t *max_us,
                                 uint64_t *sum_us,
                                 uint64_t *sample_count)
{
    *last_us = sample_us;
    if (sample_us > *max_us) {
        *max_us = sample_us;
    }
    if (UINT64_MAX - *sum_us < sample_us || *sample_count == UINT64_MAX) {
        *sum_us = sample_us;
        *sample_count = 1;
    } else {
        *sum_us += sample_us;
        ++(*sample_count);
    }
}

static void camera_log_periodic_status(int64_t now_us)
{
    if (s_camera.last_status_log_us != 0 &&
        now_us - s_camera.last_status_log_us < CAMERA_STATUS_LOG_INTERVAL_US) {
        return;
    }
    s_camera.last_status_log_us = now_us;

    rehab_follow_camera_status_t status;
    if (rehab_follow_camera_get_status(&status) == ESP_OK) {
        ESP_LOGI(TAG,
                 "CAM_STATUS state=%s generation=%" PRIu64
                 " captures=%" PRIu64 " jpeg=%" PRIu64
                 " drops=%" PRIu64 " dq_err=%" PRIu64
                 " q_err=%" PRIu64 " interval_us=%" PRIu64 " fps_milli=%" PRIu64
                 " jpeg_us_latest=%" PRIu64 " jpeg_us_max=%" PRIu64
                 " jpeg_us_avg=%" PRIu64 " loop_us_latest=%" PRIu64
                 " loop_us_max=%" PRIu64 " loop_us_avg=%" PRIu64
                 " psram_free=%u psram_min=%u psram_largest=%u"
                 " internal_free=%u internal_min=%u internal_largest=%u",
                 rehab_follow_camera_state_name(status.state),
                 status.frame_generation,
                 status.capture_count,
                 status.jpeg_publish_count,
                 status.preview_drop_count,
                 status.dq_error_count,
                 status.q_error_count,
                 status.last_interval_us,
                 status.last_interval_us == 0 ? 0 : 1000000000ULL / status.last_interval_us,
                 s_camera.last_jpeg_encode_us,
                 s_camera.max_jpeg_encode_us,
                 s_camera.jpeg_encode_sample_count == 0
                     ? 0
                     : s_camera.jpeg_encode_sum_us / s_camera.jpeg_encode_sample_count,
                 s_camera.last_loop_us,
                 s_camera.max_loop_us,
                 s_camera.loop_sample_count == 0
                     ? 0
                     : s_camera.loop_sum_us / s_camera.loop_sample_count,
                 (unsigned)status.psram_free_bytes,
                 (unsigned)status.psram_min_free_bytes,
                 (unsigned)status.psram_largest_free_block,
                 (unsigned)status.internal_free_bytes,
                 (unsigned)status.internal_min_free_bytes,
                 (unsigned)status.internal_largest_free_block);
    }
}

static esp_err_t camera_capture_raw(camera_raw_slot_t **captured_slot,
                                    int64_t capture_start_us,
                                    uint64_t *capture_us,
                                    uint64_t *downscale_us)
{
    *captured_slot = NULL;
    struct timeval timeout = {
        .tv_sec = (time_t)(s_camera.capture_timeout_ms / 1000U),
        .tv_usec = (suseconds_t)((s_camera.capture_timeout_ms % 1000U) * 1000U),
    };
    if (ioctl(s_camera.fd, VIDIOC_S_DQBUF_TIMEOUT, &timeout) != 0) {
        int saved_errno = errno;
        if (camera_stop_requested()) {
            return ESP_ERR_INVALID_STATE;
        }
        xSemaphoreTake(s_camera.lock, portMAX_DELAY);
        ++s_camera.dq_error_count;
        xSemaphoreGive(s_camera.lock);
        camera_set_failure(REHAB_FOLLOW_CAMERA_RUNNING, ESP_FAIL, saved_errno,
                           "VIDIOC_S_DQBUF_TIMEOUT failed");
        return ESP_FAIL;
    }

    struct v4l2_buffer buffer = {0};
    buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buffer.memory = V4L2_MEMORY_MMAP;
    if (ioctl(s_camera.fd, VIDIOC_DQBUF, &buffer) != 0) {
        int saved_errno = errno;
        if (camera_stop_requested()) {
            return ESP_ERR_INVALID_STATE;
        }
        xSemaphoreTake(s_camera.lock, portMAX_DELAY);
        ++s_camera.dq_error_count;
        xSemaphoreGive(s_camera.lock);
        camera_set_failure(REHAB_FOLLOW_CAMERA_RUNNING,
                           saved_errno == ETIMEDOUT ? ESP_ERR_TIMEOUT : ESP_FAIL,
                           saved_errno,
                           "VIDIOC_DQBUF failed");
        return saved_errno == ETIMEDOUT ? ESP_ERR_TIMEOUT : ESP_FAIL;
    }

    int64_t timestamp_us = esp_timer_get_time();
    *capture_us = (uint64_t)(timestamp_us - capture_start_us);
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.last_bytesused = buffer.bytesused;
    int reserved_index = s_camera.reserved_raw_slot;
    xSemaphoreGive(s_camera.lock);
    if (reserved_index < 0 || reserved_index >= (int)REHAB_FOLLOW_CAMERA_RAW_SLOTS) {
        (void)ioctl(s_camera.fd, VIDIOC_QBUF, &buffer);
        return ESP_ERR_INVALID_STATE;
    }
    camera_raw_slot_t *slot = &s_camera.raw_slots[reserved_index];
    int64_t downscale_start_us = esp_timer_get_time();
    esp_err_t copy_err = camera_normalize_output(&buffer, slot);
    int64_t downscale_end_us = esp_timer_get_time();
    *downscale_us = (uint64_t)(downscale_end_us - downscale_start_us);

    int q_result = ioctl(s_camera.fd, VIDIOC_QBUF, &buffer);
    int q_errno = q_result == 0 ? 0 : errno;
    if (q_result != 0) {
        xSemaphoreTake(s_camera.lock, portMAX_DELAY);
        ++s_camera.q_error_count;
        xSemaphoreGive(s_camera.lock);
        camera_set_failure(REHAB_FOLLOW_CAMERA_RUNNING, ESP_FAIL, q_errno,
                           "VIDIOC_QBUF failed");
        return ESP_FAIL;
    }
    if (copy_err != ESP_OK) {
        xSemaphoreTake(s_camera.lock, portMAX_DELAY);
        ++s_camera.copy_error_count;
        xSemaphoreGive(s_camera.lock);
        camera_set_failure(REHAB_FOLLOW_CAMERA_RUNNING, copy_err, 0,
                           "invalid bytesused or output normalization failed");
        return copy_err;
    }

    camera_publish_raw(slot, buffer.bytesused, timestamp_us);
    *captured_slot = slot;
    return ESP_OK;
}

static esp_err_t camera_capture_snapshot(bool *driver_failed)
{
    *driver_failed = false;
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.last_request_jpeg_us = 0;
    camera_request_kind_t request_kind = s_camera.request_kind;
    xSemaphoreGive(s_camera.lock);
    int64_t snapshot_start_us = esp_timer_get_time();
    uint64_t capture_us = 0;
    uint64_t downscale_us = 0;
    camera_raw_slot_t *slot = NULL;
    esp_err_t result = camera_stream_start();
    if (result == ESP_OK) {
        result = camera_capture_raw(&slot, snapshot_start_us, &capture_us, &downscale_us);
        esp_err_t stop_err = camera_stream_stop();
        if (result == ESP_OK && stop_err != ESP_OK) {
            result = stop_err;
        }
        if (result != ESP_OK) {
            *driver_failed = true;
        }
    } else {
        *driver_failed = true;
    }

    if (result == ESP_OK && request_kind == CAMERA_REQUEST_JPEG) {
        int64_t jpeg_start_us = esp_timer_get_time();
        result = rehab_camera_preview_encode(slot->data,
                                             slot->length,
                                             slot->generation,
                                             slot->timestamp_us);
        int64_t jpeg_end_us = esp_timer_get_time();
        xSemaphoreTake(s_camera.lock, portMAX_DELAY);
        camera_record_timing((uint64_t)(jpeg_end_us - jpeg_start_us),
                             &s_camera.last_jpeg_encode_us,
                             &s_camera.max_jpeg_encode_us,
                             &s_camera.jpeg_encode_sum_us,
                             &s_camera.jpeg_encode_sample_count);
        s_camera.last_request_jpeg_us = (uint64_t)(jpeg_end_us - jpeg_start_us);
        xSemaphoreGive(s_camera.lock);
        if (result != ESP_OK) {
            camera_set_failure(REHAB_FOLLOW_CAMERA_RUNNING, result, 0,
                               "JPEG preview encode failed");
        }
    }

    int64_t snapshot_end_us = esp_timer_get_time();
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    if (capture_us != 0) {
        camera_record_timing(capture_us,
                             &s_camera.last_capture_us,
                             &s_camera.max_capture_us,
                             &s_camera.capture_sum_us,
                             &s_camera.capture_sample_count);
    }
    if (downscale_us != 0) {
        camera_record_timing(downscale_us,
                             &s_camera.last_downscale_us,
                             &s_camera.max_downscale_us,
                             &s_camera.downscale_sum_us,
                             &s_camera.downscale_sample_count);
    }
    camera_record_timing((uint64_t)(snapshot_end_us - snapshot_start_us),
                         &s_camera.last_loop_us,
                         &s_camera.max_loop_us,
                         &s_camera.loop_sum_us,
                         &s_camera.loop_sample_count);
    xSemaphoreGive(s_camera.lock);
    size_t output_length = slot == NULL ? 0U : slot->length;
    const char *output_format = "rgb565";
    if (request_kind == CAMERA_REQUEST_JPEG) {
        rehab_camera_preview_status_t preview = {0};
        output_format = "jpeg";
        output_length = result == ESP_OK &&
                        rehab_camera_preview_get_status(&preview) == ESP_OK
            ? preview.active_length : 0U;
    }
    ESP_LOGI(TAG,
             "CAM_SNAPSHOT kind=%s result=0x%x generation=%" PRIu64
             " capture_us=%" PRIu64 " downscale_us=%" PRIu64
             " jpeg_us=%" PRIu64 " total_us=%" PRIu64
             " output_length=%u output_format=%s scaler_mode=%s",
             camera_request_kind_name(request_kind),
             result,
             slot == NULL ? s_camera.frame_generation : slot->generation,
             capture_us,
             downscale_us,
             s_camera.last_request_jpeg_us,
             s_camera.last_loop_us,
             (unsigned)output_length,
             output_format,
             camera_scaler_mode_name(s_camera.scaler_mode));
    camera_log_periodic_status(snapshot_end_us);
    return result;
}

static void camera_complete_snapshot(esp_err_t result)
{
    bool notify_waiter;
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.snapshot_result = result;
    s_camera.last_snapshot_timestamp_us = s_camera.last_frame_timestamp_us;
    notify_waiter = s_camera.snapshot_waiter_active;
    if (notify_waiter) {
        if (result == ESP_OK) {
            ++s_camera.snapshot_success_count;
            if (s_camera.request_kind == CAMERA_REQUEST_RAW_ONLY) {
                ++s_camera.raw_snapshot_success_count;
            } else if (s_camera.request_kind == CAMERA_REQUEST_JPEG) {
                ++s_camera.jpeg_snapshot_success_count;
            }
        } else {
            ++s_camera.snapshot_error_count;
        }
    } else {
        if (s_camera.reserved_raw_slot >= 0 &&
            s_camera.reserved_raw_slot < (int)REHAB_FOLLOW_CAMERA_RAW_SLOTS) {
            s_camera.raw_slots[s_camera.reserved_raw_slot].writing = false;
        }
        s_camera.reserved_raw_slot = -1;
        s_camera.request_kind = CAMERA_REQUEST_NONE;
        s_camera.snapshot_busy = false;
    }
    s_camera.snapshot_active = false;
    xSemaphoreGive(s_camera.lock);
    if (notify_waiter) {
        xSemaphoreGive(s_camera.snapshot_done);
    }
}

static void camera_provider_task(void *argument)
{
    (void)argument;
    uint32_t reopen_attempts = 0;

    while (!camera_stop_requested()) {
        camera_set_state(REHAB_FOLLOW_CAMERA_STARTING, false);
        esp_err_t err = camera_driver_open();
        if (err == ESP_OK) {
            camera_set_state(REHAB_FOLLOW_CAMERA_RUNNING, true);
            while (!camera_stop_requested()) {
                (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
                if (camera_stop_requested()) {
                    break;
                }
                xSemaphoreTake(s_camera.lock, portMAX_DELAY);
                s_camera.snapshot_request_pending = false;
                s_camera.snapshot_active = true;
                xSemaphoreGive(s_camera.lock);
                bool driver_failed = false;
                err = camera_capture_snapshot(&driver_failed);
                camera_complete_snapshot(err);
                if (driver_failed) {
                    break;
                }
            }
        }
        camera_driver_close();

        if (camera_stop_requested()) {
            break;
        }
        if (err == ESP_ERR_NO_MEM) {
            camera_set_state(REHAB_FOLLOW_CAMERA_NO_MEMORY, false);
            break;
        }
        if (reopen_attempts >= s_camera.max_reopen_attempts) {
            camera_set_failure(REHAB_FOLLOW_CAMERA_ERROR, err, s_camera.last_errno,
                               "camera reopen limit reached");
            break;
        }

        ++reopen_attempts;
        xSemaphoreTake(s_camera.lock, portMAX_DELAY);
        s_camera.retry_count = reopen_attempts;
        ++s_camera.reopen_count;
        xSemaphoreGive(s_camera.lock);
        ESP_LOGW(TAG, "CAM_REOPEN attempt=%" PRIu32 " max=%" PRIu32,
                 reopen_attempts, s_camera.max_reopen_attempts);
        camera_set_state(REHAB_FOLLOW_CAMERA_STARTING, false);
        vTaskDelay(pdMS_TO_TICKS(s_camera.reopen_backoff_ms));
    }

    camera_driver_close();
    esp_err_t release_err = camera_release_resources();
    if (release_err != ESP_OK && !camera_stop_requested()) {
        camera_set_failure(REHAB_FOLLOW_CAMERA_ERROR, release_err, 0,
                           "preview leases prevented resource release");
    } else if (camera_stop_requested()) {
        camera_set_state(REHAB_FOLLOW_CAMERA_STOPPED, false);
    }

    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.snapshot_request_pending = false;
    s_camera.snapshot_active = false;
    s_camera.snapshot_busy = false;
    s_camera.snapshot_waiter_active = false;
    s_camera.task = NULL;
    xSemaphoreGive(s_camera.lock);
    xSemaphoreGive(s_camera.stopped);
    vTaskDelete(NULL);
}

esp_err_t rehab_follow_camera_init(const rehab_follow_camera_config_t *config)
{
    if (config == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_camera.lock == NULL) {
        s_camera.lock = xSemaphoreCreateMutexStatic(&s_camera.lock_storage);
        s_camera.stopped = xSemaphoreCreateBinaryStatic(&s_camera.stopped_storage);
        s_camera.snapshot_done = xSemaphoreCreateBinaryStatic(&s_camera.snapshot_done_storage);
        if (s_camera.lock == NULL || s_camera.stopped == NULL ||
            s_camera.snapshot_done == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }

    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    if (s_camera.initialized) {
        xSemaphoreGive(s_camera.lock);
        return ESP_ERR_INVALID_STATE;
    }
    s_camera.initialized = true;
    s_camera.stop_requested = false;
    s_camera.state = REHAB_FOLLOW_CAMERA_UNINITIALIZED;
    s_camera.owner_conflict = camera_module_is_enabled(config->enabled_lua_modules);
    s_camera.preview_interval_ms = config->preview_interval_ms == 0 ? 500 : config->preview_interval_ms;
    s_camera.capture_timeout_ms = config->capture_timeout_ms == 0 ? 1000 : config->capture_timeout_ms;
    s_camera.reopen_backoff_ms = config->reopen_backoff_ms == 0 ? 1000 : config->reopen_backoff_ms;
    s_camera.max_reopen_attempts = config->max_reopen_attempts;
    s_camera.jpeg_quality = 60;
    s_camera.last_error[0] = '\0';
    s_camera.last_esp_err = ESP_OK;
    s_camera.last_errno = 0;
    s_camera.snapshot_busy = false;
    s_camera.snapshot_waiter_active = false;
    s_camera.snapshot_request_pending = false;
    s_camera.snapshot_active = false;
    s_camera.snapshot_result = ESP_ERR_INVALID_STATE;
    s_camera.reserved_raw_slot = -1;
    s_camera.request_kind = CAMERA_REQUEST_NONE;
    if (config->device_path != NULL) {
        strlcpy(s_camera.device_path, config->device_path, sizeof(s_camera.device_path));
    } else {
        s_camera.device_path[0] = '\0';
    }
    xSemaphoreGive(s_camera.lock);

    if (config->device_path != NULL &&
        strlen(config->device_path) >= sizeof(s_camera.device_path)) {
        camera_set_failure(REHAB_FOLLOW_CAMERA_ERROR, ESP_ERR_INVALID_SIZE, 0,
                           "camera device path too long");
        return ESP_ERR_INVALID_SIZE;
    }
    if (s_camera.device_path[0] == '\0') {
        camera_set_failure(REHAB_FOLLOW_CAMERA_ERROR, ESP_ERR_NOT_FOUND, 0,
                           "camera device path unavailable");
        return ESP_ERR_NOT_FOUND;
    }
    if (s_camera.owner_conflict) {
        camera_set_failure(REHAB_FOLLOW_CAMERA_CONFLICT, ESP_ERR_INVALID_STATE, 0,
                           "Camera Lua module enabled; disable module 'camera' and restart");
        ESP_LOGE(TAG,
                 "CAM_OWNER_CHECK camera_lua=enabled conflict=1"
                 " required_action=disable_module:camera");
        return ESP_OK;
    }

    camera_set_state(REHAB_FOLLOW_CAMERA_STOPPED, true);
    ESP_LOGI(TAG, "CAM_OWNER_CHECK camera_lua=disabled conflict=0 provider=rehab_follow");
    return ESP_OK;
}

esp_err_t rehab_follow_camera_start(void)
{
    if (s_camera.lock == NULL || !s_camera.initialized) {
        return ESP_ERR_INVALID_STATE;
    }

    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    if (s_camera.owner_conflict || s_camera.device_path[0] == '\0') {
        xSemaphoreGive(s_camera.lock);
        return ESP_ERR_INVALID_STATE;
    }
    if (s_camera.task != NULL) {
        xSemaphoreGive(s_camera.lock);
        return ESP_OK;
    }
    s_camera.stop_requested = false;
    s_camera.retry_count = 0;
    while (xSemaphoreTake(s_camera.stopped, 0) == pdTRUE) {
    }
    while (xSemaphoreTake(s_camera.snapshot_done, 0) == pdTRUE) {
    }
    s_camera.state = REHAB_FOLLOW_CAMERA_STARTING;
    s_camera.task = (TaskHandle_t)1;
    xSemaphoreGive(s_camera.lock);

    ESP_LOGI(TAG,
             "CAM_PROVIDER_START path=%s preview_interval_ms=%" PRIu32
             " timeout_ms=%" PRIu32 " reopen_max=%" PRIu32
             " backoff_ms=%" PRIu32 " jpeg_quality=%u",
             s_camera.device_path,
             s_camera.preview_interval_ms,
             s_camera.capture_timeout_ms,
             s_camera.max_reopen_attempts,
             s_camera.reopen_backoff_ms,
             (unsigned)s_camera.jpeg_quality);

    TaskHandle_t created_task = NULL;
    BaseType_t created = xTaskCreate(camera_provider_task,
                                     "cam_provider",
                                     CAMERA_PROVIDER_TASK_STACK,
                                     NULL,
                                     CAMERA_PROVIDER_TASK_PRIORITY,
                                     &created_task);
    if (created != pdPASS) {
        xSemaphoreTake(s_camera.lock, portMAX_DELAY);
        s_camera.task = NULL;
        xSemaphoreGive(s_camera.lock);
        camera_set_failure(REHAB_FOLLOW_CAMERA_NO_MEMORY, ESP_ERR_NO_MEM, 0,
                           "camera provider task creation failed");
        return ESP_ERR_NO_MEM;
    }
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    if (s_camera.task == (TaskHandle_t)1) {
        s_camera.task = created_task;
    }
    xSemaphoreGive(s_camera.lock);

    return ESP_OK;
}

esp_err_t rehab_follow_camera_stop(void)
{
    if (s_camera.lock == NULL || !s_camera.initialized) {
        return ESP_ERR_INVALID_STATE;
    }

    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    TaskHandle_t task = s_camera.task;
    bool running = task != NULL;
    s_camera.stop_requested = true;
    if (running) {
        s_camera.state = REHAB_FOLLOW_CAMERA_STOPPING;
    }
    xSemaphoreGive(s_camera.lock);
    if (!running) {
        camera_set_state(REHAB_FOLLOW_CAMERA_STOPPED, false);
        return ESP_OK;
    }
    if (task != (TaskHandle_t)1) {
        xTaskNotifyGive(task);
    }

    uint32_t wait_ms = s_camera.capture_timeout_ms + CAMERA_PREVIEW_DRAIN_TIMEOUT_MS + 2000U;
    if (xSemaphoreTake(s_camera.stopped, pdMS_TO_TICKS(wait_ms)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

void rehab_follow_camera_deinit(void)
{
    if (s_camera.lock == NULL || !s_camera.initialized) {
        return;
    }
    if (rehab_follow_camera_stop() != ESP_OK) {
        ESP_LOGE(TAG, "CAM_DEINIT refused while provider task or preview lease is active");
        return;
    }
    if (camera_release_resources() != ESP_OK) {
        ESP_LOGE(TAG, "CAM_DEINIT retained leased preview resources");
        return;
    }

    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    s_camera.initialized = false;
    s_camera.state = REHAB_FOLLOW_CAMERA_UNINITIALIZED;
    xSemaphoreGive(s_camera.lock);
}

bool rehab_follow_camera_is_ready(void)
{
    if (s_camera.lock == NULL) {
        return false;
    }
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    bool ready = s_camera.initialized &&
                 s_camera.state == REHAB_FOLLOW_CAMERA_RUNNING &&
                 s_camera.fd >= 0 && s_camera.task != NULL;
    xSemaphoreGive(s_camera.lock);
    return ready;
}

static esp_err_t camera_snapshot_begin(camera_request_kind_t request_kind,
                                       TaskHandle_t *task)
{
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    *task = s_camera.task;
    bool ready = s_camera.initialized &&
                 s_camera.state == REHAB_FOLLOW_CAMERA_RUNNING &&
                 s_camera.fd >= 0 && *task != NULL && *task != (TaskHandle_t)1;
    if (!ready) {
        xSemaphoreGive(s_camera.lock);
        return ESP_ERR_INVALID_STATE;
    }
    if (s_camera.snapshot_busy) {
        xSemaphoreGive(s_camera.lock);
        return ESP_ERR_NOT_FINISHED;
    }
    int slot_index = camera_find_free_raw_slot_locked();
    if (slot_index < 0) {
        xSemaphoreGive(s_camera.lock);
        return ESP_ERR_NOT_FINISHED;
    }
    while (xSemaphoreTake(s_camera.snapshot_done, 0) == pdTRUE) {
    }
    camera_raw_slot_t *slot = &s_camera.raw_slots[slot_index];
    slot->writing = true;
    slot->valid = false;
    slot->length = 0;
    s_camera.reserved_raw_slot = slot_index;
    s_camera.request_kind = request_kind;
    s_camera.snapshot_busy = true;
    s_camera.snapshot_waiter_active = true;
    s_camera.snapshot_request_pending = true;
    s_camera.snapshot_result = ESP_ERR_NOT_FINISHED;
    ++s_camera.snapshot_request_count;
    if (request_kind == CAMERA_REQUEST_RAW_ONLY) {
        ++s_camera.raw_snapshot_request_count;
    } else {
        ++s_camera.jpeg_snapshot_request_count;
    }
    xSemaphoreGive(s_camera.lock);
    return ESP_OK;
}

static esp_err_t camera_snapshot_wait(uint32_t timeout_ms,
                                      camera_request_kind_t request_kind,
                                      rehab_follow_camera_raw_frame_t *raw_frame)
{
    TickType_t timeout_ticks = pdMS_TO_TICKS(timeout_ms);
    if (timeout_ticks == 0) {
        timeout_ticks = 1;
    }
    bool signaled = xSemaphoreTake(s_camera.snapshot_done, timeout_ticks) == pdTRUE;

    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    esp_err_t result = s_camera.snapshot_result;
    if (!signaled && result == ESP_ERR_NOT_FINISHED) {
        s_camera.snapshot_waiter_active = false;
        ++s_camera.snapshot_error_count;
        xSemaphoreGive(s_camera.lock);
        return ESP_ERR_TIMEOUT;
    }

    s_camera.snapshot_waiter_active = false;
    int slot_index = s_camera.reserved_raw_slot;
    if (result == ESP_OK && request_kind == CAMERA_REQUEST_RAW_ONLY &&
        raw_frame != NULL && slot_index >= 0 &&
        slot_index < (int)REHAB_FOLLOW_CAMERA_RAW_SLOTS) {
        camera_raw_slot_t *slot = &s_camera.raw_slots[slot_index];
        if (!slot->valid || slot->length != CAMERA_OUTPUT_PAYLOAD_BYTES ||
            slot->lease_count == UINT32_MAX) {
            result = ESP_FAIL;
        } else {
            ++slot->lease_count;
            raw_frame->data = slot->data;
            raw_frame->length = slot->length;
            raw_frame->width = CAMERA_OUTPUT_WIDTH;
            raw_frame->height = CAMERA_OUTPUT_HEIGHT;
            raw_frame->stride = CAMERA_OUTPUT_STRIDE;
            raw_frame->pixel_format = REHAB_FOLLOW_CAMERA_PIXEL_FORMAT_RGB565;
            raw_frame->byte_order = REHAB_FOLLOW_CAMERA_BYTE_ORDER_LITTLE_ENDIAN;
            raw_frame->generation = slot->generation;
            raw_frame->timestamp_us = slot->timestamp_us;
            raw_frame->lease_token = (uint32_t)slot_index + 1U;
        }
    }
    if (slot_index >= 0 && slot_index < (int)REHAB_FOLLOW_CAMERA_RAW_SLOTS) {
        s_camera.raw_slots[slot_index].writing = false;
    }
    s_camera.reserved_raw_slot = -1;
    s_camera.request_kind = CAMERA_REQUEST_NONE;
    s_camera.snapshot_busy = false;
    xSemaphoreGive(s_camera.lock);
    return result;
}

esp_err_t rehab_follow_camera_snapshot_acquire(rehab_follow_preview_frame_t *frame,
                                               uint32_t timeout_ms)
{
    if (frame == NULL || timeout_ms == 0 || s_camera.lock == NULL ||
        s_camera.snapshot_done == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(frame, 0, sizeof(*frame));

    TaskHandle_t task = NULL;
    esp_err_t result = camera_snapshot_begin(CAMERA_REQUEST_JPEG, &task);
    if (result != ESP_OK) {
        return result;
    }

    xTaskNotifyGive(task);
    result = camera_snapshot_wait(timeout_ms, CAMERA_REQUEST_JPEG, NULL);
    if (result == ESP_OK) {
        result = rehab_follow_camera_preview_acquire(frame);
    }
    return result;
}

esp_err_t rehab_follow_camera_raw_snapshot_acquire(
    rehab_follow_camera_raw_frame_t *frame,
    uint32_t timeout_ms)
{
    if (frame == NULL || timeout_ms == 0 || s_camera.lock == NULL ||
        s_camera.snapshot_done == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(frame, 0, sizeof(*frame));

    TaskHandle_t task = NULL;
    esp_err_t result = camera_snapshot_begin(CAMERA_REQUEST_RAW_ONLY, &task);
    if (result != ESP_OK) {
        return result;
    }
    xTaskNotifyGive(task);
    result = camera_snapshot_wait(timeout_ms, CAMERA_REQUEST_RAW_ONLY, frame);
    if (result != ESP_OK) {
        memset(frame, 0, sizeof(*frame));
    }
    return result;
}

void rehab_follow_camera_raw_snapshot_release(rehab_follow_camera_raw_frame_t *frame)
{
    if (frame == NULL || frame->lease_token == 0 || s_camera.lock == NULL) {
        return;
    }

    uint32_t index = frame->lease_token - 1U;
    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    if (index < REHAB_FOLLOW_CAMERA_RAW_SLOTS) {
        camera_raw_slot_t *slot = &s_camera.raw_slots[index];
        if (slot->lease_count > 0 && slot->generation == frame->generation &&
            slot->data == frame->data) {
            --slot->lease_count;
        }
    }
    xSemaphoreGive(s_camera.lock);
    memset(frame, 0, sizeof(*frame));
}

esp_err_t rehab_follow_camera_get_status(rehab_follow_camera_status_t *status)
{
    if (status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(status, 0, sizeof(*status));
    if (s_camera.lock == NULL) {
        status->state = REHAB_FOLLOW_CAMERA_UNINITIALIZED;
        status->pipeline_quiescent = true;
        return ESP_OK;
    }

    xSemaphoreTake(s_camera.lock, portMAX_DELAY);
    status->state = s_camera.state;
    strlcpy(status->last_error, s_camera.last_error, sizeof(status->last_error));
    status->last_esp_err = s_camera.last_esp_err;
    status->last_errno = s_camera.last_errno;
    status->owner_conflict = s_camera.owner_conflict;
    status->fd_valid = s_camera.fd >= 0;
    status->streaming = s_camera.streaming;
    strlcpy(status->device_path, s_camera.device_path, sizeof(status->device_path));
    status->width = CAMERA_OUTPUT_WIDTH;
    status->height = CAMERA_OUTPUT_HEIGHT;
    status->pixel_format = V4L2_PIX_FMT_RGB565;
    camera_fourcc_to_string(V4L2_PIX_FMT_RGB565, status->pixel_format_fourcc);
    strlcpy(status->pixel_format_name, "RGB565", sizeof(status->pixel_format_name));
    strlcpy(status->byte_order, "little_endian", sizeof(status->byte_order));
    status->bytesperline = CAMERA_OUTPUT_STRIDE;
    status->sizeimage = CAMERA_OUTPUT_PAYLOAD_BYTES;
    status->compact_payload_bytes = CAMERA_OUTPUT_PAYLOAD_BYTES;
    status->source_width = s_camera.width;
    status->source_height = s_camera.height;
    status->source_pixel_format = s_camera.pixel_format;
    camera_fourcc_to_string(s_camera.pixel_format, status->source_pixel_format_fourcc);
    strlcpy(status->source_pixel_format_name,
            (s_camera.pixel_format == V4L2_PIX_FMT_RGB565 ||
             s_camera.pixel_format == V4L2_PIX_FMT_RGB565X) ? "RGB565" : "unknown",
            sizeof(status->source_pixel_format_name));
    strlcpy(status->source_byte_order,
            s_camera.pixel_format == V4L2_PIX_FMT_RGB565
                ? "little_endian"
                : (s_camera.pixel_format == V4L2_PIX_FMT_RGB565X
                       ? "big_endian"
                       : "unknown"),
            sizeof(status->source_byte_order));
    status->source_bytesperline = s_camera.bytesperline;
    status->source_sizeimage = s_camera.sizeimage;
    status->source_payload_bytes = s_camera.source_payload_bytes;
    status->output_width = CAMERA_OUTPUT_WIDTH;
    status->output_height = CAMERA_OUTPUT_HEIGHT;
    strlcpy(status->output_pixel_format, "RGB565", sizeof(status->output_pixel_format));
    strlcpy(status->output_byte_order, "little_endian", sizeof(status->output_byte_order));
    status->output_bytesperline = CAMERA_OUTPUT_STRIDE;
    status->output_payload_bytes = CAMERA_OUTPUT_PAYLOAD_BYTES;
    strlcpy(status->scaler_mode,
            camera_scaler_mode_name(s_camera.scaler_mode),
            sizeof(status->scaler_mode));
    status->driver_buffer_count = s_camera.driver_buffer_count;
    memcpy(status->driver_buffer_lengths,
           s_camera.driver_buffer_lengths,
           sizeof(status->driver_buffer_lengths));
    status->last_bytesused = s_camera.last_bytesused;
    status->frame_generation = s_camera.frame_generation;
    status->capture_count = s_camera.capture_count;
    status->dq_error_count = s_camera.dq_error_count;
    status->q_error_count = s_camera.q_error_count;
    status->copy_error_count = s_camera.copy_error_count;
    status->reopen_count = s_camera.reopen_count;
    status->retry_count = s_camera.retry_count;
    status->last_frame_timestamp_us = s_camera.last_frame_timestamp_us;
    status->last_interval_us = s_camera.last_interval_us;
    status->min_interval_us = s_camera.min_interval_us;
    status->max_interval_us = s_camera.max_interval_us;
    status->avg_interval_us = s_camera.interval_sample_count == 0
                                  ? 0
                                  : s_camera.interval_sum_us / s_camera.interval_sample_count;
    status->raw_slot_capacity_each = s_camera.resources_ready
                                         ? CAMERA_OUTPUT_PAYLOAD_BYTES
                                         : 0;
    status->raw_slot_total_bytes = status->raw_slot_capacity_each *
                                   REHAB_FOLLOW_CAMERA_RAW_SLOTS;
    for (size_t i = 0; i < REHAB_FOLLOW_CAMERA_RAW_SLOTS; ++i) {
        status->raw_slot_lease_counts[i] = s_camera.raw_slots[i].lease_count;
    }
    status->preview_interval_ms = s_camera.preview_interval_ms;
    status->snapshot_busy = s_camera.snapshot_busy;
    status->snapshot_request_pending = s_camera.snapshot_request_pending;
    status->snapshot_active = s_camera.snapshot_active;
    status->pipeline_quiescent = camera_pipeline_quiescent_locked();
    status->snapshot_request_count = s_camera.snapshot_request_count;
    status->snapshot_success_count = s_camera.snapshot_success_count;
    status->snapshot_error_count = s_camera.snapshot_error_count;
    status->raw_snapshot_request_count = s_camera.raw_snapshot_request_count;
    status->raw_snapshot_success_count = s_camera.raw_snapshot_success_count;
    status->jpeg_snapshot_request_count = s_camera.jpeg_snapshot_request_count;
    status->jpeg_snapshot_success_count = s_camera.jpeg_snapshot_success_count;
    status->last_snapshot_timestamp_us = s_camera.last_snapshot_timestamp_us;
    status->last_snapshot_total_us = s_camera.last_loop_us;
    status->last_snapshot_jpeg_us = s_camera.last_request_jpeg_us;
    status->capture_us_latest = s_camera.last_capture_us;
    status->capture_us_max = s_camera.max_capture_us;
    status->capture_us_average = s_camera.capture_sample_count == 0
                                     ? 0
                                     : s_camera.capture_sum_us / s_camera.capture_sample_count;
    status->downscale_us_latest = s_camera.last_downscale_us;
    status->downscale_us_max = s_camera.max_downscale_us;
    status->downscale_us_average = s_camera.downscale_sample_count == 0
                                       ? 0
                                       : s_camera.downscale_sum_us / s_camera.downscale_sample_count;
    status->jpeg_us_latest = s_camera.last_jpeg_encode_us;
    status->jpeg_us_max = s_camera.max_jpeg_encode_us;
    status->jpeg_us_average = s_camera.jpeg_encode_sample_count == 0
                                  ? 0
                                  : s_camera.jpeg_encode_sum_us / s_camera.jpeg_encode_sample_count;
    status->snapshot_total_us_latest = s_camera.last_loop_us;
    status->snapshot_total_us_max = s_camera.max_loop_us;
    status->snapshot_total_us_average = s_camera.loop_sample_count == 0
                                            ? 0
                                            : s_camera.loop_sum_us / s_camera.loop_sample_count;
    xSemaphoreGive(s_camera.lock);

    rehab_camera_preview_status_t preview = {0};
    if (rehab_camera_preview_get_status(&preview) == ESP_OK) {
        status->jpeg_generation = preview.generation;
        status->jpeg_publish_count = preview.publish_count;
        status->preview_drop_count = preview.drop_count;
        status->jpeg_error_count = preview.error_count;
        status->jpeg_timestamp_us = preview.timestamp_us;
        status->jpeg_slot_capacity_each = preview.slot_capacity_each;
        status->jpeg_slot_total_bytes = preview.slot_capacity_each *
                                        REHAB_FOLLOW_CAMERA_JPEG_SLOTS;
        memcpy(status->jpeg_slot_lease_counts,
               preview.lease_counts,
               sizeof(status->jpeg_slot_lease_counts));
    }

    status->psram_free_bytes = heap_caps_get_free_size(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    status->psram_min_free_bytes = heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    status->psram_largest_free_block = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    status->internal_free_bytes = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    status->internal_min_free_bytes = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    status->internal_largest_free_block = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    return ESP_OK;
}

const char *rehab_follow_camera_state_name(rehab_follow_camera_state_t state)
{
    switch (state) {
    case REHAB_FOLLOW_CAMERA_UNINITIALIZED:
        return "UNINITIALIZED";
    case REHAB_FOLLOW_CAMERA_CONFLICT:
        return "CONFLICT";
    case REHAB_FOLLOW_CAMERA_STARTING:
        return "STARTING";
    case REHAB_FOLLOW_CAMERA_RUNNING:
        return "RUNNING";
    case REHAB_FOLLOW_CAMERA_STOPPING:
        return "STOPPING";
    case REHAB_FOLLOW_CAMERA_STOPPED:
        return "STOPPED";
    case REHAB_FOLLOW_CAMERA_NO_MEMORY:
        return "NO_MEMORY";
    case REHAB_FOLLOW_CAMERA_ERROR:
        return "ERROR";
    default:
        return "UNKNOWN";
    }
}
