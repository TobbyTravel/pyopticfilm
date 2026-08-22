# SPDX-License-Identifier: GPL-3.0-or-later
"""One-shot import rewriter for the pyopticfilm extraction."""

from __future__ import annotations

import re
from pathlib import Path

OLD = "negpy.infrastructure.scanners.plustek"
NEW = "pyopticfilm"

BRINGUP_BLOCK = '''def clamp_area(area: Area) -> Area:
    """Clamp a normalized TA rect to ``0..1`` with a tiny positive size."""
    x1, y1, x2, y2 = (float(v) for v in area)
    x1 = max(0.0, min(1.0, x1))
    y1 = max(0.0, min(1.0, y1))
    x2 = max(0.0, min(1.0, x2))
    y2 = max(0.0, min(1.0, y2))
    if x2 <= x1:
        x2 = min(1.0, x1 + 1e-3)
    if y2 <= y1:
        y2 = min(1.0, y1 + 1e-3)
    return (x1, y1, x2, y2)


def image_crop_to_scan_area(model: Any, crop_norm: Area) -> Area:
    """Map Prescan crop widget coords → TA ``area`` for ``compute_geometry``."""
    del model  # kept for call-site compatibility; orientation is fixed in assemble()
    return clamp_area(crop_norm)


def scan_area_to_image_crop(model: Any, area: Area) -> Area:
    """Map TA ``area`` → Prescan crop widget coords (inverse of :func:`image_crop_to_scan_area`)."""
    del model
    return clamp_area(area)
'''


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = list((root / "src").rglob("*.py")) + list((root / "tests").rglob("*.py"))
    for path in paths:
        text = path.read_text(encoding="utf-8")
        text = text.replace(OLD, NEW)
        text = text.replace("negpy.infrastructure.scanners.params", "pyopticfilm.scan.bringup")
        if path.name == "bringup.py" and "scan" in path.parts:
            text = re.sub(
                r"def clamp_area\(area: Area\) -> Area:.*?def scan_area_to_image_crop\(model: Any, area: Area\) -> Area:.*?\n    return image_crop_to_scan_area\(model, area\)\n",
                BRINGUP_BLOCK + "\n",
                text,
                count=1,
                flags=re.DOTALL,
            )
        path.write_text(text, encoding="utf-8")
        print(f"rewrote {path.relative_to(root)}")


if __name__ == "__main__":
    main()
