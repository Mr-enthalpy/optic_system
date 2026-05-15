from __future__ import annotations

from pathlib import Path

import pytest

from diagnostics.valid_pixel_domain import build_valid_pixel_mask


def test_full_frame_mask_all_true() -> None:
    domain = build_valid_pixel_mask((4, 5), None)

    assert domain.type == "full_frame"
    assert domain.mask.shape == (4, 5)
    assert bool(domain.mask.all()) is True
    assert domain.valid_pixel_count == 20
    assert domain.invalid_pixel_count == 0


def test_exclude_top_rows_mask() -> None:
    domain = build_valid_pixel_mask(
        (5, 4),
        {
            "type": "exclude_top_rows",
            "top_rows": 2,
            "source": "unit_test",
        },
    )

    assert domain.type == "exclude_top_rows"
    assert bool(domain.mask[:2, :].any()) is False
    assert bool(domain.mask[2:, :].all()) is True
    assert domain.valid_pixel_count == 12
    assert domain.invalid_pixel_count == 8
    assert domain.policy_json()["top_rows"] == 2


def test_exclude_top_rows_rejects_invalid_count() -> None:
    with pytest.raises(ValueError, match="0 <= top_rows < 5"):
        build_valid_pixel_mask((5, 4), {"type": "exclude_top_rows", "top_rows": 5})


def test_source_artifact_hash_recorded(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text('{"ok": true}', encoding="utf-8")

    domain = build_valid_pixel_mask(
        (4, 4),
        {
            "type": "exclude_top_rows",
            "top_rows": 1,
            "source_artifact": str(artifact),
            "source": "unit_test",
        },
    )

    policy = domain.policy_json()
    assert policy["source_artifact_exists"] is True
    assert isinstance(policy["artifact_hash"], str)
    assert len(policy["artifact_hash"]) == 64


def test_missing_source_artifact_fails_by_default(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    with pytest.raises(FileNotFoundError, match="source_artifact not found"):
        build_valid_pixel_mask(
            (4, 4),
            {
                "type": "exclude_top_rows",
                "top_rows": 1,
                "source_artifact": str(missing),
            },
        )


def test_missing_source_artifact_can_be_allowed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    domain = build_valid_pixel_mask(
        (4, 4),
        {
            "type": "exclude_top_rows",
            "top_rows": 1,
            "source_artifact": str(missing),
            "allow_missing_source_artifact": True,
        },
    )

    policy = domain.policy_json()
    assert policy["source_artifact_exists"] is False
    assert policy["artifact_hash"] is None
