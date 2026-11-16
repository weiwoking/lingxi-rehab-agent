/* Source-level Unity tests. Add to an ESP-IDF component test app before running. */
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "unity.h"
#include "../src/rehab_sensor_internal.h"

static sensor_packet_t test_valid_packet(uint8_t node_id)
{
    sensor_packet_t packet = {0};

    packet.magic = SENSOR_PACKET_MAGIC;
    packet.version = SENSOR_PACKET_VERSION;
    packet.packet_size = sizeof(packet);
    packet.node_id = node_id;
    packet.node_type = node_id == 1 ?
                       SENSOR_NODE_TYPE_EMG_DUAL_IMU : SENSOR_NODE_TYPE_DUAL_IMU;
    packet.packet_rate_hz = 100;
    packet.tx_seq = 100;
    packet.tx_ts_us = 1000000;
    packet.flags = SENSOR_FLAG_IMU1_VALID | SENSOR_FLAG_IMU2_VALID;
    if (node_id == 1) {
        packet.flags |= SENSOR_FLAG_EMG_VALID;
        packet.emg.window_start_us = 990000;
        packet.emg.window_end_us = 1000000;
        packet.emg.sample_count = 5;
        packet.emg.rms_ch1 = 1.0f;
        packet.emg.rms_ch2 = 2.0f;
        packet.emg.rms_ch3 = 3.0f;
        packet.emg.rms_ch4 = 4.0f;
        packet.emg.mav_ch1 = 1.0f;
        packet.emg.mav_ch2 = 2.0f;
        packet.emg.mav_ch3 = 3.0f;
        packet.emg.mav_ch4 = 4.0f;
    }
    packet.imu1.ts_us = 1000000;
    packet.imu1.update_count = 10;
    packet.imu2.ts_us = 1000000;
    packet.imu2.update_count = 10;
    packet.checksum = rehab_sensor_packet_checksum(&packet);
    return packet;
}

TEST_CASE("sensor packet v5 layout remains exactly 242 bytes", "[rehab_sensor]")
{
    TEST_ASSERT_EQUAL_UINT32(242, sizeof(sensor_packet_t));
    TEST_ASSERT_EQUAL_UINT32(28, offsetof(sensor_packet_t, emg));
    TEST_ASSERT_EQUAL_UINT32(112, offsetof(sensor_packet_t, imu1));
    TEST_ASSERT_EQUAL_UINT32(176, offsetof(sensor_packet_t, imu2));
    TEST_ASSERT_EQUAL_UINT32(240, offsetof(sensor_packet_t, checksum));
}

TEST_CASE("sensor packet checksum accepts a correct vector", "[rehab_sensor]")
{
    sensor_packet_t packet = test_valid_packet(1);
    sensor_packet_t decoded = {0};
    TEST_ASSERT_EQUAL(REHAB_PACKET_OK,
                      rehab_sensor_packet_validate(&packet, sizeof(packet), &decoded));
    TEST_ASSERT_EQUAL_UINT16(packet.checksum, rehab_sensor_packet_checksum(&decoded));
}

TEST_CASE("sensor packet validation rejects each frozen contract violation", "[rehab_sensor]")
{
    sensor_packet_t decoded = {0};
    sensor_packet_t packet = test_valid_packet(1);

    TEST_ASSERT_EQUAL(REHAB_PACKET_BAD_LENGTH,
                      rehab_sensor_packet_validate(&packet, sizeof(packet) - 1, &decoded));

    packet = test_valid_packet(1);
    packet.magic ^= 1u;
    TEST_ASSERT_EQUAL(REHAB_PACKET_BAD_MAGIC,
                      rehab_sensor_packet_validate(&packet, sizeof(packet), &decoded));

    packet = test_valid_packet(1);
    packet.version++;
    TEST_ASSERT_EQUAL(REHAB_PACKET_BAD_VERSION,
                      rehab_sensor_packet_validate(&packet, sizeof(packet), &decoded));

    packet = test_valid_packet(1);
    packet.packet_size--;
    TEST_ASSERT_EQUAL(REHAB_PACKET_BAD_SIZE,
                      rehab_sensor_packet_validate(&packet, sizeof(packet), &decoded));

    packet = test_valid_packet(1);
    packet.node_type = SENSOR_NODE_TYPE_DUAL_IMU;
    TEST_ASSERT_EQUAL(REHAB_PACKET_BAD_NODE,
                      rehab_sensor_packet_validate(&packet, sizeof(packet), &decoded));

    packet = test_valid_packet(1);
    packet.flags |= 1u << 31;
    TEST_ASSERT_EQUAL(REHAB_PACKET_BAD_FLAGS,
                      rehab_sensor_packet_validate(&packet, sizeof(packet), &decoded));

    packet = test_valid_packet(1);
    packet.checksum ^= 1u;
    TEST_ASSERT_EQUAL(REHAB_PACKET_BAD_CHECKSUM,
                      rehab_sensor_packet_validate(&packet, sizeof(packet), &decoded));

    packet = test_valid_packet(1);
    packet.imu1.angle_x_deg = NAN;
    packet.checksum = rehab_sensor_packet_checksum(&packet);
    TEST_ASSERT_EQUAL(REHAB_PACKET_NON_FINITE,
                      rehab_sensor_packet_validate(&packet, sizeof(packet), &decoded));
}

