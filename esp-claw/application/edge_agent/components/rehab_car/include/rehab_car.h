/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define REHAB_CAR_UDP_PORT              5010
#define REHAB_CAR_BROADCAST_IPV4        "255.255.255.255"
#define REHAB_CAR_DEFAULT_SPEED_LEVEL   3U
#define REHAB_CAR_HOLD_INTERVAL_MS      40U
#define REHAB_CAR_HOLD_MIN_MS           100U
#define REHAB_CAR_HOLD_MAX_MS           5000U
#define REHAB_CAR_CAP_HOLD_MAX_MS       3000U
#define REHAB_CAR_UART_PORT              1
#define REHAB_CAR_UART_TX_GPIO           46
#define REHAB_CAR_UART_RX_GPIO           47
#define REHAB_CAR_UART_BAUD_RATE         115200

typedef struct rehab_car_t *rehab_car_handle_t;

typedef enum {
    REHAB_CAR_TRANSPORT_UDP = 0,
    REHAB_CAR_TRANSPORT_UART,
} rehab_car_transport_t;

typedef enum {
    REHAB_CAR_S3_INPUT_MODE_SERIAL = 0,
    REHAB_CAR_S3_INPUT_MODE_BLE,
} rehab_car_s3_input_mode_t;

typedef enum {
    REHAB_CAR_CONTROL_OWNER_NONE = 0,
    REHAB_CAR_CONTROL_OWNER_DEMO,
    REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW,
    REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN,
    REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW,
} rehab_car_control_owner_t;

typedef void (*rehab_car_control_preempt_cb_t)(void *ctx);

typedef struct {
    rehab_car_control_owner_t owner;
    uint32_t token;
} rehab_car_control_lease_t;

typedef struct {
    const char *target_ipv4;
    uint16_t target_port;
    rehab_car_transport_t default_transport;
} rehab_car_config_t;

#define REHAB_CAR_CONFIG_DEFAULT() { \
    .target_ipv4 = REHAB_CAR_BROADCAST_IPV4, \
    .target_port = REHAB_CAR_UDP_PORT, \
    .default_transport = REHAB_CAR_TRANSPORT_UDP, \
}

typedef enum {
    REHAB_CAR_DIRECTION_FORWARD = 0,
    REHAB_CAR_DIRECTION_BACKWARD,
    REHAB_CAR_DIRECTION_STRAFE_LEFT,
    REHAB_CAR_DIRECTION_STRAFE_RIGHT,
    REHAB_CAR_DIRECTION_ROTATE_CCW,
    REHAB_CAR_DIRECTION_ROTATE_CW,
    REHAB_CAR_DIRECTION_STOP,
} rehab_car_direction_t;

typedef struct {
    rehab_car_direction_t direction;
    uint32_t hold_ms;
    uint32_t pause_after_ms;
} rehab_car_sequence_step_t;

typedef struct {
    char target_ipv4[16];
    uint16_t target_port;
    rehab_car_transport_t transport;
    bool uart_initialized;
    int uart_port;
    int uart_tx_gpio;
    int uart_rx_gpio;
    int uart_baud_rate;
    rehab_car_s3_input_mode_t requested_s3_input_mode;
    esp_err_t last_s3_mode_switch_result;
    uint8_t speed_level;
    rehab_car_control_owner_t control_owner;
    uint32_t control_owner_token;
    bool socket_open;
    bool motion_active;
    bool sequence_active;
    bool sequence_cancel_requested;
    size_t sequence_step_index;
    size_t sequence_step_count;
    char last_command[24];
    size_t last_payload_length;
    int last_send_result;
    int last_error;
    esp_err_t last_operation_result;
    uint32_t last_send_count;
    esp_err_t last_final_stop_result;
} rehab_car_status_t;

/**
 * Create local state for the configured transport. UDP stays lazy; selecting
 * UART installs its driver but does not send PAD or STOP.
 */
esp_err_t rehab_car_create(const rehab_car_config_t *config,
                           rehab_car_handle_t *ret_handle);

/** Cancel work, attempt STOP when used, release UDP/UART resources, and free state. */
esp_err_t rehab_car_delete(rehab_car_handle_t handle);

