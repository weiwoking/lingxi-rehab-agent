# Actual v5-B import map

## Runtime mapping before switch

- Active Scan Detect: `C:\ec\_deltar_p4_pipeline\models\rehab_scan_yolo11n_coco80_volleyball_detect_v3_epoch10_exp.pt`
- Active Detect SHA: `e73128f36a3a7e01b0b8bb42cb1387194257470c594fd5b566cd045328f7919b`
- Global Detect confidence: `0.20`
- Detect name/path/SHA/runtime identity: `profiles/model_manifest.json`, the single `models[]` entry where `task=detect`.
- Runtime Detect SHA cross-check: `profiles/stage8b1_yolo_runtime_profile.json` field `models.detect_sha256`.
- Global confidence: `profiles/algorithm_profile.json` field `yolo_confidence`.
- `FramePipeline` reads the manifest and algorithm profile; the formal worker receives the configured model path/SHA/confidence. No source change is needed.
- Offline preflight loads the profile bundle with model hash validation, verifies depth/Detect/Segment local files and SHA-256 values, blocks external networking, and loads DELTAR on CUDA.

## Protected identities

| Asset | Path | SHA-256 before | Result |
|---|---|---|---|
| Segment | `C:\ec\_deltar_p4_pipeline\models\yolo11n-seg.pt` | `55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152` | unchanged |
| Person-Follow | `C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt` | `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1` | unchanged |
| Vision config | `C:\ec\esp-claw\pc\vision_interpreter\config\vision_config.json` | `82e0cec7a3535edfdcae9011e20e05de5452b980c5030ea609c330eeb87233dd` | unchanged |
| Launcher PS1 | `C:\ec\esp-claw\tools\Start-VisionAndScanPC\start_vision_and_scan_services.ps1` | `1b095f2840aac0a405129ea4b4859c9ec88626e0f87099ff8ec939d2f0d94d4e` | unchanged |
| Launcher CMD | `C:\ec\esp-claw\tools\Start-VisionAndScanPC\Start-VisionAndScanPC_SAME_FOLDER_FIXED.cmd` | `c985629b6ff39c6666e6cdc59730b86b329924cef5ea20a33fe8ab962d317b1d` | unchanged |

## Implemented mapping

- Active Scan Detect: `C:\ec\_deltar_p4_pipeline\models\rehab_scan_yolo11n_coco80_volleyball_detect_v5b_exp.pt`
- Active Detect SHA: `74ef3ac06f7b623731284dd36d983ea94207d0254525729303c1057715f613ce`
- `model_manifest.json`: only Detect name/path/SHA/runtime identity changed.
- `stage8b1_yolo_runtime_profile.json`: only `models.detect_sha256` changed.
- `algorithm_profile.json`: byte-for-byte unchanged; `yolo_confidence=0.20`.
- imgsz 640, Detect batch 11, device `cuda:0`, FP32, existing NMS, Segment, Person-Follow and all algorithm chains are unchanged.

Conclusion: configuration-only switching is sufficient; source code changes are not required.
