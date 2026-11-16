import json
import math
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import torch

root = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128")
sys.path.insert(0, str(root))

from src.dataloader.zjuL5 import DataLoadPreprocess, preprocessing_transforms


def tensor_tree(value):
    if torch.is_tensor(value):
        return {
            "type": "tensor",
            "shape": list(value.shape),
            "dtype": str(value.dtype),
            "min": float(value.min().item()) if value.numel() else None,
            "max": float(value.max().item()) if value.numel() else None,
        }
    if isinstance(value, dict):
        return {str(k): tensor_tree(v) for k, v in value.items()}
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    return {"type": type(value).__name__, "repr": repr(value)}


data_root = root / "data" / "demo"
index_path = data_root / "data.json"
index = json.loads(index_path.read_text(encoding="utf-8"))
records = index["test"]
selected_indices = [0, len(records) // 2, len(records) - 1]

files = []
for index_number in selected_indices:
    rel = records[index_number]["filename"]
    path = data_root / rel
    with h5py.File(path, "r") as handle:
        datasets = {}
        for key in sorted(handle.keys()):
            array = handle[key][...]
            numeric = np.issubdtype(array.dtype, np.number)
            datasets[key] = {
                "shape": list(array.shape),
                "dtype": str(array.dtype),
                "compression": handle[key].compression,
                "compression_options": handle[key].compression_opts,
                "chunks": list(handle[key].chunks) if handle[key].chunks else None,
                "finite_ratio": float(np.isfinite(array).mean()) if numeric else None,
                "min": float(np.nanmin(array)) if numeric and array.size else None,
                "max": float(np.nanmax(array)) if numeric and array.size else None,
            }
            if key == "mask":
                datasets[key]["true_count"] = int(np.count_nonzero(array))
            if key == "fr":
                datasets[key]["first_rows"] = array[:3].tolist()
        files.append({"index": index_number, "relative_path": rel, "bytes": path.stat().st_size, "datasets": datasets})

args = SimpleNamespace(
    filenames_file_eval=str(index_path),
    data_path_eval=str(data_root),
    zone_sample_num=16,
    sample_uniform=False,
)
loader_stdout = StringIO()
with redirect_stdout(loader_stdout):
    dataset = DataLoadPreprocess(args, "online_eval", transform=preprocessing_transforms("online_eval"))
sample = dataset[0]

result = {
    "index_path": str(index_path),
    "test_sample_count": len(records),
    "inspected_indices": selected_indices,
    "files": files,
    "dataset_first_sample": tensor_tree(sample),
    "official_loader_stdout": loader_stdout.getvalue().strip(),
    "field_semantics_from_official_loader": {
        "rgb": "HWC uint8 RGB, divided by 255 then ImageNet-normalized and transposed to CHW",
        "depth": "HW metric-depth float array, converted to 1xHxW float tensor",
        "hist_data": "64x2 rows: fitted Gaussian [mean depth, standard deviation] per ToF zone",
        "fr": "64x4 rows: [start_y, start_x, end_y, end_x] rectangles used to derive patch_info",
        "mask": "64-zone validity mask selecting histogram rows used for inverse-normal sampling",
        "additional.hist_data": "64xzone_sample_num sampled depth hypotheses from each valid Gaussian",
    },
}
print(json.dumps(result, ensure_ascii=False, indent=2))
