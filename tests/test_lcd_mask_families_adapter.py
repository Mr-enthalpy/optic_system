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


def _pupil_profile() -> dict:
    return {
        "pupil_profile_id": "pupil_profile_test_000",
        "lcd_coordinate_convention": "physical_mono_yx",
        "lcd_display_index": 2,
        "subpixel_axis": 1,
        "lcd_physical_center": [48.5, 96.25],
        "lcd_physical_radius": 31.0,
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
    assert rendered.usage_scope == "dry_run_profile_unaware"
    assert rendered.pupil_profile is None
    assert rendered.capture_metadata()["usage_scope"] == "dry_run_profile_unaware"


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


def test_profile_aware_render_requires_pupil_profile(tmp_path: Path) -> None:
    with pytest.raises(adapter.MaskFamilyProfileError, match="require a PupilProfile"):
        adapter.render_mask_instance_file_for_pupil_profile(
            tmp_path / "unused.json",
            None,
        )


def test_profile_aware_render_requires_effective_pupil_geometry(tmp_path: Path) -> None:
    profile = dict(_pupil_profile())
    profile.pop("lcd_physical_radius")
    with pytest.raises(adapter.MaskFamilyProfileError, match="radius or aperture_window"):
        adapter.render_mask_instance_file_for_pupil_profile(
            tmp_path / "unused.json",
            profile,
        )


def test_profile_aware_capture_metadata_contains_pupil_geometry(tmp_path: Path) -> None:
    pytest.importorskip("lcd_mask_families")
    spec_path = _write_json(tmp_path / "stripes_instance.json", _instance_spec())

    rendered = adapter.render_mask_instance_file_for_pupil_profile(
        spec_path,
        _pupil_profile(),
    )
    payload = rendered.capture_metadata()

    assert rendered.usage_scope == "pupil_profile_metadata_bound"
    assert payload["pupil_profile_id"] == "pupil_profile_test_000"
    assert payload["lcd_coordinate_convention"] == "physical_mono_yx"
    assert payload["lcd_display_index"] == 2
    assert payload["subpixel_axis"] == 1
    assert payload["lcd_physical_center"] == [48.5, 96.25]
    assert payload["lcd_physical_radius"] == 31.0
    assert payload["renderer"]["profile_binding"] == "pupil_profile_metadata_only"
    assert payload["renderer"]["physical_placement_implemented"] is False
    assert payload["renderer"]["capture_gate"] == "requires_pupil_profile"
