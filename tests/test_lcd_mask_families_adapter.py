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


def _embedding_profile(**updates) -> dict:
    profile = {
        **_pupil_profile(),
        "aperture_window": [5, 7, 15, 19],
    }
    profile.update(updates)
    return profile


def _rendered_capture_mask(
    *,
    shape: tuple[int, int] = (12, 10),
    coordinate_frame: str = "normalized_lcd_pupil",
    dtype=np.uint8,
    projection_dtype: str = "uint8",
    mask_id: str = "manual_mask_000",
) -> adapter.RenderedCaptureMask:
    mask = np.arange(shape[0] * shape[1], dtype=dtype).reshape(shape)
    return adapter.RenderedCaptureMask(
        mask=mask,
        mask_id=mask_id,
        mask_hash=f"{mask_id}_hash",
        family_id="manual_family",
        family_version="0.1.0",
        grid={
            "coordinate_frame": coordinate_frame,
            "shape_hw": [shape[0], shape[1]],
        },
        projection={"output_dtype": projection_dtype},
        renderer={"name": "test"},
        metadata={"source": "test"},
    )


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


def test_exact_aperture_window_embedding_succeeds() -> None:
    rendered = _rendered_capture_mask()

    physical = adapter.embed_rendered_mask_for_pupil_profile(
        rendered,
        _embedding_profile(),
        lcd_shape_hw=(30, 40),
        outside_value=3,
    )

    assert isinstance(physical, adapter.RenderedPhysicalMask)
    assert physical.local_mask.shape == (12, 10)
    assert physical.physical_mask.shape == (30, 40)
    np.testing.assert_array_equal(physical.local_mask, rendered.mask)
    np.testing.assert_array_equal(physical.physical_mask[7:19, 5:15], rendered.mask)
    outside = physical.physical_mask.copy()
    outside[7:19, 5:15] = 3
    assert np.all(outside == 3)
    assert not physical.local_mask.flags.writeable
    assert not physical.physical_mask.flags.writeable


def test_physical_embedding_capture_metadata_records_placement() -> None:
    physical = adapter.embed_rendered_mask_for_pupil_profile(
        _rendered_capture_mask(),
        _embedding_profile(),
        lcd_shape_hw=(30, 40),
        outside_value=2,
    )

    payload = physical.capture_metadata()

    assert payload["usage_scope"] == "physical_lcd_embedding"
    assert payload["physical_placement_implemented"] is True
    assert payload["pupil_profile_id"] == "pupil_profile_test_000"
    assert payload["lcd_coordinate_convention"] == "physical_mono_yx"
    assert payload["lcd_display_index"] == 2
    assert payload["subpixel_axis"] == 1
    assert payload["lcd_physical_center"] == [48.5, 96.25]
    assert payload["lcd_physical_radius"] == 31.0
    assert payload["aperture_window"] == [5, 7, 15, 19]
    assert payload["lcd_shape_hw"] == [30, 40]
    assert payload["placement_window_xyxy"] == [5, 7, 15, 19]
    assert payload["outside_value"] == 2
    assert payload["local_mask_shape_hw"] == [12, 10]
    assert payload["physical_mask_shape_hw"] == [30, 40]
    assert payload["renderer"]["physical_placement_implemented"] is True


def test_profile_unaware_render_is_not_physical_until_embedded() -> None:
    rendered = _rendered_capture_mask()

    assert rendered.capture_metadata()["usage_scope"] == "dry_run_profile_unaware"

    physical = adapter.embed_rendered_mask_for_pupil_profile(
        rendered,
        _embedding_profile(),
        lcd_shape_hw=(30, 40),
    )

    assert physical.capture_metadata()["usage_scope"] == "physical_lcd_embedding"


def test_sequence_embedding_preserves_order(tmp_path: Path) -> None:
    pytest.importorskip("lcd_mask_families")
    sequence_path = _write_json(
        tmp_path / "sequence.json",
        {
            "schema_version": "lcd_mask_families.v0.1",
            "sequence_id": "optic_system_embedding_sequence",
            "masks": [
                _instance_spec("sequence_stripes_000"),
                _blocks_spec("sequence_blocks_001"),
            ],
        },
    )

    rendered = adapter.render_and_embed_mask_sequence_file_for_pupil_profile(
        sequence_path,
        _embedding_profile(),
        lcd_shape_hw=(30, 40),
    )

    assert [item.mask_id for item in rendered] == [
        "sequence_stripes_000",
        "sequence_blocks_001",
    ]
    assert all(item.physical_mask.shape == (30, 40) for item in rendered)


