from __future__ import annotations

import json
from pathlib import Path

from scripts.diagnose_valid_pixel_domain import (
    run_valid_pixel_domain_diagnostic,
    summarize_probe_rows,
)


def test_summarize_probe_rows_recommends_excluding_top_rows() -> None:
    rows = [
        {
            "gain_db": 0.0,
            "exposure_us": 100.0,
            "all_peak": 255,
            "all_peak_y": 0,
            "all_peak_x": 7,
            "drop_peak": 40,
            "drop_full_count": 0,
        },
        {
            "gain_db": 0.0,
            "exposure_us": 50000.0,
            "all_peak": 255,
            "all_peak_y": 900,
            "all_peak_x": 1170,
            "drop_peak": 255,
            "drop_full_count": 12000,
        },
    ]

    summary = summarize_probe_rows(rows, full_scale=255, drop_top_rows=1)

    assert summary["evidence"]["top_row_full_scale_observed"] is True
    assert summary["evidence"]["post_drop_full_scale_at_short_exposure"] is False
    assert summary["evidence"]["psf_region_full_scale_at_long_exposure"] is True
    assert summary["recommended_valid_pixel_domain"] == {
        "type": "exclude_top_rows",
        "top_rows": 1,
    }


def test_dry_run_diagnostic_writes_csv_and_json(tmp_path: Path) -> None:
    csv_path, json_path = run_valid_pixel_domain_diagnostic(
        output_dir=tmp_path,
        wavelength_nm=550.0,
        grating=1,
        gain_db=0.0,
        exposures_us=[100.0, 5000.0],
        repeats=2,
        frames_per_burst=3,
        drop_top_rows=1,
        hardware=False,
        camera_index=0,
        lcd_display_index=None,
        lcd_subpixel_axis=None,
        tls_serial=None,
        keep_awake=False,
    )

    assert csv_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["recommended_valid_pixel_domain"]["type"] == "exclude_top_rows"
    assert payload["drop_top_rows"] == 1
    assert payload["pixel_format"] == "RAW8"
