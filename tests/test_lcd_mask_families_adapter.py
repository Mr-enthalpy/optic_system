from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from capture import mask_family_adapter as adapter


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _instance_spec(mask_id: str = "stripes_test_000") -> dict:
    return {
        "schema_version": "lcd_mask_families.v0.1",
        "family_id": "stripes",
        "family_version": "0.1.0",
        "parameters": {
            "angle_rad": 0.0,
            "period": 0.25,
            "phase_rad": 0.0,
            "duty": 0.5,
        },
        "grid": {
            "coordinate_frame": "normalized_lcd_pupil",
            "shape_hw": [12, 10],
        },
        "projection": {
            "output_dtype": "uint8",
            "value_range": [0, 255],
            "quantization": "round",
            "clip": True,
            "normalize": "none",
        },
        "identity": {"mask_id": mask_id},
        "metadata": {"role": "optic_system_adapter_test"},
    }


def _blocks_spec(mask_id: str = "blocks_test_001") -> dict:
    return {
        "schema_version": "lcd_mask_families.v0.1",
        "family_id": "blocks",
        "family_version": "0.1.0",
        "parameters": {
            "block_h": 3,
            "block_w": 2,
            "offset_y": 0,
            "offset_x": 0,
            "invert": False,
        },
        "grid": {
            "coordinate_frame": "pixel_index",
            "shape_hw": [12, 10],
        },
        "projection": {
            "output_dtype": "uint8",
            "value_range": [0, 255],
            "quantization": "round",
            "clip": True,
            "normalize": "none",
        },
        "identity": {"mask_id": mask_id},
        "metadata": {"role": "optic_system_adapter_test"},
    }


def test_missing_dependency_reports_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import_module = adapter.importlib.import_module

    def fake_import_module(name: str):
        if name == "lcd_mask_families":
            raise ModuleNotFoundError("No module named 'lcd_mask_families'", name=name)
        return real_import_module(name)

    monkeypatch.setattr(adapter.importlib, "import_module", fake_import_module)

    assert adapter.is_lcd_mask_families_available() is False
    with pytest.raises(adapter.LcdMaskFamiliesUnavailableError, match="Optional dependency"):
        adapter.render_mask_instance_file("unused.json")


def test_render_mask_instance_file_returns_neutral_capture_mask(tmp_path: Path) -> None:
    pytest.importorskip("lcd_mask_families")
    spec_path = _write_json(tmp_path / "stripes_instance.json", _instance_spec())

    rendered = adapter.render_mask_instance_file(spec_path)

    assert isinstance(rendered, adapter.RenderedCaptureMask)
    assert isinstance(rendered.mask, np.ndarray)
    assert rendered.mask.shape == (12, 10)
    assert rendered.mask_id == "stripes_test_000"
    assert rendered.mask_hash
    assert rendered.family_id == "stripes"
    assert rendered.family_version == "0.1.0"
    assert rendered.grid["shape_hw"] == [12, 10]
    assert rendered.projection["output_dtype"] == "uint8"
    assert rendered.renderer["contract_version"] == "lcd_mask_families.v0.1"
    assert rendered.renderer["adapter_role"] == "experimental_handoff_consumer"
    assert rendered.metadata["source_spec_path"] == str(spec_path)


def test_render_mask_sequence_file_preserves_order(tmp_path: Path) -> None:
    pytest.importorskip("lcd_mask_families")
    sequence_path = _write_json(
        tmp_path / "sequence.json",
        {
            "schema_version": "lcd_mask_families.v0.1",
            "sequence_id": "optic_system_adapter_sequence",
            "masks": [
                _instance_spec("sequence_stripes_000"),
                _blocks_spec("sequence_blocks_001"),
            ],
            "metadata": {"role": "optic_system_adapter_test"},
        },
    )

    rendered = adapter.render_mask_sequence_file(sequence_path)

    assert [item.mask_id for item in rendered] == [
        "sequence_stripes_000",
        "sequence_blocks_001",
    ]
    assert [item.family_id for item in rendered] == ["stripes", "blocks"]


def test_rendered_capture_mask_metadata_is_json_friendly(tmp_path: Path) -> None:
    pytest.importorskip("lcd_mask_families")
    spec_path = _write_json(tmp_path / "stripes_instance.json", _instance_spec())

    rendered = adapter.render_mask_instance_file(spec_path)
    payload = rendered.capture_metadata()

    json.dumps(payload)
    assert payload["mask_hash"] == rendered.mask_hash
    assert payload["grid"]["coordinate_frame"] == "normalized_lcd_pupil"
    assert payload["renderer"]["name"] == "lcd_mask_families"
