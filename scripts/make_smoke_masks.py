"""
Generate physical mono LCD smoke-test masks as .npy files.

All masks use the canonical physical mono representation:

- ``subpixel_axis=0``:  ``[3H, W]``   (height tripled)
- ``subpixel_axis=1``:  ``[H, 3W]``   (width tripled)

Usage::

    python scripts/make_smoke_masks.py \\
        --output-dir plans/generated_masks \\
        --logical-shape 540 2560 \\
        --subpixel-axis 0

Environment variables (fallbacks when CLI flags are omitted):

    OPTIC_SYSTEM_LCD_LOGICAL_H
    OPTIC_SYSTEM_LCD_LOGICAL_W
    OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_repo_on_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate physical mono LCD smoke-test masks"
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="output directory for .npy mask files",
    )
    parser.add_argument(
        "--logical-shape",
        nargs=2,
        type=int,
        default=None,
        help="LCD RGB buffer size (height width), e.g. 540 2560",
    )
    parser.add_argument(
        "--subpixel-axis",
        type=int,
        choices=[0, 1],
        default=None,
        help="axis to expand by 3: 0 = height, 1 = width",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _ensure_repo_on_path()

    logical_h = args.logical_shape[0] if args.logical_shape else None
    logical_w = args.logical_shape[1] if args.logical_shape else None

    if logical_h is None:
        logical_h = int(os.environ.get("OPTIC_SYSTEM_LCD_LOGICAL_H", "0"))
    if logical_w is None:
        logical_w = int(os.environ.get("OPTIC_SYSTEM_LCD_LOGICAL_W", "0"))
    if logical_h <= 0 or logical_w <= 0:
        print(
            "Error: --logical-shape is required (or set "
            "OPTIC_SYSTEM_LCD_LOGICAL_H / OPTIC_SYSTEM_LCD_LOGICAL_W)"
        )
        return 1

    subpixel_axis = args.subpixel_axis
    if subpixel_axis is None:
        val = os.environ.get("OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS", "")
        if val in ("0", "1"):
            subpixel_axis = int(val)
        else:
            print(
                "Error: --subpixel-axis is required (or set "
                "OPTIC_SYSTEM_LCD_SUBPIXEL_AXIS=0|1)"
            )
            return 1

    if subpixel_axis == 0:
        phys_h, phys_w = logical_h * 3, logical_w
    else:
        phys_h, phys_w = logical_h, logical_w * 3

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"LCD logical shape: {logical_h}×{logical_w}")
    print(f"Physical mono shape: {phys_h}×{phys_w}  (axis {subpixel_axis})")
    print(f"Output: {output_dir}")

    _write_mask(output_dir / "all_black.npy", phys_h, phys_w, 0)
    _write_mask(output_dir / "all_white.npy", phys_h, phys_w, 255)

    stripe = max(phys_w // 10, 3)
    stripe = (stripe // 3) * 3
    if stripe < 3:
        stripe = 3
    _write_stripes_vertical(output_dir / "vertical_stripes_coarse.npy",
                           phys_h, phys_w, stripe)

    stripe_h = max(phys_h // 10, 3)
    _write_stripes_horizontal(output_dir / "horizontal_stripes_coarse.npy",
                              phys_h, phys_w, stripe_h)

    print("Generated: all_black, all_white, vertical_stripes_coarse, "
          "horizontal_stripes_coarse")
    return 0


def _write_mask(path: Path, h: int, w: int, value: int) -> None:
    arr = np.full((h, w), value, dtype=np.uint8)
    np.save(str(path), arr)


def _write_stripes_vertical(path: Path, h: int, w: int, stripe_w: int) -> None:
    arr = np.zeros((h, w), dtype=np.uint8)
    for x in range(0, w, stripe_w * 2):
        end = min(x + stripe_w, w)
        arr[:, x:end] = 255
    np.save(str(path), arr)


def _write_stripes_horizontal(path: Path, h: int, w: int, stripe_h: int) -> None:
    arr = np.zeros((h, w), dtype=np.uint8)
    for y in range(0, h, stripe_h * 2):
        end = min(y + stripe_h, h)
        arr[y:end, :] = 255
    np.save(str(path), arr)


if __name__ == "__main__":
    raise SystemExit(main())
