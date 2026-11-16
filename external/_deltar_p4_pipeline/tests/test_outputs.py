from pathlib import Path

import numpy as np
from PIL import Image

from deltar_p4_pipeline.validation import quantize_depth_mm, validate_depth_png


def test_npy_to_png_quantization_rule(tmp_path: Path) -> None:
    depth = np.asarray([[1.0004, 1.0005, 100.0, np.nan, -1.0]], dtype=np.float32)
    mm = quantize_depth_mm(depth)
    assert mm.tolist() == [[1000, 1000, 65535, 0, 0]]
    path = tmp_path / "depth.png"
    Image.fromarray(mm, mode="I;16").save(path)
    report = validate_depth_png(depth, path)
    assert report["exact_quantized_match"] is True
    assert report["max_quantization_difference_mm"] == 0
