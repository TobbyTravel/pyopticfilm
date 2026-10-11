# SPDX-License-Identifier: GPL-3.0-or-later
"""Part A: native sawtooth / channel-split metrics on Scan Lab raw TIFFs."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import tifffile

TIFFS = {
    1440: Path(r"c:\Users\jbone\Desktop\examples\ScanLab_TIFFS\tiff-1440.tiff"),
    1800: Path(r"c:\Users\jbone\Desktop\examples\ScanLab_TIFFS\tiff-1800.tiff"),
    2400: Path(r"c:\Users\jbone\Desktop\examples\ScanLab_TIFFS\tiff-2400.tiff"),
    7200: Path(r"c:\Users\jbone\Desktop\examples\ScanLab_TIFFS\tiff-7200.tiff"),
}


def load_tl_crop(path: Path, *, h: int = 400, w: int = 600) -> np.ndarray:
    """Top-left crop as HxWx3 uint16 (memory-map friendly)."""
    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        arr = page.asarray(out="memmap")
        # shapes: HxWx3 or HxW (unlikely)
        if arr.ndim == 2:
            crop = np.asarray(arr[:h, :w])
            crop = np.stack([crop, crop, crop], axis=-1)
        else:
            crop = np.asarray(arr[:h, :w, :3])
        return crop


def channel_edge_rows(ch: np.ndarray, *, bright_above: bool = True) -> np.ndarray:
    """Per-column row of strongest bright→dark (or dark→bright) step."""
    chf = ch.astype(np.float32)
    if bright_above:
        diff = chf[:-1] - chf[1:]
    else:
        diff = chf[1:] - chf[:-1]
    return np.argmax(diff, axis=0).astype(np.float64)


def channel_edge_cols(ch: np.ndarray, *, bright_left: bool = True) -> np.ndarray:
    """Per-row column of strongest left/right step (vertical edge)."""
    chf = ch.astype(np.float32)
    if bright_left:
        diff = chf[:, :-1] - chf[:, 1:]
    else:
        diff = chf[:, 1:] - chf[:, :-1]
    return np.argmax(diff, axis=1).astype(np.float64)


def detrend_resid(y: np.ndarray) -> np.ndarray:
    x = np.arange(len(y), dtype=np.float64)
    coef = np.polyfit(x, y, 1)
    return y - (coef[0] * x + coef[1])


def autocorr_lag(resid: np.ndarray, max_lag: int = 32) -> tuple[int | None, float, np.ndarray]:
    r = resid - resid.mean()
    if float(np.dot(r, r)) < 1e-6:
        return None, float("nan"), np.zeros(max_lag + 1)
    corr = np.correlate(r, r, mode="full")
    mid = len(corr) // 2
    corr = corr[mid : mid + max_lag + 1].astype(np.float64)
    corr = corr / (corr[0] + 1e-9)
    lag = None
    peak = float("nan")
    for k in range(2, min(max_lag, len(corr) - 1)):
        if corr[k] > 0.25 and corr[k] >= corr[k - 1] and corr[k] >= corr[k + 1]:
            lag = k
            peak = float(corr[k])
            break
    return lag, peak, corr


def summarize_edge(name: str, positions: np.ndarray) -> dict:
    resid = detrend_resid(positions)
    lag, peak, _ = autocorr_lag(resid)
    eo = float(resid[0::2].mean() - resid[1::2].mean())
    return {
        "name": name,
        "mean": float(positions.mean()),
        "resid_std": float(resid.std()),
        "resid_pp": float(resid.max() - resid.min()),
        "lag": lag,
        "peak": peak,
        "even_odd": eo,
    }


def apply_col_stagger(rgb: np.ndarray, pattern: tuple[int, ...]) -> np.ndarray:
    """Shift columns by pattern[x%n] (host unstagger inverse / trial)."""
    h, w, c = rgb.shape
    max_s = max(pattern)
    # Pull from later rows into earlier (unstagger): out[y,x] = in[y+shift,x]
    out = np.zeros((h - max_s, w, c), dtype=rgb.dtype)
    n = len(pattern)
    for x in range(w):
        s = pattern[x % n]
        out[:, x, :] = rgb[s : s + out.shape[0], x, :]
    return out


def apply_row_stagger(rgb: np.ndarray, pattern: tuple[int, ...]) -> np.ndarray:
    """Shift rows by pattern[y%n] in X — for vertical-edge sawtooth."""
    h, w, c = rgb.shape
    max_s = max(pattern)
    out = np.zeros((h, w - max_s, c), dtype=rgb.dtype)
    n = len(pattern)
    for y in range(h):
        s = pattern[y % n]
        out[y, :, :] = rgb[y, s : s + out.shape[1], :]
    return out


def analyze_crop(dpi: int, rgb: np.ndarray) -> None:
    # Use interior band away from absolute border chrome if present.
    h, w, _ = rgb.shape
    # Horizontal edge: search in top portion, skip leftmost holder strip.
    y0, y1 = 5, min(h, 200)
    x0, x1 = min(80, w // 5), min(w, 80 + 400)
    horiz = rgb[y0:y1, x0:x1]
    # Vertical edge: left portion
    vy0, vy1 = min(40, h // 5), min(h, 40 + 300)
    vx0, vx1 = 5, min(w, 200)
    vert = rgb[vy0:vy1, vx0:vx1]

    print(f"\n=== {dpi} PPI crop={rgb.shape} dtype={rgb.dtype} ===")
    print(f"  horiz ROI {horiz.shape}  vert ROI {vert.shape}")

    # Luminance + per-channel horizontal edge (bright above for holder/film)
    lum = horiz.mean(axis=2)
    # Decide polarity: if top mean > bottom mean → bright above
    bright_above = float(lum[:20].mean()) > float(lum[-20:].mean())
    ey_r = channel_edge_rows(horiz[:, :, 0], bright_above=bright_above)
    ey_g = channel_edge_rows(horiz[:, :, 1], bright_above=bright_above)
    ey_b = channel_edge_rows(horiz[:, :, 2], bright_above=bright_above)
    ey_l = channel_edge_rows(lum, bright_above=bright_above)

    for label, ey in ("L", ey_l), ("R", ey_r), ("G", ey_g), ("B", ey_b):
        s = summarize_edge(f"horiz-{label}", ey)
        print(
            f"  {s['name']:10s} mean={s['mean']:7.2f} std={s['resid_std']:.3f} "
            f"pp={s['resid_pp']:.1f} lag={s['lag']} peak={s['peak']:.3f} "
            f"even-odd={s['even_odd']:+.3f}"
        )
    print(
        f"  channel split G-R={ey_g.mean() - ey_r.mean():+.3f} "
        f"B-R={ey_b.mean() - ey_r.mean():+.3f} bright_above={bright_above}"
    )

    # Vertical edge
    vlum = vert.mean(axis=2)
    bright_left = float(vlum[:, :20].mean()) > float(vlum[:, -20:].mean())
    ex_r = channel_edge_cols(vert[:, :, 0], bright_left=bright_left)
    ex_g = channel_edge_cols(vert[:, :, 1], bright_left=bright_left)
    ex_b = channel_edge_cols(vert[:, :, 2], bright_left=bright_left)
    ex_l = channel_edge_cols(vlum, bright_left=bright_left)
    for label, ex in ("L", ex_l), ("R", ex_r), ("G", ex_g), ("B", ex_b):
        s = summarize_edge(f"vert-{label}", ex)
        print(
            f"  {s['name']:10s} mean={s['mean']:7.2f} std={s['resid_std']:.3f} "
            f"pp={s['resid_pp']:.1f} lag={s['lag']} peak={s['peak']:.3f} "
            f"even-odd={s['even_odd']:+.3f}"
        )
    print(
        f"  channel split G-R(x)={ex_g.mean() - ex_r.mean():+.3f} "
        f"B-R(x)={ex_b.mean() - ex_r.mean():+.3f} bright_left={bright_left}"
    )

    # Unstagger trials on horizontal edge (column Y)
    base_std = summarize_edge("base", ey_l)["resid_std"]
    print("  --- unstagger trials (horiz luminance) ---")
    for pat in ((1, 0), (0, 1), (2, 0), (4, 0)):
        trial = apply_col_stagger(horiz, pat)
        tlum = trial.mean(axis=2)
        ey = channel_edge_rows(tlum, bright_above=bright_above)
        s = summarize_edge(str(pat), ey)
        delta = s["resid_std"] - base_std
        print(
            f"  col{pat} std={s['resid_std']:.3f} (d={delta:+.3f}) "
            f"lag={s['lag']} even-odd={s['even_odd']:+.3f}"
        )

    base_v = summarize_edge("base", ex_l)["resid_std"]
    print("  --- row-stagger trials (vert luminance) ---")
    for pat in ((1, 0), (0, 1), (2, 0)):
        trial = apply_row_stagger(vert, pat)
        tlum = trial.mean(axis=2)
        ex = channel_edge_cols(tlum, bright_left=bright_left)
        s = summarize_edge(str(pat), ex)
        delta = s["resid_std"] - base_v
        print(
            f"  row{pat} std={s['resid_std']:.3f} (d={delta:+.3f}) "
            f"lag={s['lag']} even-odd={s['even_odd']:+.3f}"
        )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("Native 100% analysis of Scan Lab raw TIFFs (no NegPy zoom)")
    for dpi in (1440, 1800, 2400, 7200):
        path = TIFFS[dpi]
        if not path.exists():
            print(f"MISSING {path}")
            continue
        # Smaller crop for 7200 speed; still enough for edge stats.
        h = 300 if dpi < 7200 else 250
        w = 500 if dpi < 7200 else 400
        rgb = load_tl_crop(path, h=h, w=w)
        analyze_crop(dpi, rgb)
    print("\nDONE")


if __name__ == "__main__":
    main()
