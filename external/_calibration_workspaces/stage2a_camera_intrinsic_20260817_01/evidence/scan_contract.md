# Formal scan contract

The configured PC data root is `C:\ec\_deltar_scan_service_data`; complete uploads are stored at `validated\scan_YYYYMMDD_HHMMSS_NNN`.

An environment scan declares `deltar_scan_input.v1`, `frame_count=11`, and `capture_mode=multi_frame`. Its frame folders are contiguous `frame_000000` through `frame_000010`. Each contains `frame.json`, `image.rgb565`, and `tof.json`.

Camera raw is exactly 640×360, stride 1280, 460800 bytes, `RGB565_LE`; PC expands R5/G6/B5 to RGB. `frame.json` carries scan-local sequence, `camera_timestamp_us`, `tof_timestamp_us`, and `pair_delta_us=abs(camera-tof)`. `tof.json` is `rehab_tof_raw.v1`, 8×8, and must match the frame sequence/timestamp. No JPEG preview is part of the validated-input contract.

Completeness requires exact count/identities/files/raster/size and all cross-file sequence/timestamp relationships.
