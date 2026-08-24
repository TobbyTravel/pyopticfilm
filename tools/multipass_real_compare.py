# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-hardware multi-pass SNR comparison on a live GL128 scan.

Loads the physical scanner and the film currently on it, then scans the *same*
loaded film twice — once as a **single pass** (N=1, the short-exposure bin only)
and once as an **N-pass ME merge** (default N=6, three short + three long at the
capture-validated bins) — at the model's maximum resolution over the full
transparency area. The two panels are compared visually and by a simple
shot-noise (high-frequency residue) SNR proxy so the SNR gain from N passes is
directly visible and numerically confirmed.

Outputs to ``--outdir`` (default ``/home/tobby/Pictures``):

* ``multipass_real_single_<DPI>.tif``  — 16-bit archival, single pass, full frame
* ``multipass_real_n6_<DPI>.tif``      — 16-bit archival, N-pass merge, full frame
* ``multipass_real_full.png``          — side-by-side full-frame preview (downscaled
  if needed to keep the PNG under ~10 MB)
* ``multipass_real_crop.png``          — side-by-side high-res central crop (each
  panel is the same physical window of the frame at full pixel resolution, so
  grain is visible to the eye)
* ``multipass_real_metrics.csv``       — per-panel SNR metrics, config, and timings

Note: the 8100 (V2) has no IR channel — this tool does not touch infrared,
and the two panels are otherwise on identical settings (same dpi, area,
apply_calib, lamp warm-up).

::

    env -u PYTHONPATH uv run --all-groups --with pillow \
        python -m tools.multipass_real_compare \
            [--dpi 7200] [--n 6] [--outdir /home/tobby/Pictures]
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _import(name: str):
    return __import__(name)


def _import_module(name: str):
    """Import a subpackage (e.g. ``PIL.Image``) reliably — ``__import__`` on a
    namespace package does not attach submodules as attributes."""
    import importlib

    return importlib.import_module(name)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --- image helpers (PIL for captions) ------------------------------------------


def save_tiff(path: Path, rgb: np.ndarray, dpi: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert _has("tifffile"), "tifffile not importable"
    tf = _import("tifffile")
    if dpi:
        tf.imwrite(path, rgb, photometric="rgb", resolution=(dpi, dpi))
    else:
        tf.imwrite(path, rgb, photometric="rgb")


def save_png(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert _has("PIL"), "PIL not importable"
    Image = _import_module("PIL.Image")
    Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8)).save(path, format="PNG")