TEST_CASE("sequence classification covers continuity gaps duplicates order and restart", "[rehab_sensor]")
{
    uint32_t gap = 99;

    TEST_ASSERT_EQUAL(REHAB_SEQ_BASELINE,
                      rehab_sensor_classify_sequence(false, false, 0, 0, 10, 1000, &gap));
    TEST_ASSERT_EQUAL_UINT32(0, gap);
    TEST_ASSERT_EQUAL(REHAB_SEQ_CONTIGUOUS,
                      rehab_sensor_classify_sequence(true, false, 10, 1000, 11, 2000, &gap));
    TEST_ASSERT_EQUAL(REHAB_SEQ_GAP,
                      rehab_sensor_classify_sequence(true, false, 10, 1000, 15, 2000, &gap));
    TEST_ASSERT_EQUAL_UINT32(4, gap);
    TEST_ASSERT_EQUAL(REHAB_SEQ_DUPLICATE,
                      rehab_sensor_classify_sequence(true, false, 10, 1000, 10, 2000, &gap));
    TEST_ASSERT_EQUAL(REHAB_SEQ_OUT_OF_ORDER,
                      rehab_sensor_classify_sequence(true, false, 10, 1000, 9, 2000, &gap));
    TEST_ASSERT_EQUAL(REHAB_SEQ_CONTIGUOUS,
                      rehab_sensor_classify_sequence(true, false, UINT32_MAX, 1000, 0, 2000, &gap));
    TEST_ASSERT_EQUAL(REHAB_SEQ_REBASELINE,
                      rehab_sensor_classify_sequence(true, true, 1000, 5000000, 3, 1000, &gap));
    TEST_ASSERT_EQUAL(REHAB_SEQ_REBASELINE,
                      rehab_sensor_classify_sequence(true, false, 1000, 5000000, 3, 1000, &gap));
}

TEST_CASE("UDP quality uses the frozen percentage boundaries", "[rehab_sensor]")
{
    float ratio = -1.0f;

    TEST_ASSERT_EQUAL(REHAB_UDP_QUALITY_NORMAL,
                      rehab_sensor_classify_udp_quality_pct(0.0f));
    TEST_ASSERT_EQUAL(REHAB_UDP_QUALITY_NORMAL,
                      rehab_sensor_classify_udp_quality_pct(20.0f));
    TEST_ASSERT_EQUAL(REHAB_UDP_QUALITY_ACCEPTABLE,
                      rehab_sensor_classify_udp_quality_pct(20.01f));
    TEST_ASSERT_EQUAL(REHAB_UDP_QUALITY_ACCEPTABLE,
                      rehab_sensor_classify_udp_quality_pct(30.0f));
    TEST_ASSERT_EQUAL(REHAB_UDP_QUALITY_DEGRADED,
                      rehab_sensor_classify_udp_quality_pct(30.01f));
    TEST_ASSERT_EQUAL(REHAB_UDP_QUALITY_UNKNOWN,
                      rehab_sensor_calculate_udp_quality(false, 800, 132, &ratio));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, ratio);
    TEST_ASSERT_EQUAL(REHAB_UDP_QUALITY_UNKNOWN,
                      rehab_sensor_calculate_udp_quality(true, 9, 0, &ratio));
    TEST_ASSERT_EQUAL(REHAB_UDP_QUALITY_NORMAL,
                      rehab_sensor_calculate_udp_quality(true, 800, 0, &ratio));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, ratio);
    TEST_ASSERT_EQUAL(REHAB_UDP_QUALITY_NORMAL,
                      rehab_sensor_calculate_udp_quality(true, 800, 132, &ratio));
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 14.16f, ratio);
    TEST_ASSERT_EQUAL_STRING("normal",
                             rehab_udp_quality_to_string(REHAB_UDP_QUALITY_NORMAL));
    TEST_ASSERT_EQUAL_STRING("acceptable",
                             rehab_udp_quality_to_string(REHAB_UDP_QUALITY_ACCEPTABLE));
    TEST_ASSERT_EQUAL_STRING("degraded",
                             rehab_udp_quality_to_string(REHAB_UDP_QUALITY_DEGRADED));
    TEST_ASSERT_EQUAL_STRING("unknown",
                             rehab_udp_quality_to_string(REHAB_UDP_QUALITY_UNKNOWN));
}
