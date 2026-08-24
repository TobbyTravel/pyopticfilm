# SPDX-License-Identifier: GPL-3.0-or-later
"""Render a human-readable comparison of the multi-pass ME merge.

Writes one PNG and one small CSV to ``--outdir`` (default ``/home/tobby/Pictures``).

The image shows, side by side and display-normalised at the same level:

* **single** — 1 pass, short exposure 14000
* **ME**     — 2 passes, 14000 + 42000 (the battle-tested ``merge_exposures``)
* **N4**     — 4 passes, 14000×2 + 42000×2 (new streaming IVW merger)
* **N6**     — 6 passes, 14000×3 + 42000×3 (new streaming IVW merger)

plus a crop of the dense-shadow region where the noise reduction is visible to
the eye. The scene and per-pass Poisson–Gaussian noise model mirror the device
(short 14000 / long 42000, the 8200i SE capture-validated bins). The read-noise
sigma is deliberately on the high side so the grain is clearly visible — that is
the point of this visual harness. The authoritative numeric tests live in
``tests/test_multi_pass.py``.

::

    env -u PYTHONPATH uv run --all-groups python -m tools.multipass_compatibility \
        [--outdir /home/tobby/Pictures] [--seed 20260824]
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

EXP_SHORT = 14000
EXP_LONG = 42000
ALPHA = 1.0
BETA = 4096.0
READ_NOISE_SIGMA = 1200.0  # heavy read+shot grain so humans can SEE the noise-reduction difference


# --- scene + pass simulation ----------------------------------------------------


def make_truth(h: int, w: int) -> np.ndarray:
    """A believable dense-film shadow scan — nearly flat, mild soft structure.

    Deliberately *low-contrast* so the noise (read + shot) IS the dominant visual
    texture — that is what a human can actually see and compare across variants.
    Think of the dense-shadow region of a 35 mm slide negative.
    """
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    # Base ~5500 DN (dense film negative, ~8% of full scale). Very mild soft structure
    # (15-30 % of the base) so the image isn't a totally blank rectangle but the
    # noise is still dominant over the signal.
    base = 5500.0
    # Gentle soft vignette/gradient for texture context (not for the eye to read).
    gx = 0.10 * ((xx - w / 2) / w) ** 2
    gy = 0.10 * ((yy - h / 2) / h) ** 2
    base = base * (1.0 + gx + gy)
    # A couple of very soft tonal patches (subtle, ~5-25% of base) to read as "film".
    cy0, cx0 = h * 0.35, w * 0.30
    r0 = min(h, w) * 0.4
    base = base + np.exp(-(((yy - cy0) ** 2 + (xx - cx0) ** 2) / (2 * r0**2)) * 2.5) * 900.0
    cy1, cx1 = h * 0.70, w * 0.70
    base = base + np.exp(-(((yy - cy1) ** 2 + (xx - cx1) ** 2) / (2 * r0**2)) * 3.0) * 600.0
    truth = np.repeat(np.clip(base, 0, 60000)[..., None], 3, axis=2)
    # Mild per-channel tint.
    truth[..., 0] *= 1.03
    truth[..., 2] *= 0.97
    return np.clip(truth, 0, 60000)


def draw_pass(exp: int, truth: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """One raw colour pass at USB exposure ``exp`` (read noise dominates the grain)."""
    rr = exp / EXP_SHORT
    sig = np.sqrt(ALPHA * (truth * rr) + BETA + (READ_NOISE_SIGMA * rr) ** 2)
    noisy = truth * rr + rng.normal(0.0, float(np.mean(sig)), truth.shape)
    return np.clip(noisy, 0, 65535).astype(np.uint16)


# --- metrics --------------------------------------------------------------------


def shadow_rmse(plane: np.ndarray, truth: np.ndarray, frac: float = 0.30) -> float:
    plane = plane.astype(np.float64)
    lum = plane.mean(axis=2)
    mask = lum <= np.percentile(lum, frac * 100)
    diff = plane[mask] - truth[mask]
    return float(np.sqrt(np.mean(diff**2)))


def shadow_mean(plane_truth: np.ndarray, frac: float = 0.30) -> float:
    lum = plane_truth.mean(axis=2)
    mask = lum <= np.percentile(lum, frac * 100)
    return float(plane_truth[mask].mean())


# --- render helpers -------------------------------------------------------------


def normalize_for_display(rgb: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Map ``rgb`` through a fixed DN-to-255 scale shared by all variants."""
    a = np.asarray(rgb, dtype=np.float64)
    return np.clip((a - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)


def common_display_range(variants: list[np.ndarray]) -> tuple[float, float]:
    """Pick a shared (lo, hi) DN window that all variants are mapped through, so
    the noise differences between variants are directly visible to the eye.

    Uses the across-variant p2 / p98 (plus margin) to avoid over-crushing the
    darkest or brightest pixels.
    """
    all_p2 = [float(np.percentile(v, 2)) for v in variants]
    all_p98 = [float(np.percentile(v, 98)) for v in variants]
    lo = max(0.0, min(all_p2) * 0.85)
    hi = max(max(all_p98) * 1.05, lo + 10.0)
    return lo, hi


def _has(module: str) -> bool:
    """True if an importable top-level module is available (no import side effects)."""
    import importlib.util

    return importlib.util.find_spec(module) is not None


def hstack_panels(images: list[np.ndarray]) -> np.ndarray:
    """Tallest-left-align hstack with dark 3-px separators."""
    gap = 3
    if not images:
        return np.zeros((1, 1, 3), dtype=np.uint8)
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


def vstack_panels(images: list[np.ndarray]) -> np.ndarray:
    """Top-left-align vstack with dark 3-px separators."""
    gap = 3
    if not images:
        return np.zeros((1, 1, 3), dtype=np.uint8)
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


def with_caption(img: np.ndarray, text: str) -> np.ndarray:
    """Prepend a 26 px dark caption bar; render text via PIL when available."""
    arr = np.asarray(img, dtype=np.uint8)
    bar_h = 26
    out = np.zeros((bar_h + arr.shape[0], arr.shape[1], 3), dtype=np.uint8)
    out[bar_h:] = arr
    if not text:
        return out
    try:
        from PIL import Image, ImageDraw

        pil = Image.fromarray(out).convert("RGB")
        ImageDraw.Draw(pil).text((6, 6), text, fill=(235, 235, 235))
        return np.asarray(pil, dtype=np.uint8)
    except Exception:  # noqa: BLE001 - bar without text is acceptable
        return out


def save_png(path: Path, rgb: np.ndarray) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(rgb, dtype=np.uint8)
    if _has("PIL"):
        from PIL import Image

        Image.fromarray(arr).save(path, format="PNG")
        return True
    if _has("tifffile"):
        import tifffile as tf

        alt = path.with_suffix(".tif")
        tf.imwrite(alt, arr, photometric="rgb")
        print(f"  note: PIL unavailable; wrote TIFF at {alt}", file=sys.stderr)
        return True
    print("  ERROR: neither PIL nor tifffile is importable — cannot embed image.", file=sys.stderr)
    return False


# --- variants -------------------------------------------------------------------


@dataclass
class Variant:
    label: str
    ladder: list[int]
    fused: np.ndarray


def build_variants(truth: np.ndarray, rng: np.random.Generator) -> list[Variant]:
    from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
    from pyopticfilm.scan.exposure_merge import reduce_passes

    def make(n_passes: int) -> tuple[list[int], np.ndarray]:
        ladder = [EXP_SHORT] if n_passes == 1 else list(MODEL_8200I_SE.exposure_ladder(n_passes))
        planes = [draw_pass(e, truth, rng) for e in ladder]
        return ladder, reduce_passes(planes, ladder).rgb

    specs = [
        ("single (N=1)", 1),
        ("ME 2-pass (N=2)", 2),
        ("N-pass (N=4)", 4),
        ("N-pass (N=6)", 6),
    ]
    out: list[Variant] = []
    for label, n in specs:
        ladder, fused = make(n)
        out.append(Variant(label=label, ladder=ladder, fused=fused))
    return out


# --- entry point -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render multi-pass ME comparison images.")
    parser.add_argument("--outdir", type=Path, default=Path("/home/tobby/Pictures"))
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=768)
    args = parser.parse_args(argv)

    truth = make_truth(args.height, args.width)
    rng = np.random.default_rng(args.seed)
    variants = build_variants(truth, rng)

    # --- numeric metrics -------------------------------------------------------
    rows = []
    for v in variants:
        rows.append(
            {
                "label": v.label,
                "ladder": "+".join(str(e) for e in v.ladder),
                "shadow_rmse_dn": round(shadow_rmse(v.fused, truth), 1),
                "shadow_mean_dn": round(shadow_mean(truth), 0),
                "overall_mean_dn": round(float(v.fused.mean()), 0),
            }
        )

    # Shared DN→255 window so cross-panel noise differences are visible to the eye.
    dlo, dhi = common_display_range([v.fused for v in variants])

    # --- full-frame panels -----------------------------------------------------
    panels = [with_caption(normalize_for_display(v.fused, dlo, dhi), v.label) for v in variants]
    top = hstack_panels(panels)

    # --- flat-shadow crop (centre of the dense negative, where grain dominates)
    ch = 160
    cw = 240
    y0 = int(0.28 * args.height)
    x0 = int(0.38 * args.width)
    crops = [
        with_caption(
            normalize_for_display(v.fused[y0 : y0 + ch, x0 : x0 + cw], dlo, dhi),
            v.label.split(" (")[0] + "  ·  shadow crop",
        )
        for v in variants
    ]
    crops_row = hstack_panels(crops)

    # --- header strip ----------------------------------------------------------
    header = np.zeros((46, top.shape[1], 3), dtype=np.uint8)
    if _has("PIL"):
        from PIL import Image, ImageDraw

        pil = Image.fromarray(header).convert("RGB")
        ImageDraw.Draw(pil).text(
            (8, 12),
            "pyopticfilm multi-pass ME  ·  same scene, same seed  ·  "
            f"single = 14000 · ME = 14000+42000 · N = replicate bracket (σ={READ_NOISE_SIGMA:.0f} DN)",
            fill=(245, 245, 245),
        )
        header = np.asarray(pil, dtype=np.uint8)

    composite = vstack_panels([header, top, crops_row])
    png_path = args.outdir / "multipass_compatibility.png"
    wrote = save_png(png_path, composite)
    if not wrote:
        return 1

    # --- CSV -------------------------------------------------------------------
    csv_path = args.outdir / "multipass_metrics.csv"
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    # --- console summary -------------------------------------------------------
    print(
        f"scene        : {args.width}x{args.height}  seed={args.seed}  "
        f"short={EXP_SHORT} long={EXP_LONG}  read-noise σ={READ_NOISE_SIGMA:.0f} DN"
    )
    print(f"truth mean   : {float(truth.mean()):.0f} DN   (each panel display-normalised via p99.9 stretch)")
    print(f"{'variant':<20} {'ladder':<28} shadow_RMSE(DN)  shadow_mean(DN)")
    for r in rows:
        print(f"{r['label']:<20} {r['ladder']:<28} {r['shadow_rmse_dn']:>8.1f}  {r['shadow_mean_dn']:>12.0f}")
    print()
    print(f"PNG    -> {png_path}")
    print(f"CSV    -> {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
