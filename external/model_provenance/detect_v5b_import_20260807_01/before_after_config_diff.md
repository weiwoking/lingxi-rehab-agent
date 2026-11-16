# Before/after Scan Detect configuration diff

## `model_manifest.json`

```diff
- name: YOLO11n COCO80 volleyball Detect v3 Epoch 10 experimental
- path: C:\ec\_deltar_p4_pipeline\models\rehab_scan_yolo11n_coco80_volleyball_detect_v3_epoch10_exp.pt
- sha256: e73128f36a3a7e01b0b8bb42cb1387194257470c594fd5b566cd045328f7919b
- runtime: Ultralytics 8.4.93 CUDA FP32 experimental Epoch 10
+ name: YOLO11n COCO80 volleyball Detect v5-B Pilot P15 Epoch 2 experimental
+ path: C:\ec\_deltar_p4_pipeline\models\rehab_scan_yolo11n_coco80_volleyball_detect_v5b_exp.pt
+ sha256: 74ef3ac06f7b623731284dd36d983ea94207d0254525729303c1057715f613ce
+ runtime: Ultralytics 8.4.93 CUDA FP32 experimental v5-B Pilot P15 Epoch 2
```

Depth and Segment entries are unchanged. Before SHA: `81bc95fec8e0e6d54d9f9762ecbf607ff28c5ee55fbe0afc7451cb7e6185c8a3`; after SHA: `0be8eff315194cf498ae77b188186b2a16d3ffefe8bc639b29dfc3eb00ce2b46`.

## `stage8b1_yolo_runtime_profile.json`

```diff
- models.detect_sha256: e73128f36a3a7e01b0b8bb42cb1387194257470c594fd5b566cd045328f7919b
+ models.detect_sha256: 74ef3ac06f7b623731284dd36d983ea94207d0254525729303c1057715f613ce
```

Every other field is unchanged. Before SHA: `b66ade13ca35cdd6e0e52d560983cda84837cf4ba638df3eda365f5a1e05b863`; after SHA: `3c3a473f35072f28a267dbd16871416fc59c0cb9d1ab27abe5145c12f1fca2f4`.

## `algorithm_profile.json`

No diff. SHA before/after: `10d07c2d781f3de4a62ef6e549581e39123794585cebd9fd370bc170fbd8e2c8`; `yolo_confidence=0.20`.

The two changed JSON files were staged outside `profiles` and installed with same-volume `os.replace` atomic replacement.