@pytest.mark.parametrize(
    ("window", "message"),
    [
        ([5, 7, 15, 18], "height"),
        ([5, 7, 14, 19], "width"),
        ([-1, 7, 15, 19], "non-negative"),
        ([5, 7, 41, 19], "outside"),
        ([5, 7, 15, 31], "outside"),
        ([5, 7, 5, 19], "x1 > x0"),
        ([5, 7, 15, 7], "y1 > y0"),
    ],
)
def test_embedding_rejects_invalid_aperture_windows(window: list[int], message: str) -> None:
    with pytest.raises(adapter.MaskFamilyEmbeddingError, match=message):
        adapter.embed_rendered_mask_for_pupil_profile(
            _rendered_capture_mask(),
            _embedding_profile(aperture_window=window),
            lcd_shape_hw=(30, 40),
        )


def test_embedding_requires_pupil_profile() -> None:
    with pytest.raises(adapter.MaskFamilyEmbeddingError, match="requires a PupilProfile"):
        adapter.embed_rendered_mask_for_pupil_profile(
            _rendered_capture_mask(),
            None,
            lcd_shape_hw=(30, 40),
        )


def test_embedding_rejects_center_radius_only_profile() -> None:
    with pytest.raises(adapter.MaskFamilyEmbeddingError, match="aperture_window"):
        adapter.embed_rendered_mask_for_pupil_profile(
            _rendered_capture_mask(),
            _pupil_profile(),
            lcd_shape_hw=(30, 40),
        )


def test_embedding_rejects_unsupported_coordinate_frame() -> None:
    with pytest.raises(adapter.MaskFamilyEmbeddingError, match="unsupported coordinate_frame"):
        adapter.embed_rendered_mask_for_pupil_profile(
            _rendered_capture_mask(coordinate_frame="full_lcd"),
            _embedding_profile(),
            lcd_shape_hw=(30, 40),
        )


def test_embedding_rejects_float_masks() -> None:
    with pytest.raises(adapter.MaskFamilyEmbeddingError, match="uint8"):
        adapter.embed_rendered_mask_for_pupil_profile(
            _rendered_capture_mask(dtype=np.float32, projection_dtype="float32"),
            _embedding_profile(),
            lcd_shape_hw=(30, 40),
        )


def test_embedding_rejects_non_uint8_projection() -> None:
    with pytest.raises(adapter.MaskFamilyEmbeddingError, match="output_dtype"):
        adapter.embed_rendered_mask_for_pupil_profile(
            _rendered_capture_mask(projection_dtype="float32"),
            _embedding_profile(),
            lcd_shape_hw=(30, 40),
        )


def test_embedding_rejects_invalid_lcd_shape() -> None:
    with pytest.raises(adapter.MaskFamilyEmbeddingError, match="positive"):
        adapter.embed_rendered_mask_for_pupil_profile(
            _rendered_capture_mask(),
            _embedding_profile(),
            lcd_shape_hw=(0, 40),
        )


def test_embedding_rejects_grid_shape_mismatch() -> None:
    rendered = _rendered_capture_mask()
    rendered = adapter.RenderedCaptureMask(
        mask=rendered.mask,
        mask_id=rendered.mask_id,
        mask_hash=rendered.mask_hash,
        family_id=rendered.family_id,
        family_version=rendered.family_version,
        grid={"coordinate_frame": "normalized_lcd_pupil", "shape_hw": [12, 9]},
        projection=rendered.projection,
        renderer=rendered.renderer,
        metadata=rendered.metadata,
    )

    with pytest.raises(adapter.MaskFamilyEmbeddingError, match="shape_hw"):
        adapter.embed_rendered_mask_for_pupil_profile(
            rendered,
            _embedding_profile(),
            lcd_shape_hw=(30, 40),
        )
