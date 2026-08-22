# SPDX-License-Identifier: GPL-3.0-or-later
"""Audit a multi-exposure short/long bracket (and optional SF merged TIFF).

Usage::

    python -m tools.audit_me_bracket short.tif long.tif [--merged ours.tif] [--sf sf.tif]

Reports mean Y, clip fractions, fitted exposure ratio, corr(merged, short/long),
and optional shadow-ROI SNR. Does not modify files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def _load(path: Path) -> np.ndarray:
    from pyopticfilm.image import load_rgb16_tiff

    rgb, _dpi = load_rgb16_tiff(path)
    return np.asarray(rgb, dtype=np.float64)


def _mean_y(rgb: np.ndarray) -> float:
    return float(rgb.mean() / 65535.0)


def _clip_frac(rgb: np.ndarray, thr: float = 0.92 * 65535.0) -> tuple[float, float, float]:
    return tuple(float(np.mean(rgb[:, :, c] >= thr)) for c in range(3))  # type: ignore[return-value]


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    x = a.reshape(-1).astype(np.float64)
    y = b.reshape(-1).astype(np.float64)
    x = x - x.mean()
    y = y - y.mean()
    d = float(np.sqrt(np.sum(x * x) * np.sum(y * y)))
    if d < 1e-12:
        return 0.0
    return float(np.sum(x * y) / d)


def _shadow_snr(rgb: np.ndarray, mask: np.ndarray) -> float:
    """Mean / std on mask (higher is cleaner dense film)."""
    vals = rgb[mask]
    if vals.size < 16:
        return float("nan")
    std = float(vals.std())
    if std < 1e-6:
        return float("inf")
    return float(vals.mean() / std)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("short", type=Path, help="Linear short-exposure RGB16 TIFF")
    parser.add_argument("long", type=Path, help="Linear long-exposure RGB16 TIFF")
    parser.add_argument(
        "--merged",
        type=Path,
        default=None,
        help="Optional merged TIFF (else runs SNR/IVW on short+long)",
    )
    parser.add_argument(
        "--sf",
        type=Path,
        default=None,
        help="Optional SilverFast ME TIFF for same-bracket abs-diff",
    )
    parser.add_argument(
        "--exposure-short", type=int, default=14000, help="USB short LPERIOD"
    )
    parser.add_argument(
        "--exposure-long", type=int, default=42000, help="USB long LPERIOD"
    )
    args = parser.parse_args(argv)

    short = _load(args.short)
    long = _load(args.long)
    if short.shape != long.shape:
        print(f"shape mismatch: short {short.shape} vs long {long.shape}", file=sys.stderr)
        return 1

    from pyopticfilm.scan.exposure_merge import merge_exposures_result

    if args.merged is not None:
        merged = _load(args.merged)
    else:
        result = merge_exposures_result(
            short.astype(np.uint16),
            long.astype(np.uint16),
            exposure_short=args.exposure_short,
            exposure_long=args.exposure_long,
            align_shift=(0.0, 0.0),
        )
        merged = result.rgb.astype(np.float64)
        stats = result.fusion_stats
        print("--- SNR/IVW (computed) ---")
        if stats is not None:
            print(
                f"  weights short={stats.mean_short_weight:.4g} "
                f"long={stats.mean_long_weight:.4g} "
                f"r={stats.exposure_ratio_used:.4g} "
                f"residual={stats.mean_residual_confidence:.3f} "
                f"both-zero={stats.zero_weight_fraction:.2%}"
            )

    usb_r = args.exposure_long / float(args.exposure_short)
    # Dense ROI heuristic: darkest 10% of short luma.
    lum = short.mean(axis=2)
    thr = float(np.percentile(lum, 10.0))
    shadow = lum <= thr

    print("--- planes ---")
    print(f"  shape {short.shape[1]}×{short.shape[0]}")
    print(
        f"  short meanY={_mean_y(short):.4f}  "
        f"clip@92% RGB={tuple(f'{c:.2%}' for c in _clip_frac(short))}"
    )
    print(
        f"  long  meanY={_mean_y(long):.4f}  "
        f"clip@92% RGB={tuple(f'{c:.2%}' for c in _clip_frac(long))}"
    )
    print(
        f"  merged meanY={_mean_y(merged):.4f}  "
        f"clip@92% RGB={tuple(f'{c:.2%}' for c in _clip_frac(merged))}"
    )
    print(f"  USB ratio={usb_r:.3f}  mean(long)/mean(short)={long.mean() / max(short.mean(), 1):.3f}")
    print("--- correlations ---")
    print(f"  corr(merged, long)  = {_corr(merged, long):.6f}")
    print(f"  corr(merged, short) = {_corr(merged, short):.6f}")
    print("--- shadow ROI (darkest 10% of short) ---")
    print(f"  SNR short = {_shadow_snr(short, shadow):.3f}")
    print(f"  SNR long  = {_shadow_snr(long / usb_r, shadow):.3f}")
    print(f"  SNR merged= {_shadow_snr(merged, shadow):.3f}")

    if args.sf is not None:
        sf = _load(args.sf)
        if sf.shape != merged.shape:
            print(f"SF shape {sf.shape} ≠ merged {merged.shape}", file=sys.stderr)
            return 1
        mad = float(np.mean(np.abs(merged - sf)))
        print("--- vs SilverFast ME ---")
        print(f"  mean abs diff = {mad:.1f} DN")
        print(f"  corr(merged, SF) = {_corr(merged, sf):.6f}")
        print(f"  SF meanY={_mean_y(sf):.4f}")

    print(
        "\nVerdict hint: if corr(merged,long)≈1 and long barely clips, "
        "IVW≈long is expected; chase SF 'look' in post/NegPy. "
        "If long clips and merged still tracks long in those channels, "
        "per-channel confidence / roll-off need work."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
