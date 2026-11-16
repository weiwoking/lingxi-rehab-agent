# Model Provenance

Detection uses the read-only existing `C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt`, loaded as `task=detect`. Segmentation uses `C:\ec\_deltar_p4_pipeline\models\yolo11n-seg.pt`, downloaded by Ultralytics 8.4.93 from the official `ultralytics/assets` GitHub release v8.4.0 and loaded as `task=segment`.

The authoritative sizes, SHA-256 hashes, class tables, PyTorch/Ultralytics versions, device, source paths, DELTAR checkpoint hash, and license notes are machine-readable in `models/MODEL_MANIFEST.json`. Ultralytics assets are treated as research PoC assets under the recorded AGPL-3.0 notice; this project makes no independent commercial-license determination.

YOLO runs in the existing Vision Python 3.10 environment on CPU. DELTAR runs in the formal Python 3.11 / PyTorch 2.7.1+cu128 environment on CUDA. No Ultralytics package was installed into the DELTAR environment, and no model is copied into `esp-claw`.
