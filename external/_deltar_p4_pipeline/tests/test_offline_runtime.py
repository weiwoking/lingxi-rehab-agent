from __future__ import annotations

from pathlib import Path

import pytest
import torch

from deltar_p4_pipeline.deltar_bridge import (
    audit_checkpoint_state,
    validate_offline_encoder_source,
)
from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.offline_preflight import require_local_file


def test_local_asset_path_rejects_urls_relative_and_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a URL"):
        require_local_file("https://example.invalid/model.pt", "model")
    with pytest.raises(ValueError, match="absolute"):
        require_local_file("model.pt", "model")
    with pytest.raises(FileNotFoundError):
        require_local_file(tmp_path / "missing.pt", "model")
    model = tmp_path / "model.pt"
    model.write_bytes(b"local")
    assert require_local_file(model.resolve(), "model") == model.resolve()


def test_checkpoint_audit_reports_missing_unexpected_and_shape_mismatch() -> None:
    model = torch.nn.Sequential(torch.nn.Linear(3, 2))
    state = model.state_dict()
    assert audit_checkpoint_state(model, dict(state)).as_dict() == {
        "model_state_keys": 2,
        "checkpoint_state_keys": 2,
        "missing_keys": [],
        "unexpected_keys": [],
        "shape_mismatches": [],
    }
    invalid = {
        "0.weight": torch.zeros((3, 2)),
        "extra": torch.zeros(1),
    }
    audit = audit_checkpoint_state(model, invalid)
    assert audit.missing_keys == ("0.bias",)
    assert audit.unexpected_keys == ("extra",)
    assert audit.shape_mismatches[0]["key"] == "0.weight"


def test_encoder_source_requires_literal_pretrained_false(tmp_path: Path) -> None:
    source = tmp_path / "src" / "models" / "encoder.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import timm\nnet = timm.create_model('x', pretrained=False)\n",
        encoding="utf-8",
    )
    assert validate_offline_encoder_source(tmp_path) == source.resolve()
    source.write_text(
        "import timm\nnet = timm.create_model('x', pretrained=True)\n",
        encoding="utf-8",
    )
    with pytest.raises(PipelineError, match="offline-safe"):
        validate_offline_encoder_source(tmp_path)
