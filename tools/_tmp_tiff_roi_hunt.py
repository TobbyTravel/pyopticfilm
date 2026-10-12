# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-ROI lag-2 hunt + unstagger A/B on Scan Lab TIFFs."""

from __future__ import annotations

import sys
from pathlib import Path

from _tmp_analyze_scanlab_tiffs import (
    apply_col_stagger,
    channel_edge_rows,
    load_tl_crop,
    summarize_edge,
)

TIFF_DIR = Path(r"c:\Users\jbone\Desktop\examples\ScanLab_TIFFS")


def scan_rois(dpi: int, path: Path) -> None:
    rgb = load_tl_crop(path, h=500, w=800)
    h, w, _ = rgb.shape
    print(f"\n=== {dpi} multi-ROI ===")
    best = None
    for y0 in range(0, min(200, h - 80), 20):
        for x0 in (40, 80, 120, 200):
            y1 = min(h, y0 + 120)
            x1 = min(w, x0 + 400)
            crop = rgb[y0:y1, x0:x1]
            lum = crop.mean(axis=2)
            ba = float(lum[:15].mean()) > float(lum[-15:].mean())
            ey = channel_edge_rows(lum, bright_above=ba)
            if ey.mean() < 3 or ey.mean() > crop.shape[0] - 4:
                continue
            s = summarize_edge("x", ey)
            if s["lag"] == 2 and s["resid_std"] > 0.3:
                key = (s["resid_std"], abs(s["even_odd"]))
                if best is None or key > best[0]:
                    best = (key, (y0, x0), s, ba, crop)
    if best is None:
        print("  no strong lag-2 ROI found")
        return
    _, (y0, x0), s, ba, crop = best
    print(
        f"  best ROI y0={y0} x0={x0} ba={ba} std={s['resid_std']:.3f} "
        f"eo={s['even_odd']:+.3f} peak={s['peak']:.3f}"
    )
    ey_r = channel_edge_rows(crop[:, :, 0], bright_above=ba)
    ey_g = channel_edge_rows(crop[:, :, 1], bright_above=ba)
    print(f"  G-R={ey_g.mean() - ey_r.mean():+.3f}")
    base = s["resid_std"]
    for pat in ((1, 0), (0, 1)):
        t = apply_col_stagger(crop, pat)
        ey = channel_edge_rows(t.mean(axis=2), bright_above=ba)
        st = summarize_edge("t", ey)
        print(
            f"  unstagger {pat}: std={st['resid_std']:.3f} "
            f"d={st['resid_std'] - base:+.3f} eo={st['even_odd']:+.3f} lag={st['lag']}"
        )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    for dpi, name in (
        (1440, "tiff-1440.tiff"),
        (1800, "tiff-1800.tiff"),
        (2400, "tiff-2400.tiff"),
        (7200, "tiff-7200.tiff"),
    ):
        scan_rois(dpi, TIFF_DIR / name)


if __name__ == "__main__":
    main()
