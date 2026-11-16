from __future__ import annotations
import argparse, json
from pathlib import Path
import torch
import torch.nn as nn
from PIL import Image, ImageOps
from torchvision import transforms
from ultralytics import YOLO

BASE = Path(__file__).resolve().parent
CLASS_NAMES = ["person", "zxl"]

class LogitWrapper(nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model
    def forward(self, x):
        out = self.model(x)
        return out[1] if isinstance(out, tuple) else out

def load_model(model_path: Path, device):
    ckpt = torch.load(model_path, map_location=device, weights_only=False)
    y = YOLO(ckpt["base_weights"])
    model = y.model
    in_features = model.model[-1].linear.in_features
    model.model[-1].linear = nn.Linear(in_features, 2)
    wrapped = LogitWrapper(model)
    wrapped.load_state_dict(ckpt["state_dict"])
    wrapped.to(device).eval()
    return wrapped, ckpt

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, default=BASE / "best_zxl_identity.pt")
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--bbox", nargs=4, type=float, help="x1 y1 x2 y2 in full-image pixel coordinates")
    ap.add_argument("--threshold-json", type=Path, default=BASE / "recommended_threshold.json")
    ap.add_argument("--preprocess-json", type=Path, default=BASE / "recommended_preprocess.json")
    args = ap.parse_args()
    threshold = json.loads(args.threshold_json.read_text(encoding="utf-8"))["recommended_zxl_threshold"]
    pre = json.loads(args.preprocess_json.read_text(encoding="utf-8"))
    imgsz = int(pre["imgsz"])
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model, ckpt = load_model(args.model, device)
    with Image.open(args.image) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        if args.bbox:
            x1, y1, x2, y2 = args.bbox
            W, H = im.size
            bw, bh = x2 - x1, y2 - y1
            expand = float(pre.get("bbox_expand_ratio", 0.05))
            x1 = max(0, int(x1 - bw * expand)); y1 = max(0, int(y1 - bh * expand))
            x2 = min(W, int(x2 + bw * expand)); y2 = min(H, int(y2 + bh * expand))
            im = im.crop((x1, y1, x2, y2))
        tfm = transforms.Compose([
            transforms.Resize((imgsz, imgsz), interpolation=transforms.InterpolationMode.BILINEAR),
            transforms.ToTensor(),
            transforms.Normalize(mean=pre["normalization"]["mean"], std=pre["normalization"]["std"]),
        ])
        x = tfm(im).unsqueeze(0).to(device)
    with torch.no_grad():
        p = torch.softmax(model(x), dim=1).cpu().numpy()[0]
    identity_valid = bool(p[1] >= threshold)
    print(json.dumps({
        "identity_class": "zxl" if identity_valid else "person",
        "zxl_probability": float(p[1]),
        "person_probability": float(p[0]),
        "threshold": float(threshold),
        "identity_valid": identity_valid,
        "model_path": str(args.model),
        "input_size": imgsz,
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
