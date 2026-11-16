/* SPDX-License-Identifier: Apache-2.0 */
#include "unity.h"

#include "rehab_score_bridge_priv.h"

TEST_CASE("score bridge accepts only RFC1918 gateway ranges", "[rehab_score]")
{
    TEST_ASSERT_TRUE(rehab_score_gateway_is_private(10, 0));
    TEST_ASSERT_TRUE(rehab_score_gateway_is_private(172, 16));
    TEST_ASSERT_TRUE(rehab_score_gateway_is_private(172, 31));
    TEST_ASSERT_TRUE(rehab_score_gateway_is_private(192, 168));

    TEST_ASSERT_FALSE(rehab_score_gateway_is_private(172, 15));
    TEST_ASSERT_FALSE(rehab_score_gateway_is_private(172, 32));
    TEST_ASSERT_FALSE(rehab_score_gateway_is_private(169, 254));
    TEST_ASSERT_FALSE(rehab_score_gateway_is_private(8, 8));
    TEST_ASSERT_FALSE(rehab_score_gateway_is_private(127, 0));
}

TEST_CASE("score bridge formats automatic gateway URL on port 8020", "[rehab_score]")
{
    char url[REHAB_SCORE_URL_LEN];
    TEST_ASSERT_EQUAL(ESP_OK,
                      rehab_score_format_gateway_url(192, 168, 137, 1,
                                                     url, sizeof(url)));
    TEST_ASSERT_EQUAL_STRING("http://192.168.137.1:8020", url);
    TEST_ASSERT_EQUAL(ESP_OK,
                      rehab_score_format_gateway_url(10, 0, 0, 1,
                                                     url, sizeof(url)));
    TEST_ASSERT_EQUAL_STRING("http://10.0.0.1:8020", url);
    TEST_ASSERT_EQUAL(ESP_OK,
                      rehab_score_format_gateway_url(172, 31, 255, 254,
                                                     url, sizeof(url)));
    TEST_ASSERT_EQUAL_STRING("http://172.31.255.254:8020", url);

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      rehab_score_format_gateway_url(8, 8, 8, 8,
                                                     url, sizeof(url)));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      rehab_score_format_gateway_url(0, 0, 0, 0,
                                                     url, sizeof(url)));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE,
                      rehab_score_format_gateway_url(192, 168, 1, 1,
                                                     url, 8));
}
