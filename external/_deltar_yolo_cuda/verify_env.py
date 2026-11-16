from __future__ import annotations

import json
import platform

import cv2
import numpy
import PIL
import torch
import torchvision
import ultralytics

payload = {
    "python": platform.python_version(),
    "torch": torch.__version__,
    "torchvision": torchvision.__version__,
    "cuda_runtime": torch.version.cuda,
    "cuda_available": torch.cuda.is_available(),
    "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    "ultralytics": ultralytics.__version__,
    "numpy": numpy.__version__,
    "pillow": PIL.__version__,
    "opencv": cv2.__version__,
}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
assert payload["torch"] == "2.7.1+cu128"
assert payload["torchvision"] == "0.22.1+cu128"
assert payload["cuda_runtime"] == "12.8"
assert payload["cuda_available"] is True
assert "RTX 5070 Ti" in payload["gpu"]
assert payload["ultralytics"] == "8.4.93"
