# Current Detector Static Audit

- Detector: `feature_extractors.compensation_detector.detect_compensation_4imu_4emg`
- `LF` uses `TrunkForwardLean_deg >= 15` and appends a compensation type.
- `TR` uses `TrunkRotation_deg >= 15` and appends a compensation type.
- Upper-arm ROM, forearm rotation, wrist peaks, pauses, and EMG RMS z-score create quality findings only.
- Static diagnosis: when V2.1.2 trial rows do not provide `TrunkForwardLean_deg` / `TrunkRotation_deg`, the current score-row adapter can pass zero defaults, so LF/TR cannot fire.