def with_caption(rgb: np.ndarray, text: str, bar_h: int = 28) -> np.ndarray:
    arr = np.asarray(rgb, dtype=np.uint8)
    if not _has("PIL") or not text:
        return arr
    Image = _import_module("PIL.Image")
    ImageDraw = _import_module("PIL.ImageDraw")
    out = np.zeros((bar_h + arr.shape[0], arr.shape[1], 3), dtype=np.uint8)
    out[bar_h:] = arr
    pil = Image.fromarray(out).convert("RGB")
    ImageDraw.Draw(pil).text((6, max(2, (bar_h - 15) // 2)), text, fill=(235, 235, 235))
    return np.asarray(pil, dtype=np.uint8)


def hstack(images: list[np.ndarray], gap: int = 4) -> np.ndarray:
    h = max(im.shape[0] for im in images)
    total_w = sum(im.shape[1] for im in images) + gap * (len(images) - 1)
    canvas = np.zeros((h, total_w, 3), dtype=np.uint8)
    x = 0
    for i, im in enumerate(images):
        canvas[: im.shape[0], x : x + im.shape[1]] = im
        x += im.shape[1]
        if i < len(images) - 1:
            x += gap
    return canvas


def vstack(images: list[np.ndarray], gap: int = 4) -> np.ndarray:
    w = max(im.shape[1] for im in images)
    total_h = sum(im.shape[0] for im in images) + gap * (len(images) - 1)
    canvas = np.zeros((total_h, w, 3), dtype=np.uint8)
    y = 0
    for i, im in enumerate(images):
        canvas[y : y + im.shape[0], : im.shape[1]] = im
        y += im.shape[0]
        if i < len(images) - 1:
            y += gap
    return canvas


def downscale_uint8(rgb_u8: np.ndarray, max_dim: int) -> np.ndarray:
    """Downscale a uint8 preview to fit a max side, preserving aspect ratio."""
    im = np.asarray(rgb_u8, dtype=np.uint8)
    h, w = im.shape[:2]
    target = max(h, w)
    if target <= max_dim:
        return np.ascontiguousarray(im)
    scale = max_dim / float(target)
    new_h = max(1, round(h * scale))
    new_w = max(1, round(w * scale))
    Image = _import_module("PIL.Image")
    return np.ascontiguousarray(Image.fromarray(im).resize((new_w, new_h), Image.LANCZOS))


# --- per-panel metrics ----------------------------------------------------------


def box_blur(x: np.ndarray, k: int) -> np.ndarray:
    """k×k box blur (odd kernel), edge-padded back to the input shape."""
    x = np.asarray(x, dtype=np.float64)
    k = max(1, int(k) | 1)
    from numpy.lib.stride_tricks import sliding_window_view

    sw = sliding_window_view(x, (k, k), axis=(0, 1))
    out = sw.mean(axis=(-2, -1))
    H, W = x.shape[:2]
    ph = (H - out.shape[0]) // 2
    pw = (W - out.shape[1]) // 2
    return np.pad(
        out,
        ((ph, H - out.shape[0] - ph), (pw, W - out.shape[1] - pw), (0, 0)),
        mode="edge",
    )


def high_freq_rms_dn(rgb: np.ndarray, k: int = 9) -> float:
    """RMS of (pixel - k×k box blur) across all channels — a shot/noise proxy in DN."""
    a = np.asarray(rgb, dtype=np.float64)
    sm = box_blur(a, k)
    resid = a - sm
    return float(np.sqrt(np.mean(resid**2)))


def central_crop(rgb: np.ndarray, frac: float = 0.5) -> np.ndarray:
    """Central crop covering ``frac`` of each dimension (default 50%)."""
    h, w = rgb.shape[:2]
    ch = max(16, round(h * frac))
    cw = max(16, round(w * frac))
    x0 = (w - cw) // 2
    y0 = (h - ch) // 2
    return rgb[y0 : y0 + ch, x0 : x0 + cw]


def percentile_window(rgb: np.ndarray) -> tuple[float, float]:
    p2 = float(np.percentile(rgb, 2))
    p98 = float(np.percentile(rgb, 98))
    return max(0.0, p2 * 0.9), max(p98 * 1.05, p2 * 0.9 + 10.0)


def to_u8(rgb: np.ndarray, lo: float, hi: float) -> np.ndarray:
    a = np.asarray(rgb, dtype=np.uint16)
    a = a.astype(np.float64)
    return np.clip((a - lo) / max(1.0, hi - lo) * 255.0, 0, 255).astype(np.uint8)


# --- scanner runner -------------------------------------------------------------


@dataclass
class Panel:
    label: str
    rgb: np.ndarray
    dpi: int
    n_passes: int
    exposure_ladder: list[int]
    scan_time_s: float


def _scan_once(scanner, *, dpi: int, n_passes: int, area=None) -> Panel:
    t0 = time.perf_counter()
    args = {} if area is None else {"area": area}
    if n_passes == 1:
        img = scanner.scan(resolution=dpi, mode="color", **args)
    else:
        img = scanner.scan(resolution=dpi, mode="color", passes=n_passes, **args)
    dt = time.perf_counter() - t0
    ladder = (
        list(scanner.model.exposure_ladder(max(n_passes, 2))) if n_passes >= 2 else [int(scanner.model.exposure_short)]
    )
    label = "single (N=1)" if n_passes == 1 else f"N-pass (N={n_passes})"
    print(f"  [{label}] scan ok in {dt:.1f}s, shape={img.rgb.shape}", file=sys.stderr)
    return Panel(
        label=label,
        rgb=np.ascontiguousarray(img.rgb, dtype=np.uint16),
        dpi=img.dpi,
        n_passes=n_passes,
        exposure_ladder=ladder,
        scan_time_s=dt,
    )


def area_from_pct(pct: float) -> tuple[float, float, float, float] | None:
    """Symmetric central crop covering ``pct``% of the total scan area.

    100 → full (returns None, i.e. no crop). 20 → central crop spanning
    sqrt(0.20) of each side.
    """
    if pct >= 99.999:
        return None
    side = float(np.sqrt(max(0.02, min(1.0, pct / 100.0))))
    off = (1.0 - side) / 2.0
    return (round(off, 6), round(off, 6), round(1.0 - off, 6), round(1.0 - off, 6))


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real-hardware multi-pass SNR comparison on a loaded film.")
    parser.add_argument("--dpi", type=int, default=None, help="DPI (default: model maximum)")
    parser.add_argument("--n", type=int, default=6, help="Multi-pass count for the 'many passes' panel (default 6)")
    parser.add_argument(
        "--area-pct",
        type=float,
        default=100.0,
        help="Fraction of the total scan area to use (100 = full frame, 20 = 20% of total area, etc.)",
    )
    parser.add_argument("--outdir", type=Path, default=Path("/home/tobby/Pictures"))
    parser.add_argument(
        "--crop-fraction", type=float, default=0.50, help="Central-crop fraction of each side (default 50%%)"
    )
    parser.add_argument(
        "--max-preview-side", type=int, default=2048, help="Max side of the full-frame preview PNG (default 2048)"
    )
    args = parser.parse_args(argv)

    from pyopticfilm import Scanner

    outdir: Path = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    assert _has("PIL"), "PIL required to render the PNG previews (install via: uv run --with pillow)"
    assert _has("tifffile"), "tifffile required for the archival TIFF outputs"

    area = area_from_pct(args.area_pct)

    print(f"[{_now()}] scanning…", file=sys.stderr)
    with Scanner.open() as scanner:
        model = scanner.model
        dpi = args.dpi or int(model.resolutions_dpi[0])
        n = max(2, int(args.n))
        area_txt = "full" if area is None else f"central {args.area_pct:g}% area -> {area}"
        print(f"  model={model.model}  dpi={dpi}  area={area_txt}  N: 1 vs {n}", file=sys.stderr)
        scanner.warmup()
        p1 = _scan_once(scanner, dpi=dpi, n_passes=1, area=area)
        scanner.park()
        time.sleep(1.0)
        scanner.warmup()
        pn = _scan_once(scanner, dpi=dpi, n_passes=n, area=area)

    # --- compute per-panel metrics ---------------------------------------------
    lo, hi = percentile_window(pn.rgb)
    metrics = []
    for p in (p1, pn):
        crop = central_crop(p.rgb, frac=args.crop_fraction)
        metrics.append(
            {
                "panel": p.label,
                "n_passes": p.n_passes,
                "dpi": p.dpi,
                "scan_time_s": round(p.scan_time_s, 2),
                "exposure_ladder": "+".join(str(e) for e in p.exposure_ladder),
                "hf_rms_full_dn": round(high_freq_rms_dn(p.rgb), 2),
                "hf_rms_crop_dn": round(high_freq_rms_dn(crop), 2),
                "mean_dn": round(float(p.rgb.mean()), 1),
            }
        )
    print(f"[{_now()}] metrics done", file=sys.stderr)

    # --- render previews ---------------------------------------------------------
    # Full-frame preview, shared normalisation, downsampled to keep the PNG small.
    n1_full = downscale_uint8(to_u8(p1.rgb, lo, hi), args.max_preview_side)
    nn_full = downscale_uint8(to_u8(pn.rgb, lo, hi), args.max_preview_side)
    n1_full = with_caption(
        n1_full, f"  {p1.label}   ·   {p1.dpi} dpi   ·   full frame   ·   ladder {p1.exposure_ladder}"
    )
    nn_full = with_caption(
        nn_full, f"  {pn.label}   ·   {pn.dpi} dpi   ·   full frame   ·   ladder {pn.exposure_ladder}"
    )
    full_preview = hstack([n1_full, nn_full])

    # Central-crop panel at full pixel resolution.
    c1 = central_crop(p1.rgb, frac=args.crop_fraction)
    cn = central_crop(pn.rgb, frac=args.crop_fraction)
    # Same (lo, hi) display window as the full-frame preview so the two images
    # are directly comparable in both size and exposure.
    n1_crop = to_u8(c1, lo, hi)
    nn_crop = to_u8(cn, lo, hi)
    n1_crop = with_caption(n1_crop, f"  {p1.label}   ·   central {int(args.crop_fraction * 100)}% crop")
    nn_crop = with_caption(nn_crop, f"  {pn.label}   ·   central {int(args.crop_fraction * 100)}% crop")
    crop_preview = hstack([n1_crop, nn_crop])

    png_full = outdir / "multipass_real_full.png"
    png_crop = outdir / "multipass_real_crop.png"
    save_png(png_full, np.ascontiguousarray(full_preview, dtype=np.uint8))
    save_png(png_crop, np.ascontiguousarray(crop_preview, dtype=np.uint8))

    # --- archival TIFFs (16-bit, full frame) -------------------------------------
    tiff_n1 = outdir / f"multipass_real_single_{dpi}.tif"
    tiff_nn = outdir / f"multipass_real_n{pn.n_passes}_{dpi}.tif"
    save_tiff(tiff_n1, p1.rgb, dpi=dpi)
    save_tiff(tiff_nn, pn.rgb, dpi=dpi)

    # --- CSV --------------------------------------------------------------------
    csv_path = outdir / "multipass_real_metrics.csv"
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(metrics[0].keys()))
        writer.writeheader()
        for r in metrics:
            writer.writerow(r)

    # --- console summary ----------------------------------------------------------
    print(f"[{_now()}] done", file=sys.stderr)
    print(f"  PNG   (full)      : {png_full}")
    print(f"  PNG   (crop)      : {png_crop}")
    print(f"  TIFF  (N=1)       : {tiff_n1}")
    print(f"  TIFF  (N={pn.n_passes})       : {tiff_nn}")
    print(f"  CSV               : {csv_path}")
    print()
    print(
        f"  {'panel':<16} {'N':>2} {'DPI':>5}  {'time':>7}  {'hF-RMS full':>14}  {'hF-RMS crop':>14}  {'mean DN':>10}"
    )
    for r in metrics:
        print(
            f"  {r['panel']:<16} {r['n_passes']:>2} {r['dpi']:>5}  {r['scan_time_s']:>6.1f}s {r['hf_rms_full_dn']:>13.2f}  {r['hf_rms_crop_dn']:>13.2f}  {r['mean_dn']:>10.1f}"
        )
    print()
    print("  High-frequency RMS is a shot-noise proxy in DN (lower = less noise).")
    print(f"  The crop is the central {int(args.crop_fraction * 100)}% of each frame at full pixel resolution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
