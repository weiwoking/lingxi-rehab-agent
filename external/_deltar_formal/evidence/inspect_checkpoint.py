import json
from pathlib import Path

import torch


path = Path(r"C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128\weights\nyu.pt")
state = torch.load(path, map_location="cpu", weights_only=True)
if not isinstance(state, dict):
    raise TypeError(type(state).__name__)

result = {
    "path": str(path),
    "top_level_type": type(state).__name__,
    "key_count": len(state),
    "keys": [
        {
            "key": key,
            "type": type(value).__name__,
            "shape": list(value.shape) if torch.is_tensor(value) else None,
            "dtype": str(value.dtype) if torch.is_tensor(value) else None,
            "numel": value.numel() if torch.is_tensor(value) else None,
        }
        for key, value in state.items()
    ],
}
print(json.dumps(result, ensure_ascii=False, indent=2))
