    #include "rehab_udp_client.h"

#include "wifi_secrets.h"

    #include <string.h>
    #include <errno.h>
    #include <inttypes.h>
    #include <sys/socket.h>
    #include <netdb.h>
    #include <arpa/inet.h>
    #include <unistd.h>

    #include "freertos/FreeRTOS.h"
    #include "freertos/event_groups.h"
    #include "freertos/queue.h"
    #include "freertos/task.h"

    #include "esp_check.h"
    #include "esp_event.h"
    #include "esp_log.h"
    #include "esp_netif.h"
    #include "esp_wifi.h"
    #include "nvs_flash.h"
    #include "esp_timer.h"
    /* ============================================================
    * 1. 用户可改参数
    * ============================================================ */

    /*
    * S3 不再连接 P4 SoftAP。
    * S3 作为 STA 连接电脑热点 / 手机热点。
    *
    * 后面换热点，只改 SSID / PASSWORD。
    */

    /*
    * S3 使用 UDP broadcast。
    * P4 只要在同一热点下监听 5005，就能收到。
    */
    #define REHAB_BROADCAST_IP         "255.255.255.255"
    #define REHAB_SENSOR_UDP_PORT      5005

    #define REHAB_WIFI_MAX_RETRY       100

    #define REHAB_UDP_TASK_STACK_SIZE  4096
    #define REHAB_UDP_TASK_PRIORITY    5

    #define REHAB_PACKET_QUEUE_LEN     1
    #define REHAB_RSSI_PRINT_PERIOD_MS  3000
    /* ============================================================
    * 2. 内部变量
    * ============================================================ */

    static const char *TAG = "REHAB_UDP_CLIENT";

    #define WIFI_CONNECTED_BIT BIT0
    #define WIFI_FAIL_BIT      BIT1

    static EventGroupHandle_t s_wifi_event_group = NULL;
    static QueueHandle_t s_packet_queue = NULL;

    static int s_retry_num = 0;
    static bool s_wifi_connected = false;
    static bool s_started = false;

    /* ============================================================
    * 3. Wi-Fi 事件处理
    * ============================================================ */

    static void wifi_event_handler(void *arg,
                                esp_event_base_t event_base,
                                int32_t event_id,
                                void *event_data)
    {
        (void)arg;

        if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
            ESP_LOGI(TAG, "S3 STA start, connecting to hotspot: %s", REHAB_WIFI_SSID);
            esp_wifi_connect();
        } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
            s_wifi_connected = false;

            if (s_retry_num < REHAB_WIFI_MAX_RETRY) {
                esp_wifi_connect();
                s_retry_num++;
                ESP_LOGW(TAG,
                        "Wi-Fi disconnected, retry %d/%d",
                        s_retry_num,
                        REHAB_WIFI_MAX_RETRY);
            } else {
                xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
                ESP_LOGE(TAG, "Wi-Fi connect failed");
            }
        } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
            const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)event_data;

            ESP_LOGI(TAG,
                    "S3 got IP: " IPSTR
                    ", netmask: " IPSTR
                    ", gateway: " IPSTR,
                    IP2STR(&event->ip_info.ip),
                    IP2STR(&event->ip_info.netmask),
                    IP2STR(&event->ip_info.gw));

            s_retry_num = 0;
            s_wifi_connected = true;
            xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        }
    }

    static void rehab_print_wifi_rssi_if_needed(void)
    {
        static int64_t last_print_us = 0;

        if (!s_wifi_connected) {
            return;
        }

        int64_t now_us = esp_timer_get_time();

        if ((now_us - last_print_us) < REHAB_RSSI_PRINT_PERIOD_MS * 1000LL) {
            return;
        }

        last_print_us = now_us;

        wifi_ap_record_t ap_info;
        esp_err_t ret = esp_wifi_sta_get_ap_info(&ap_info);

        if (ret == ESP_OK) {
            ESP_LOGI(TAG,
                    "WiFi RSSI=%d dBm, channel=%u, authmode=%d",
                    ap_info.rssi,
                    ap_info.primary,
                    ap_info.authmode);
        } else {
            ESP_LOGW(TAG,
                    "get WiFi RSSI failed: %s",
                    esp_err_to_name(ret));
        }
    }

    /* ============================================================
    * 4. NVS / 网络初始化
    * ============================================================ */

    static esp_err_t rehab_nvs_init_once(void)
    {
        esp_err_t ret = nvs_flash_init();

        if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
            ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
            ESP_RETURN_ON_ERROR(nvs_flash_erase(), TAG, "nvs erase failed");
            ret = nvs_flash_init();
        }

        return ret;
    }

    static esp_err_t rehab_wifi_sta_init(void)
    {
        ESP_RETURN_ON_ERROR(rehab_nvs_init_once(), TAG, "nvs init failed");

        s_wifi_event_group = xEventGroupCreate();
        ESP_RETURN_ON_FALSE(s_wifi_event_group != NULL,
                            ESP_ERR_NO_MEM,
                            TAG,
                            "create event group failed");

        ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "esp_netif_init failed");

        esp_err_t ret = esp_event_loop_create_default();
        if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
            ESP_RETURN_ON_ERROR(ret, TAG, "create default event loop failed");
        }

        esp_netif_create_default_wifi_sta();

        wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
        ESP_RETURN_ON_ERROR(esp_wifi_init(&cfg), TAG, "esp_wifi_init failed");

        ESP_RETURN_ON_ERROR(
            esp_event_handler_instance_register(WIFI_EVENT,
                                                ESP_EVENT_ANY_ID,
                                                &wifi_event_handler,
                                                NULL,
                                                NULL),
            TAG,
            "register wifi event failed");

        ESP_RETURN_ON_ERROR(
            esp_event_handler_instance_register(IP_EVENT,
                                                IP_EVENT_STA_GOT_IP,
                                                &wifi_event_handler,
                                                NULL,
                                                NULL),
            TAG,
            "register ip event failed");

        wifi_config_t wifi_config = {0};

        strncpy((char *)wifi_config.sta.ssid,
                REHAB_WIFI_SSID,
                sizeof(wifi_config.sta.ssid) - 1);

        strncpy((char *)wifi_config.sta.password,
                REHAB_WIFI_PASSWORD,
                sizeof(wifi_config.sta.password) - 1);

        wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

        ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA),
                            TAG,
                            "set wifi mode failed");

        ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &wifi_config),
                            TAG,
                            "set wifi config failed");

        ret = esp_wifi_set_ps(WIFI_PS_NONE);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "esp_wifi_set_ps(WIFI_PS_NONE) returned %s", esp_err_to_name(ret));
        }

        ESP_RETURN_ON_ERROR(esp_wifi_start(),
                            TAG,
                            "wifi start failed");

        ESP_LOGI(TAG, "Wi-Fi init done, waiting for connection");

        EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
                                            WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                            pdFALSE,
                                            pdFALSE,
                                            pdMS_TO_TICKS(20000));

        if (bits & WIFI_CONNECTED_BIT) {
            ESP_LOGI(TAG, "Connected to hotspot: %s", REHAB_WIFI_SSID);
            return ESP_OK;
        }

        if (bits & WIFI_FAIL_BIT) {
            ESP_LOGE(TAG, "Failed to connect hotspot: %s", REHAB_WIFI_SSID);
            return ESP_FAIL;
        }

        ESP_LOGW(TAG, "Wi-Fi connect timeout, UDP task will wait and retry later");
        return ESP_OK;
    }

    /* ============================================================
    * 5. UDP 发送任务
    * ============================================================ */

    static void rehab_udp_client_task(void *arg)
    {
        (void)arg;

        struct sockaddr_in dest_addr;
        memset(&dest_addr, 0, sizeof(dest_addr));

        dest_addr.sin_addr.s_addr = inet_addr(REHAB_BROADCAST_IP);
        dest_addr.sin_family = AF_INET;
        dest_addr.sin_port = htons(REHAB_SENSOR_UDP_PORT);

        int sock = -1;
        uint32_t send_ok = 0;
        uint32_t send_fail = 0;

        ESP_LOGI(TAG,
                "UDP broadcast task start, target=%s:%d",
                REHAB_BROADCAST_IP,
                REHAB_SENSOR_UDP_PORT);

        while (1) {
            rehab_print_wifi_rssi_if_needed();
            if (!s_wifi_connected) {
                if (sock >= 0) {
                    close(sock);
                    sock = -1;
                }

                vTaskDelay(pdMS_TO_TICKS(500));
                continue;
            }

            if (sock < 0) {
                sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
                if (sock < 0) {
                    ESP_LOGE(TAG, "Unable to create socket, errno=%d", errno);
                    vTaskDelay(pdMS_TO_TICKS(1000));
                    continue;
                }

                int broadcast = 1;
                int ret = setsockopt(sock,
                                    SOL_SOCKET,
                                    SO_BROADCAST,
                                    &broadcast,
                                    sizeof(broadcast));
                if (ret < 0) {
                    ESP_LOGW(TAG, "setsockopt SO_BROADCAST failed, errno=%d", errno);
                }

                ESP_LOGI(TAG, "UDP broadcast socket created");
            }

            sensor_packet_t pkt;

            if (xQueueReceive(s_packet_queue, &pkt, pdMS_TO_TICKS(1000)) == pdTRUE) {
                int err = sendto(sock,
                                &pkt,
                                sizeof(pkt),
                                0,
                                (struct sockaddr *)&dest_addr,
                                sizeof(dest_addr));

                if (err < 0) {
                    send_fail++;
                    ESP_LOGW(TAG,
                            "UDP broadcast send failed, errno=%d, fail=%" PRIu32,
                            errno,
                            send_fail);

                    close(sock);
                    sock = -1;
                    vTaskDelay(pdMS_TO_TICKS(200));
                } else {
                    send_ok++;

                    if ((send_ok % 100) == 0) {
                        ESP_LOGI(TAG,
                                "UDP broadcast sent packets=%" PRIu32
                                ", latest tx_seq=%" PRIu32
                                ", size=%u",
                                send_ok,
                                pkt.tx_seq,
                                (unsigned)sizeof(pkt));
                    }
                }
            }
        }
    }

    /* ============================================================
    * 6. 对外接口
    * ============================================================ */

    esp_err_t rehab_udp_client_start(void)
    {
        if (s_started) {
            ESP_LOGW(TAG, "rehab udp client already started");
            return ESP_OK;
        }

        s_packet_queue = xQueueCreate(REHAB_PACKET_QUEUE_LEN, sizeof(sensor_packet_t));
        ESP_RETURN_ON_FALSE(s_packet_queue != NULL,
                            ESP_ERR_NO_MEM,
                            TAG,
                            "create packet queue failed");

        ESP_RETURN_ON_ERROR(rehab_wifi_sta_init(),
                            TAG,
                            "wifi sta init failed");

        BaseType_t ok = xTaskCreate(rehab_udp_client_task,
                                    "rehab_udp_client",
                                    REHAB_UDP_TASK_STACK_SIZE,
                                    NULL,
                                    REHAB_UDP_TASK_PRIORITY,
                                    NULL);

        ESP_RETURN_ON_FALSE(ok == pdPASS,
                            ESP_ERR_NO_MEM,
                            TAG,
                            "create udp client task failed");

        s_started = true;

        ESP_LOGI(TAG, "rehab udp broadcast client started");

        return ESP_OK;
    }

    esp_err_t rehab_udp_client_send_packet(const sensor_packet_t *pkt)
    {
        if (pkt == NULL) {
            return ESP_ERR_INVALID_ARG;
        }

        if (s_packet_queue == NULL) {
            return ESP_ERR_INVALID_STATE;
        }

        xQueueOverwrite(s_packet_queue, pkt);

        return ESP_OK;
    }

    bool rehab_udp_client_is_connected(void)
    {
        return s_wifi_connected;
    }