/** Update the local speed level (1..3). This never sends on either transport. */
esp_err_t rehab_car_set_speed(rehab_car_handle_t handle, uint8_t level);

/**
 * Send a validated legacy PAD payload once when hold_ms is zero. Otherwise,
 * repeat every 40 ms for 100..5000 ms and always attempt a final STOP.
 */
esp_err_t rehab_car_send_pad(rehab_car_handle_t handle,
                             uint8_t lx,
                             uint8_t ly,
                             uint8_t rx,
                             uint8_t ry,
                             const char *buttons,
                             uint32_t hold_ms);

/** Map a semantic direction through the current speed level and the one PAD formatter. */
esp_err_t rehab_car_move(rehab_car_handle_t handle,
                         rehab_car_direction_t direction,
                         uint32_t hold_ms);

/** Highest-priority, idempotent cancellation plus exact four-byte STOP. */
esp_err_t rehab_car_stop(rehab_car_handle_t handle);

/** Compatibility alias for the Phase 3A1 API; identical to rehab_car_stop(). */
esp_err_t rehab_car_send_stop(rehab_car_handle_t handle);

/** Read a mutex-protected snapshot without sending on either transport. */
esp_err_t rehab_car_get_status(rehab_car_handle_t handle,
                               rehab_car_status_t *status);

/**
 * Change the active wire backend after an exact STOP on the current backend.
 * Switching is rejected while motion, a sequence, or an owner lease is active.
 */
esp_err_t rehab_car_set_transport(rehab_car_handle_t handle,
                                  rehab_car_transport_t transport);

const char *rehab_car_transport_name(rehab_car_transport_t transport);

/**
 * Request the S3 input mode over the existing UART backend. The complete
 * sequence is STOP followed by MODE BLE or MODE SERIAL. Success confirms only
 * that both local UART writes completed; the S3 firmware has no parsed ACK.
 */
esp_err_t rehab_car_set_s3_input_mode(rehab_car_handle_t handle,
                                      rehab_car_s3_input_mode_t mode);

const char *rehab_car_s3_input_mode_name(rehab_car_s3_input_mode_t mode);

/** Acquire exclusive long-lived behavior control without sending. */
esp_err_t rehab_car_control_acquire(rehab_car_handle_t handle,
                                    rehab_car_control_owner_t owner,
                                    rehab_car_control_preempt_cb_t preempt_cb,
                                    void *preempt_ctx,
                                    rehab_car_control_lease_t *out_lease);

/** Execute one bounded semantic move while an exclusive behavior lease is valid. */
esp_err_t rehab_car_control_move(rehab_car_handle_t handle,
                                 const rehab_car_control_lease_t *lease,
                                 rehab_car_direction_t direction,
                                 uint8_t speed_level,
                                 uint32_t hold_ms);

/** Send STOP without revoking the calling behavior's own valid lease. */
esp_err_t rehab_car_control_stop(rehab_car_handle_t handle,
                                 const rehab_car_control_lease_t *lease);

/** Release a valid idle behavior lease without sending UDP. */
esp_err_t rehab_car_control_release(rehab_car_handle_t handle,
                                    rehab_car_control_lease_t *lease);

const char *rehab_car_control_owner_name(rehab_car_control_owner_t owner);

/** Start one bounded temporary sequence worker using a private copy of the steps. */
esp_err_t rehab_car_sequence_start(rehab_car_handle_t handle,
                                   const rehab_car_sequence_step_t *sequence,
                                   size_t step_count);

/** Cancel the active sequence if any and always attempt exact STOP. */
esp_err_t rehab_car_sequence_cancel(rehab_car_handle_t handle);

/** Start the fixed Phase 3A2 three-speed chassis diagnostic sequence. */
esp_err_t rehab_car_demo_start(rehab_car_handle_t handle);

/** Register manual `car`, raw `PAD`, and raw `STOP` console commands. */
esp_err_t rehab_car_register_console(rehab_car_handle_t handle);

/** Register deterministic car capability groups against this service handle. */
esp_err_t rehab_car_register_capabilities(rehab_car_handle_t handle);

#ifdef __cplusplus
}
#endif
