# Vision Interpreter Identity Model B Deployment Report

## Result
Deployment completed.

## Before Architecture
Model A only: `/opt/rehab/vision_interpreter/models/yolo11n.pt` performed full-frame object detection. `/api/v1/vision/fuse` decoded one RGB565 frame and one ToF frame, ran Model A, then ToF/geometry fusion.

## After Architecture
Model A still performs full-frame detection and owns all original bboxes. Model B is a second-stage identity classifier used only for Model A `person` detections. It crops each original person bbox with 5 percent expansion for classification only. ToF mapping, distance and height continue to use the unmodified Model A bbox. Final detections are Model A non-person objects plus Model A person boxes reclassified as `person` or `zxl`. At most one `zxl` is emitted per image.

## Model Paths and Hashes
- Model A path: `/opt/rehab/vision_interpreter/models/yolo11n.pt`
- Model A SHA256: `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`
- Model B PT path: `/opt/rehab/vision_interpreter/models/identity/best_zxl_identity.pt`
- Model B PT SHA256: `f440b61cd48f76f75052469fa1ea1155323f971b5757ada92c7323ebc6ae900f`
- Model B ONNX path: `/opt/rehab/vision_interpreter/models/identity/best_zxl_identity.onnx`
- Model B ONNX SHA256: `dda7c071ae56b3e16c6a77a445e410ba04924377d56fed46c72e461c7ca23886`
- Model B base classifier weights: `/opt/rehab/vision_interpreter/models/identity/yolo11n-cls.pt`

## Identity Parameters
- class order: `0=person`, `1=zxl`
- input_size: `320`
- bbox_expand_ratio: `0.05`
- zxl_threshold: `0.59`
- safety_threshold: `0.86`
- balanced_threshold: `0.74`
- demo_threshold: `0.59`
- max_zxl_per_image: `1`
- fail_open_as_person: `true`

## Config
- `/opt/rehab/vision_interpreter/config/vision_config.json`

## Modified Files
- `app/main.py`
- `app/identity_classifier.py`
- `app/response_builder.py`
- `config/vision_config.json`
- `tests/test_contract.py`
- `models/identity/*`
- `rollback_identity_model_b.sh`

## Tests
- Python syntax check: passed
- pytest strict contract suite: `11 passed, 1 warning`
- Model A offline load: passed
- Model B offline load: passed with handoff checkpoint loader; output shape `(1, 2)`
- Temporary `127.0.0.1:8011` health: passed
- Temporary `127.0.0.1:8011` fuse: passed
- Temporary error regression: `INVALID_IMAGE_SIZE`, `INVALID_TOF_DATA`, `PAIR_TIME_EXCEEDED`, `NO_VALID_TOF_FRAME` passed
- Temporary multi-person max-one-zxl: passed
- Temporary 20 request performance: avg `110.4 ms`, min `105 ms`, max `114 ms`
- 8011 stopped: passed
- Formal 8010 restart: passed
- Formal 8010 health: passed
- Formal 8010 fuse: passed
- Formal Nginx health/fuse: passed
- Formal 8010 20 request performance: avg `105.55 ms`, min `102 ms`, max `110 ms`

## Formal 8010 State
- `vision-interpreter`: active and enabled
- listener: `127.0.0.1:8010` only
- `8011`: stopped

## Health Result
Health returned `status=ok`, `model_ready=true`, `model_error=null`, unchanged top-level field set: `status`, `model_ready`, `model_path`, `model_error`, `model_classes`, `geometry`, `image`, `tof`.

## Representative Results
- zxl output: final class `zxl`, confidence `0.9204`, summary `object_count=1`, `zxl_found=true`, `person_count=0`
- multi-person temporary test: `object_count=2`, `zxl_found=true`, `person_count=1`
- no-target temporary test: `object_count=0`, `zxl_found=false`, `person_count=0`
- non-person regression: tests assert non-person detections do not enter Model B
- bbox regression: tests assert bbox is unchanged across identity classification
- ToF/height regression: representative outputs include `distance_cm`, `object_height_cm`, `bottom_height_from_ground_cm` using original bbox geometry

## 8000 and Nginx
- `cloud-rehab-server.service`: active
- `8000 /doctor`: HTTP 200
- Nginx `/doctor`: HTTP 200
- Nginx `/patient`: HTTP 200
- Nginx `/api/state`: HTTP 200
- `nginx -t`: passed
- Nginx configuration was not modified in this deployment.

## Known Limitations
- Current camera and mount parameters remain temporary calibration parameters. They prove the algorithm chain runs but do not prove measurement accuracy.
- Model B PT is a handoff checkpoint containing `base_weights` and `state_dict`, not a direct `YOLO(path)` checkpoint. Deployment uses the provided handoff loading pattern and cached `yolo11n-cls.pt` base weights.
- ONNX is retained as backup only and is not used for production inference.
- A mistaken rollback dry-run generation attempt briefly stopped `vision-interpreter`; it was intercepted, the deployed directory was restored, and final 8010 validation passed afterward.

## Rollback
Dry run:

```bash
/opt/rehab/vision_interpreter/rollback_identity_model_b.sh
```

Execute:

```bash
/opt/rehab/vision_interpreter/rollback_identity_model_b.sh --execute
```

Backup root: `/opt/rehab/backups/vision_interpreter_20260713005133`

## Follow-up P4 Real Input Verification
1. Capture real `640x360` RGB565 frames and paired `8x8` ToF frames from the device.
2. Send repeated `/api/v1/vision/fuse` requests with real zxl, ordinary person, multi-person, non-person, and no-target scenes.
3. Compare distance and height outputs against physical measurements after real calibration replaces temporary parameters.
4. Tune `identity.zxl_threshold` only through config if field data requires it.
