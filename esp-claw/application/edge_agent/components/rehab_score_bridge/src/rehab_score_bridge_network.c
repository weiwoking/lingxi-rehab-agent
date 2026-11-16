/* SPDX-License-Identifier: Apache-2.0 */
#include "rehab_score_bridge_priv.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "app_config.h"
#include "esp_netif.h"
#include "esp_netif_ip_addr.h"

#define REHAB_SCORE_STA_IFKEY "WIFI_STA_DEF"

bool rehab_score_gateway_is_private(uint8_t first_octet,
                                    uint8_t second_octet)
{
    return first_octet == 10U ||
           (first_octet == 172U && second_octet >= 16U && second_octet <= 31U) ||
           (first_octet == 192U && second_octet == 168U);
}

esp_err_t rehab_score_format_gateway_url(uint8_t first_octet,
                                         uint8_t second_octet,
                                         uint8_t third_octet,
                                         uint8_t fourth_octet,
                                         char *out_url,
                                         size_t out_size)
{
    if (!out_url || out_size == 0 ||
            !rehab_score_gateway_is_private(first_octet, second_octet)) {
        return ESP_ERR_INVALID_ARG;
    }
    int written = snprintf(out_url,
                           out_size,
                           "http://%u.%u.%u.%u:%u",
                           first_octet,
                           second_octet,
                           third_octet,
                           fourth_octet,
                           REHAB_SCORE_AUTO_PORT);
    return written > 0 && (size_t)written < out_size
               ? ESP_OK
               : ESP_ERR_INVALID_SIZE;
}

const char *rehab_score_url_source_to_string(rehab_score_url_source_t source)
{
    switch (source) {
    case REHAB_SCORE_URL_SOURCE_MANUAL:
        return "manual";
    case REHAB_SCORE_URL_SOURCE_STA_GATEWAY:
        return "sta_gateway";
    default:
        return "unknown";
    }
}

esp_err_t rehab_score_resolve_base_url(char *out_url,
                                       size_t out_size,
                                       rehab_score_url_source_t *out_source)
{
    if (!out_url || out_size == 0 || !out_source) {
        return ESP_ERR_INVALID_ARG;
    }
    out_url[0] = '\0';
    *out_source = REHAB_SCORE_URL_SOURCE_STA_GATEWAY;

    app_config_t *config = calloc(1, sizeof(*config));
    if (!config) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = app_config_load(config);
    if (err != ESP_OK) {
        free(config);
        return err;
    }
    if (config->rehab_score_base_url[0]) {
        *out_source = REHAB_SCORE_URL_SOURCE_MANUAL;
        bool valid = rehab_score_contract_base_url(config->rehab_score_base_url,
                                                   out_url,
                                                   out_size);
        free(config);
        return valid ? ESP_OK : ESP_ERR_INVALID_ARG;
    }
    free(config);

    esp_netif_t *sta = esp_netif_get_handle_from_ifkey(REHAB_SCORE_STA_IFKEY);
    if (!sta || !esp_netif_is_netif_up(sta)) {
        return ESP_ERR_NOT_FOUND;
    }
    esp_netif_ip_info_t info = {0};
    err = esp_netif_get_ip_info(sta, &info);
    if (err != ESP_OK || info.ip.addr == 0 || info.gw.addr == 0) {
        return ESP_ERR_NOT_FOUND;
    }
    return rehab_score_format_gateway_url(esp_ip4_addr1(&info.gw),
                                          esp_ip4_addr2(&info.gw),
                                          esp_ip4_addr3(&info.gw),
                                          esp_ip4_addr4(&info.gw),
                                          out_url,
                                          out_size);
}
