import json
import platform
import sys

import timm
import torch
import torchvision


result = {
    "python": sys.version,
    "platform": platform.platform(),
    "torch": torch.__version__,
    "torchvision": torchvision.__version__,
    "timm": timm.__version__,
    "timm_has_tf_efficientnetv2_b3": "tf_efficientnetv2_b3" in timm.list_models(),
    "timm_has_swin_base_patch4_window12_384": "swin_base_patch4_window12_384" in timm.list_models(),
    "torch_cuda_runtime": torch.version.cuda,
    "cuda_available": torch.cuda.is_available(),
}

if torch.cuda.is_available():
    device = torch.device("cuda:0")
    result.update(
        {
            "device_name": torch.cuda.get_device_name(device),
            "compute_capability": list(torch.cuda.get_device_capability(device)),
            "compiled_arch_list": torch.cuda.get_arch_list(),
        }
    )
    a = torch.arange(16, device=device, dtype=torch.float32).reshape(4, 4)
    b = a @ a.T
    torch.cuda.synchronize()
    result["cuda_matmul_sum"] = float(b.sum().item())

print(json.dumps(result, ensure_ascii=False, indent=2))
