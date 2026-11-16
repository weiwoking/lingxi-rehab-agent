# zxl Identity Classifier Evaluation Report

## Summary

- Final model: D_yolo11n_320_1to2
- Classes: 0=person, 1=zxl
- Recommended zxl threshold: 0.86
- Recommended input size: 320
- Recommended bbox expand ratio: 0.05
- PT: `E:\wulianwang\yolo\zxl_identity_training\output\best_zxl_identity.pt`
- ONNX: `E:\wulianwang\yolo\zxl_identity_training\output\best_zxl_identity.onnx`

## Actual Data

- zxl train/val/test crops: 81 / 18 / 20
- zxl-source person train/val/test crops: 96 / 12 / 48
- VOC person train_pool/mining_pool/val/test/hard_final_person: 5111 / 1101 / 808 / 415 / 409
- VOC class 14 person boxes originally found: 9218
- Valid VOC person crops: 7844

## Final Test Metrics

- zxl test recall: 0.5500
- zxl test FN as person: 9
- custom zxl-photo person false zxl: 0
- person test false zxl: 0
- hard_final_person false zxl: 0
- average inference seconds/image: 0.004165
- model size MB: 5.96

## Selection Rule

Models were ranked by hard_final_person false positives first, then fixed person-test false positives, zxl recall, zxl F1, inference speed, and model size. The threshold was searched from 0.50 to 0.99 in 0.01 increments.

## Hard Negative Mining

The best first-round model scored all 1101 mining_pool person crops by zxl probability. The highest-risk crops were written to `hard_negative_report.csv` and the top samples were used for H1/H2 second-round fine-tuning. Fixed test and hard_final_person were not used in mining or training.

## BBox Robustness

Robustness variants were evaluated in `bbox_robustness.csv`, including original crop, 5/10 percent expansion, shifts, and edge cropping. The recommended expansion ratio is 0.05.

## Reproduction

Run:

```powershell
& 'E:\wulianwang\yolo\coco_zxl_training\.venv\Scripts\python.exe' 'E:\wulianwang\yolo\coco_zxl_training\scripts\run_model_b_full_pipeline.py'
```

## Handoff

Use `best_zxl_identity.pt` or `best_zxl_identity.onnx` in the Alibaba Cloud deployment project as a second-stage classifier after Model A person detection. Do not replace Model A weights directly; crop person boxes, apply the recommended bbox expansion/preprocess, then apply the recommended threshold.
