# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal 8200i SE scan at 1200 dpi with colour + infrared passes.

Requires WinUSB/libusb binding and ``pip install tifffile`` for TIFF output::

    uv run python scripts/scan_1200_ir.py
    uv run python scripts/scan_1200_ir.py --out ./frames
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pyopticfilm import Scanner
from pyopticfilm.scan.bringup import bringup_scan_geometry

DPI = 1200


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("scan_1200_ir"),
        help="Output folder for colour.tif and ir.tif",
    )
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    with Scanner.open() as scanner:
        geometry, _meta = bringup_scan_geometry(scanner.model, DPI, profile="preview_safe")
        print(f"Scanning {scanner.model.model} at {DPI} dpi (preview_safe window)…")

        color = scanner.scan(resolution=DPI, mode="color", geometry=geometry)
        color_path = color.save_tiff(out / "color.tif")
        print(f"Colour: {color.rgb.shape} → {color_path}")

        ir = scanner.scan(resolution=DPI, mode="infrared", geometry=geometry)
        ir_path = ir.save_tiff(out / "ir.tif")
        print(f"Infrared: {ir.rgb.shape} → {ir_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
