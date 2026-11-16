# Mixed Session Rep Diagnosis

This report compares mixed-session repetitions without using scores to assign labels.

| rep | expected label | final score | detector | non-hit reason |
| --- | --- | ---: | --- | --- |
| 1 | normal | 75.04 | False | lf_tr_required_inputs_absent_from_trial_and_default_to_zero_in_current_score_row |
| 2 | forward_trunk_compensation | 74.02 | False | lf_tr_required_inputs_absent_from_trial_and_default_to_zero_in_current_score_row |
| 3 | normal | 86.2 | False | lf_tr_required_inputs_absent_from_trial_and_default_to_zero_in_current_score_row |

## Lowest Three Subscores

### rep_01
- coordination_score: value=7.22, normal_mean=13.037241379310347, normal_median=12.92, z=-7.105645570469902, robust_z=-8.543666666666663, percentile=0.0
- speed_score: value=9.39, normal_mean=13.086206896551724, normal_median=13.36, z=-2.9169207881870625, robust_z=-2.8793172043010724, percentile=0.0
- ROM_score: value=15.77, normal_mean=17.19344827586207, normal_median=16.95, z=-0.9801342105351272, robust_z=-1.0683355704697994, percentile=13.793103448275861
### rep_02
- coordination_score: value=6.98, normal_mean=13.037241379310347, normal_median=12.92, z=-7.398800835262258, robust_z=-8.903399999999996, percentile=0.0
- speed_score: value=9.89, normal_mean=13.086206896551724, normal_median=13.36, z=-2.522337791371012, robust_z=-2.516682795698922, percentile=0.0
- ROM_score: value=15.03, normal_mean=17.19344827586207, normal_median=16.95, z=-1.4896710360701029, robust_z=-1.7383087248322167, percentile=6.896551724137931
### rep_03
- coordination_score: value=12.91, normal_mean=13.037241379310347, normal_median=12.92, z=-0.15542283435112184, robust_z=-0.014988888888888564, percentile=48.275862068965516
- speed_score: value=12.94, normal_mean=13.086206896551724, normal_median=13.36, z=-0.1153815107931075, robust_z=-0.3046129032258062, percentile=44.827586206896555
- ROM_score: value=17.64, normal_mean=17.19344827586207, normal_median=16.95, z=0.30747911885731183, robust_z=0.624704697986579, percentile=65.51724137931035

## IMU2 Key Comparison

- imu2_pitch_range: rep01=0.8500000000000005, rep02=2.1899999999999995, rep03=0.7400000000000002
- imu2_roll_range: rep01=3.6899999999999977, rep02=18.25999999999999, rep03=5.099999999999994
- imu2_yaw_range: rep01=3.3300000000000125, rep02=9.430000000000007, rep03=4.189999999999998
- imu2_gyro_norm_p95: rep01=6.592366798047572, rep02=18.972580214614986, rep03=7.313891270821067
- imu2_pitch_positive_excursion: rep01=0.009999999999999787, rep02=2.0299999999999994, rep03=0.17999999999999972
- imu2_pitch_negative_excursion: rep01=-0.8400000000000007, rep02=-0.16000000000000014, rep03=-0.5600000000000005
- imu2_pitch_absolute_excursion: rep01=0.8400000000000007, rep02=2.0299999999999994, rep03=0.5600000000000005
- imu2_positive_excursion_duration_ms: rep01=100.0, rep02=4200.0, rep03=420.0
- imu2_negative_excursion_duration_ms: rep01=1960.0, rep02=80.0, rep03=2240.0

## Detector Conditions

- detector_LF_feature_value: rep01=0.0, rep02=0.0, rep03=0.0
- detector_LF_margin: rep01=-15.0, rep02=-15.0, rep03=-15.0
- detector_TR_feature_value: rep01=0.0, rep02=0.0, rep03=0.0
- detector_TR_margin: rep01=-15.0, rep02=-15.0, rep03=-15.0
- detector_quality_emg_Biceps_brachii_rms_z_feature_value: rep01=0.0007019850271182103, rep02=0.000158081288575437, rep03=0.00020914577074607462
- detector_quality_emg_Biceps_brachii_rms_z_margin: rep01=-2.777482189266653, rep02=-3.038510794495047, rep03=-3.014004085654576
- detector_quality_emg_Extensor_carpi_ulnaris_rms_z_feature_value: rep01=0.0012618252382858802, rep02=0.000921287171488093, rep03=0.0009475844440337359
- detector_quality_emg_Extensor_carpi_ulnaris_rms_z_margin: rep01=-2.813151523505997, rep02=-2.972667430123873, rep03=-2.960349179447403
- detector_quality_emg_Extensor_digitorum_rms_z_feature_value: rep01=0.005353714852865067, rep02=0.005030974050747637, rep03=0.00564783051183171
- detector_quality_emg_Extensor_digitorum_rms_z_margin: rep01=-1.2413467412194352, rep02=-1.4004660809785776, rep03=-1.0963403176109034
- detector_quality_emg_Triceps_brachii_rms_z_feature_value: rep01=0.005491658549644707, rep02=0.006373649751829962, rep03=0.004843152265838268
- detector_quality_emg_Triceps_brachii_rms_z_margin: rep01=0.9903264165466905, rep02=1.7007090622452523, rep03=0.46799965353460626
- detector_quality_forearm_rotation_deviation_feature_value: rep01=621.89, rep02=629.6, rep03=401.90999999999997
- detector_quality_forearm_rotation_deviation_margin: rep01=193.98480626599996, rep02=201.694806266, rep03=-25.995193734000054
- detector_quality_upper_arm_rom_low_feature_value: rep01=251.54, rep02=159.3, rep03=230.92
- detector_quality_upper_arm_rom_low_margin: rep01=-87.45797815899999, rep02=4.782021840999988, rep03=-66.83797815899999
- detector_quality_wrist_pause_count_feature_value: rep01=5.0, rep02=5.0, rep03=5.0
- detector_quality_wrist_pause_count_margin: rep01=2.0, rep02=2.0, rep03=2.0
- detector_quality_wrist_speed_peaks_feature_value: rep01=0.0, rep02=0.0, rep03=0.0
- detector_quality_wrist_speed_peaks_margin: rep01=-8.0, rep02=-8.0, rep03=-8.0
