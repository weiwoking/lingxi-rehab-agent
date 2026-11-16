# Stage 5 ToF Simulator

The simulator emits `rehab_tof_raw.v1` with 64 distances, sigmas, statuses and target counts. Rays originate at the ToF optical centre, use explicit horizontal/vertical FoV, zone order, physical in-plane rotation and `T_camera_from_tof`, and intersect the analytic scene independently of camera depth.

Each zone uses 25 rays and supports nearest, median, dominant-surface and mixture aggregation. Profiles parameterize Gaussian noise, fixed bias, distance-dependent sigma, invalid zones, count/status changes and outliers. Diagnostics retain dominant support and foreground/background mixture flags.

This is deliberately marked `approximate_sensor_model=true`; it is not a complete VL53L5CX optical/material model. Alignment sensitivity covers long-axis baselines 0/20/40/60 mm, rotations 0/90/180/270 degrees and added errors 0/±1/±3 degrees. These are simulation values, not the user's real mount.
