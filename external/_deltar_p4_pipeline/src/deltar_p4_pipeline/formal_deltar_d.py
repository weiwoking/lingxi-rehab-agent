"""Production DELTAR-D: true per-zone RGB read with official 8x8 write layout."""
from __future__ import annotations

from typing import Any

import torch
import torch.nn.functional as F
from einops import rearrange, repeat


def rectangles_form_uniform_grid(rect_data: torch.Tensor, atol: float = 1e-5) -> bool:
    rects = rect_data.detach().to(torch.float64)
    if rects.ndim == 2:
        rects = rects.unsqueeze(0)
    if rects.ndim != 3 or tuple(rects.shape[1:]) != (64, 4):
        return False
    for one in rects:
        grid = one.reshape(8, 8, 4)
        sy, sx, ey, ex = (grid[..., index] for index in range(4))
        heights, widths = ey - sy, ex - sx
        checks = (
            torch.allclose(sy, sy[:, :1].expand_as(sy), atol=atol, rtol=0),
            torch.allclose(ey, ey[:, :1].expand_as(ey), atol=atol, rtol=0),
            torch.allclose(sx, sx[:1, :].expand_as(sx), atol=atol, rtol=0),
            torch.allclose(ex, ex[:1, :].expand_as(ex), atol=atol, rtol=0),
            torch.allclose(ey[:-1, 0], sy[1:, 0], atol=atol, rtol=0),
            torch.allclose(ex[0, :-1], sx[0, 1:], atol=atol, rtol=0),
            torch.allclose(
                heights, heights.flatten()[0].expand_as(heights), atol=atol, rtol=0
            ),
            torch.allclose(
                widths, widths.flatten()[0].expand_as(widths), atol=atol, rtol=0
            ),
        )
        if not all(checks):
            return False
    return True


def sample_true_zone_rectangles(
    embeddings: torch.Tensor,
    rect_data: torch.Tensor,
    stride: float,
    output_size: tuple[int, int],
) -> torch.Tensor:
    """One vectorized grid_sample for all 64 Stage2 rectangles."""
    batch, channels, height, width = embeddings.shape
    p1, p2 = output_size
    rects = rect_data.to(device=embeddings.device, dtype=embeddings.dtype)
    if tuple(rects.shape) != (batch, 64, 4):
        raise ValueError(f"rect_data must be [B,64,4], got {tuple(rects.shape)}")
    fy0, fx0, fy1, fx1 = (rects[..., index] / stride for index in range(4))
    uy = (torch.arange(p1, device=rects.device, dtype=rects.dtype) + 0.5) / p1
    ux = (torch.arange(p2, device=rects.device, dtype=rects.dtype) + 0.5) / p2
    yy = fy0[..., None, None] + uy[None, None, :, None] * (fy1 - fy0)[..., None, None] - 0.5
    xx = fx0[..., None, None] + ux[None, None, None, :] * (fx1 - fx0)[..., None, None] - 0.5
    yy = yy.expand(batch, 64, p1, p2)
    xx = xx.expand(batch, 64, p1, p2)
    grid = torch.stack(
        (
            xx * 2.0 / max(width - 1, 1) - 1.0,
            yy * 2.0 / max(height - 1, 1) - 1.0,
        ),
        dim=-1,
    )
    source = embeddings[:, None].expand(
        batch, 64, channels, height, width
    ).reshape(batch * 64, channels, height, width)
    sampled = F.grid_sample(
        source,
        grid.reshape(batch * 64, p1, p2, 2),
        mode="bilinear",
        padding_mode="zeros",
        align_corners=True,
    )
    return sampled.reshape(batch, 64, channels, p1, p2)


