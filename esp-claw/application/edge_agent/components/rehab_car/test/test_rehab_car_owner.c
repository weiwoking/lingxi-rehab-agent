// SPDX-License-Identifier: Apache-2.0
#include <string.h>

#include "unity.h"
#include "rehab_car.h"

static rehab_car_handle_t create_loopback_car(void)
{
    const rehab_car_config_t config = {
        .target_ipv4 = "127.0.0.1",
        .target_port = REHAB_CAR_UDP_PORT,
    };
    rehab_car_handle_t car = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_create(&config, &car));
    return car;
}

TEST_CASE("car person follow owner is appended and named", "[rehab_car][owner]")
{
    TEST_ASSERT_EQUAL(2, REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW);
    TEST_ASSERT_EQUAL(3, REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN);
    TEST_ASSERT_EQUAL(4, REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW);
    TEST_ASSERT_EQUAL_STRING("person_follow", rehab_car_control_owner_name(
        REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW));
}

TEST_CASE("legacy new and environment owners remain mutually exclusive",
          "[rehab_car][owner]")
{
    rehab_car_handle_t car = create_loopback_car();
    rehab_car_control_lease_t legacy = {0};
    rehab_car_control_lease_t person = {0};
    rehab_car_control_lease_t environment = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_acquire(
        car, REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW,
        NULL, NULL, &legacy));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_control_acquire(
        car, REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW,
        NULL, NULL, &person));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_control_acquire(
        car, REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN,
        NULL, NULL, &environment));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_release(car, &legacy));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_acquire(
        car, REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW,
        NULL, NULL, &person));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_control_acquire(
        car, REHAB_CAR_CONTROL_OWNER_TARGET_FOLLOW,
        NULL, NULL, &legacy));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_control_acquire(
        car, REHAB_CAR_CONTROL_OWNER_ENVIRONMENT_SCAN,
        NULL, NULL, &environment));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_release(car, &person));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}

TEST_CASE("global stop preempts person owner and makes token stale",
          "[rehab_car][owner][stop]")
{
    rehab_car_handle_t car = create_loopback_car();
    rehab_car_control_lease_t person = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_control_acquire(
        car, REHAB_CAR_CONTROL_OWNER_PERSON_FOLLOW,
        NULL, NULL, &person));
    rehab_car_control_lease_t stale = person;
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_stop(car));
    rehab_car_status_t status = {0};
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_get_status(car, &status));
    TEST_ASSERT_EQUAL(REHAB_CAR_CONTROL_OWNER_NONE, status.control_owner);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
                      rehab_car_control_release(car, &stale));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, rehab_car_control_stop(car, &stale));
    TEST_ASSERT_EQUAL(ESP_OK, rehab_car_delete(car));
}
