# feature_extractors/extract_features.py

# =========================
# 4 路 EMG 肌肉列表
# =========================
MUSCLES = [
    'Extensor_digitorum',
    'Biceps_brachii',
    'Triceps_brachii',
    'Extensor_carpi_ulnaris'
]


def get_value(row, key, default=None):
    """
    安全读取 row 中的字段。
    如果字段不存在，返回 default。
    """
    try:
        if key in row.index:
            return row[key]
    except AttributeError:
        if key in row:
            return row[key]
    return default


def extract_wrist_features(row):
    """
    Wrist feature group. With rehab_sensor_mapping_20260806 this is derived
    from IMU4 (wrist).
    用于速度、平滑性、停顿、速度峰值。
    """
    return {
        'mean_speed': get_value(row, 'WristMeanSpeed_mm_s', 0),
        'num_speed_peaks': get_value(row, 'NumSpeedPeaks', 0),
        'pause_count': get_value(row, 'PauseCount', 0),
        'DrinkWaterTremorDetected': get_value(row, 'DrinkWaterTremorDetected', None),
        'DrinkWaterTremorFraction': get_value(row, 'DrinkWaterTremorFraction', None),
        'DrinkWaterTremorValidWindowCount': get_value(row, 'DrinkWaterTremorValidWindowCount', None),
        'DrinkWaterTremorPositiveWindowCount': get_value(row, 'DrinkWaterTremorPositiveWindowCount', None),
        'DrinkWaterTremorStatus': get_value(row, 'DrinkWaterTremorStatus', None),
        'DrinkWaterTremorVersion': get_value(row, 'DrinkWaterTremorVersion', None),
    }


def extract_forearm_features(row):
    """
    Forearm feature group. With rehab_sensor_mapping_20260806 this is derived
    from IMU3 (forearm).
    兼容两套字段：
    1. 真实 4 IMU 角度版：
       ForearmRotationRange_deg
       ForearmMeanAngularSpeed_deg_s

    2. MATLAB 重新生成的数据表：
       ForearmROM_mm
       ForearmMeanSpeed_mm_s
    """

    rotation_range = get_value(row, 'ForearmRotationRange_deg', None)
    mean_angular_speed = get_value(row, 'ForearmMeanAngularSpeed_deg_s', None)

    # 如果没有角度版字段，就使用 MATLAB 生成的前臂运动学字段
    if rotation_range is None:
        rotation_range = get_value(row, 'ForearmROM_mm', None)

    if mean_angular_speed is None:
        mean_angular_speed = get_value(row, 'ForearmMeanSpeed_mm_s', None)

    return {
        'rotation_range': rotation_range,
        'mean_angular_speed': mean_angular_speed
    }


def extract_upperarm_features(row):
    """
    Upper-arm feature group. With rehab_sensor_mapping_20260806 this is derived
    from IMU1 (upper arm).
    用于 ROM / 运动幅度评分。
    当前旧表格里是 UpperArmROM_mm，所以先保留这个字段名。
    """
    return {
        'ROM': get_value(row, 'UpperArmROM_mm', 0),
        'mean_angular_speed': get_value(row, 'UpperArmMeanAngularSpeed_deg_s', None)
    }


def extract_trunk_features(row):
    """
    Trunk feature group. With rehab_sensor_mapping_20260806 this is derived
    from IMU2 (trunk/lower back).
    用于代偿评分。
    """
    return {
        'TrunkCompensationRatio': get_value(row, 'TrunkCompensationRatio', 0),
        'TrunkROM_deg': get_value(row, 'TrunkROM_deg', None),
        'TrunkForwardLean_deg': get_value(row, 'TrunkForwardLean_deg', 0),
        'TrunkRotation_deg': get_value(row, 'TrunkRotation_deg', 0),
        'DrinkWaterLFDetected': get_value(row, 'DrinkWaterLFDetected', None),
        'DrinkWaterLFNeutralRoll_deg': get_value(row, 'DrinkWaterLFNeutralRoll_deg', None),
        'DrinkWaterLFPeakAbsRoll_deg': get_value(row, 'DrinkWaterLFPeakAbsRoll_deg', None),
        'DrinkWaterLFMaxDurationOverThreshold_s': get_value(row, 'DrinkWaterLFMaxDurationOverThreshold_s', None),
        'DrinkWaterLFPositiveDuration_s': get_value(row, 'DrinkWaterLFPositiveDuration_s', None),
        'DrinkWaterLFNegativeDuration_s': get_value(row, 'DrinkWaterLFNegativeDuration_s', None),
        'DrinkWaterLFRollThreshold_deg': get_value(row, 'DrinkWaterLFRollThreshold_deg', None),
        'DrinkWaterLFMinDuration_s': get_value(row, 'DrinkWaterLFMinDuration_s', None),
        'DrinkWaterLFValidSampleCount': get_value(row, 'DrinkWaterLFValidSampleCount', None),
        'DrinkWaterLFEstimatedRateHz': get_value(row, 'DrinkWaterLFEstimatedRateHz', None),
        'DrinkWaterLFStatus': get_value(row, 'DrinkWaterLFStatus', None),
        'DrinkWaterLFVersion': get_value(row, 'DrinkWaterLFVersion', None),
    }


def extract_emg_features(row, muscles=None):
    """
    提取 4 路 sEMG 特征。
    当前评分使用 RMS / MAV / WL_mean。
    """
    if muscles is None:
        muscles = MUSCLES

    features = {}

    for m in muscles:
        features[f'{m}_RMS'] = get_value(row, f'{m}_RMS', 0)
        features[f'{m}_MAV'] = get_value(row, f'{m}_MAV', 0)
        features[f'{m}_WL'] = get_value(row, f'{m}_WL_mean', 0)
        features[f'{m}_IEMG'] = get_value(row, f'{m}_IEMG', None)
        features[f'{m}_SSI'] = get_value(row, f'{m}_SSI', None)
        features[f'{m}_SSC'] = get_value(row, f'{m}_SSC', None)
        features[f'{m}_MF'] = get_value(row, f'{m}_MF', None)
        features[f'{m}_MPF'] = get_value(row, f'{m}_MPF', None)
        features[f'{m}_BSE'] = get_value(row, f'{m}_BSE', None)

    for key in ["RMS", "MAV", "WL", "IEMG", "SSI", "SSC", "MF", "MPF", "BSE"]:
        value = get_value(row, key, None)
        if value is not None:
            features[key] = value

    return features