def _official_layout(patch_info: dict[Any, Any], stride: float) -> dict[str, Any]:
    zone_num = int(patch_info["zone_num"][0])
    pad_size = patch_info[stride]["pad_size"]
    patch_size = patch_info[stride]["patch_size"]
    index = patch_info[stride]["index_wo_pad"]
    pad_height, pad_width = torch.max(pad_size, axis=0)[0]
    p1, p2 = torch.max(patch_size, axis=0)[0]
    sy0, sx0 = torch.min(index, axis=0)[0][0:2]
    ey0, ex0 = torch.max(index, axis=0)[0][2:4]
    sy, ey = sy0 + pad_height, ey0 + pad_height
    sx, ex = sx0 + pad_width, ex0 + pad_width
    return {
        "zone_num": zone_num,
        "pad_height": pad_height,
        "pad_width": pad_width,
        "p1": int(p1),
        "p2": int(p2),
        "sy0": sy0,
        "sx0": sx0,
        "ey0": ey0,
        "ex0": ex0,
        "sy": sy,
        "sx": sx,
        "ey": ey,
        "ex": ex,
        "tzh": ey - sy,
        "tzw": ex - sx,
        "interpolate": bool((ey - sy) != p1 * zone_num or (ex - sx) != p2 * zone_num),
    }


def official_layout_write(
    feat0: torch.Tensor,
    messages: torch.Tensor,
    patch_info: dict[Any, Any],
    stride: float,
    image_shape: tuple[int, int],
) -> tuple[torch.Tensor, dict[str, Any]]:
    batch = feat0.shape[0]
    height, width = image_shape
    channels = feat0.shape[-1]
    layout = _official_layout(patch_info, stride)
    zone_mask = torch.zeros(
        [batch, height, width], dtype=torch.bool, device=feat0.device
    )
    zone_mask[
        :,
        torch.clip(layout["sy0"], 0, height):torch.clip(layout["ey0"], 0, height),
        torch.clip(layout["sx0"], 0, width):torch.clip(layout["ex0"], 0, width),
    ] = 1
    zone_mask = repeat(
        rearrange(zone_mask, "b h w -> b (h w)"), "b s -> b s c", c=channels
    )
    if layout["pad_height"] > 0 or layout["pad_width"] > 0:
        pad_mask = torch.ones(
            [batch, channels, layout["tzh"], layout["tzw"]],
            dtype=torch.bool,
            device=feat0.device,
        )
        pad_mask[:, :, :torch.clip(0 - layout["sy0"], 0, None)] = 0
        if torch.clip(layout["ey0"] - height, 0, None) > 0:
            pad_mask[:, :, -torch.clip(layout["ey0"] - height, 0, None):] = 0
        pad_mask[..., :torch.clip(0 - layout["sx0"], 0, None)] = 0
        if torch.clip(layout["ex0"] - width, 0, None) > 0:
            pad_mask[..., -torch.clip(layout["ex0"] - width, 0, None):] = 0
        pad_mask = rearrange(pad_mask, "b c h w -> (b h w c)")
    else:
        pad_mask = torch.ones(
            [batch * channels * layout["tzh"] * layout["tzw"]],
            dtype=torch.bool,
            device=feat0.device,
        )
    if layout["interpolate"]:
        dense = rearrange(
            messages,
            "(b ph pw) (p1 p2) c -> b c (ph p1) (pw p2)",
            b=batch,
            ph=layout["zone_num"],
            pw=layout["zone_num"],
            p1=layout["p1"],
            p2=layout["p2"],
        )
        dense = F.interpolate(
            dense,
            size=[layout["tzh"], layout["tzw"]],
            mode="bilinear",
            align_corners=True,
        )
        flattened = rearrange(dense, "b c h w -> (b h w c)")
    else:
        flattened = rearrange(
            messages,
            "(b zn zn2) (p1 p2) c -> (b zn p1 zn2 p2 c)",
            b=batch,
            zn=layout["zone_num"],
            zn2=layout["zone_num"],
            p1=layout["p1"],
            p2=layout["p2"],
        )
    feat0[zone_mask] += flattened[pad_mask]
    return feat0, {
        "layout_covered_feature_values": int(torch.count_nonzero(zone_mask).item()),
        "layout_interpolate": layout["interpolate"],
    }


