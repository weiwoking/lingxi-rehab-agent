import hashlib
import json
import random
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image


root = Path(r"C:\ec\_deltar_formal\outputs\full_demo")
files = sorted(root.rglob("*_demo.png"))
random.seed(20260717)
selected = random.sample(files, 3)

contracts = Counter()
for path in files:
    array = np.asarray(Image.open(path))
    contracts[(tuple(array.shape), str(array.dtype))] += 1

samples = []
for path in selected:
    array = np.asarray(Image.open(path))
    positive = array[array > 0]
    samples.append(
        {
            "relative_path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "unit": "millimetres (official evaluate.py multiplies model depth in metres by 1000)",
            "nonzero_ratio": float((array > 0).mean()),
            "min": int(array.min()),
            "median": float(np.median(array)),
            "max": int(array.max()),
            "positive_min": int(positive.min()) if positive.size else None,
        }
    )

result = {
    "root": str(root),
    "file_count": len(files),
    "shape_dtype_contracts": [
        {"shape": list(shape), "dtype": dtype, "count": count}
        for (shape, dtype), count in sorted(contracts.items())
    ],
    "random_seed": 20260717,
    "random_samples": samples,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