def _replacement_class(base: type) -> type:
    class FormalDTransformerFusion(base):
        def __init__(self, embedding_dim: int, max_resolution: list[int], num_heads: int = 4):
            super().__init__(embedding_dim, max_resolution, num_heads=num_heads)
            self.last_formal_d_audit: dict[str, Any] = {}

        def forward(self, x: torch.Tensor, feat1: torch.Tensor, **kwargs: Any) -> torch.Tensor:
            rect_data, mask, patch_info = (
                kwargs["rect_data"],
                kwargs["mask"],
                kwargs["patch_info"],
            )
            if rectangles_form_uniform_grid(rect_data):
                self.last_formal_d_audit = {
                    "fusion": "D_TRUE_READ_OFFICIAL_WRITE",
                    "uniform_equivalence_path": True,
                    "official_forward_used": True,
                    "resolution": list(x.shape[-2:]),
                }
                return super().forward(x, feat1, **kwargs)

            embeddings = x
            batch, channels, height, width = embeddings.shape
            layout = _official_layout(patch_info, self.conv_patch_size)
            p1, p2 = layout["p1"], layout["p2"]
            offset_y = (
                torch.randint(0, self.max_resolution[0] - height + 1, [1])
                if height < self.max_resolution[0]
                else 0
            )
            offset_x = (
                torch.randint(0, self.max_resolution[1] - width + 1, [1])
                if width < self.max_resolution[1]
                else 0
            )
            positional = self.positional_encodings.view(
                self.max_resolution[0], self.max_resolution[1], -1
            )[offset_y:offset_y + height, offset_x:offset_x + width].permute(2, 0, 1)
            embeddings = embeddings + positional
            feat0 = embeddings.flatten(2).permute(0, 2, 1)
            feat1 = feat1 + self.positional_encodings2.unsqueeze(0).unsqueeze(0)
            feat1 = rearrange(feat1, "b z n d -> (b z) n d")
            flat_mask = rearrange(mask, "b z -> (b z)")
            message_mask = repeat(
                flat_mask, "bz -> bz p c", p=p1 * p2, c=channels
            )
            write_audit: dict[str, Any] = {}
            for layer, name in zip(self.layers, self.layer_names):
                if name == "image":
                    feat0 = layer(feat0, (height, width))
                elif name == "hist2image":
                    patches = sample_true_zone_rectangles(
                        embeddings, rect_data, self.conv_patch_size, (p1, p2)
                    )
                    queries = rearrange(
                        patches, "b z c p1 p2 -> (b z) (p1 p2) c"
                    )
                    messages = layer(queries, feat1)
                    messages[~message_mask] = 0
                    feat0, write_audit = official_layout_write(
                        feat0,
                        messages,
                        patch_info,
                        self.conv_patch_size,
                        (height, width),
                    )
            self.last_formal_d_audit = {
                "fusion": "D_TRUE_READ_OFFICIAL_WRITE",
                "uniform_equivalence_path": False,
                "official_forward_used": False,
                "read_path": "stage2_true_per_zone_vectorized_grid_sample",
                "write_path": "official_8x8_dense_layout",
                "resolution": [height, width],
                "zone_patch_shape": [p1, p2],
                "valid_zone_count": int(torch.count_nonzero(mask).item()),
                "new_learnable_parameters": 0,
                **write_audit,
            }
            return rearrange(feat0, "b (h w) c -> b c h w", h=height)

    FormalDTransformerFusion.__name__ = "FormalDTransformerFusion"
    return FormalDTransformerFusion


def activate_formal_d(model: torch.nn.Module) -> dict[str, Any]:
    before = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    names = ("cross_atten1", "cross_atten2", "cross_atten3")
    for name in names:
        official = getattr(model.decoder, name)
        replacement_type = _replacement_class(type(official))
        replacement = replacement_type(
            int(official.positional_encodings.shape[1]), list(official.max_resolution)
        )
        replacement.load_state_dict(official.state_dict(), strict=True)
        replacement = replacement.to(next(model.parameters()).device)
        setattr(model.decoder, name, replacement)
    after = model.state_dict()
    if set(before) != set(after) or any(
        not torch.equal(before[key], value.detach().cpu()) for key, value in after.items()
    ):
        raise RuntimeError("formal DELTAR-D activation changed checkpoint state")
    return {
        "fusion": "D_TRUE_READ_OFFICIAL_WRITE",
        "adapted_modules": list(names),
        "state_key_count": len(before),
        "state_keys_exact": True,
        "state_values_bit_exact": True,
        "new_learnable_parameters": 0,
    }


def fusion_audits(model: torch.nn.Module) -> dict[str, Any]:
    return {
        name: dict(getattr(model.decoder, name).last_formal_d_audit)
        for name in ("cross_atten1", "cross_atten2", "cross_atten3")
    }
