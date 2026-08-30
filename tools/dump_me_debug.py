# SPDX-License-Identifier: GPL-3.0-or-later
"""One-off diagnostic: run a real ME scan on the connected 8100 V2 and dump
the pre-merge bracket planes plus fusion stats, so a corrupted-looking pixel
in the merged deliverable can be traced back to what the raw brackets
actually saw at that location.

Usage (from repo root):
    uv run python -m tools.dump_me_debug [--dpi 7200] [--out DIR]
    uv run python -m tools.dump_me_debug --n-brackets 5 [--dpi 7200] [--out DIR]

With --n-brackets 2 (the default) this is the original 2-bracket (short +
adaptive long) ME scan, unchanged. With --n-brackets N > 2, this instead
captures N exposures geometrically spaced between the existing validated
floor (14000) and the DPI-appropriate ceiling (42000 at 7200dpi), fuses them
with an N-way generalization of exposure_merge.py's IVW formula, and dumps
every bracket plane alongside the fused result. This is a throwaway testing
harness (see plan at .claude/plans/splendid-hopping-garden.md) — the N-way
capture/merge logic here does NOT touch session_gl128.py or
exposure_merge.py; it reuses the existing, fully-tested multi_exposure=True
API in a loop plus exposure_merge.py's private confidence helper.

Not wired into Scan Lab's UI on purpose — this bypasses the GUI and talks to
Scanner directly so last_me_debug (bracket planes, fusion stats, align
shift) is trivially available in one place, no GUI-side plumbing needed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import tifffile

from pyopticfilm.image import ScanImage
from tools.scanlab.backend import (
    lab_scan_kwargs,
    list_lab_targets,
    open_lab_scanner,
    with_mock_mode,
)

_ME_SHORT_FLOOR = 14000
_ME_LONG_CEILING_AT_7200 = 42000


def _exposure_schedule(n: int, dpi: int) -> list[int]:
    """Geometric spacing between the existing validated floor and ceiling.

    N=2 reduces to today's fixed (14000, 42000) endpoints. Content-
    independent — does not reproduce production's adaptive long-exposure
    selection, deliberately, for a fusion-quality-only comparison.
    """
    ceiling = _ME_LONG_CEILING_AT_7200 if dpi == 7200 else 85000
    if n < 2:
        raise ValueError(f"n_brackets must be >= 2, got {n}")
    ratio = ceiling / _ME_SHORT_FLOOR
    return [round(_ME_SHORT_FLOOR * (ratio ** (i / (n - 1)))) for i in range(n)]


def _capture_n_brackets(
    scanner, kwargs: dict, exposures: list[int]
) -> tuple[list[np.ndarray], float]:
    """Capture N bracket planes by pairing consecutive exposures into
    multi_exposure=True calls, pulling raw linear planes from
    scanner.last_me_debug each time. Returns (frames, wall_clock_seconds).
    """
    n = len(exposures)
    frames: list[np.ndarray | None] = [None] * n
    t0 = time.monotonic()
    i = 0
    while i < n:
        if i + 1 < n:
            e_short, e_long = exposures[i], exposures[i + 1]
            print(f"  capturing brackets {i},{i+1}: exposures {e_short},{e_long}")
            scanner.scan(
                **kwargs,
                multi_exposure=True,
                align_passes=False,
                me_short_exposure=e_short,
                me_long_exposure=e_long,
            )
            debug = scanner.last_me_debug
            frames[i] = debug.rgb_short
            frames[i + 1] = debug.rgb_long
            i += 2
        else:
            # Odd leftover bracket: reuse the previous exposure as a forced
            # short partner, keep only the long plane (its short is a
            # redundant re-capture we already have from the prior pair).
            e_short, e_long = exposures[i - 1], exposures[i]
            print(f"  capturing bracket {i} (paired with e[{i-1}] as forced short): exposure {e_long}")
            scanner.scan(
                **kwargs,
                multi_exposure=True,
                align_passes=False,
                me_short_exposure=e_short,
                me_long_exposure=e_long,
            )
            debug = scanner.last_me_debug
            frames[i] = debug.rgb_long
            i += 1
    elapsed = time.monotonic() - t0
    assert all(f is not None for f in frames)
    return frames, elapsed  # type: ignore[return-value]


def _merge_n_way(
    frames: list[np.ndarray],
    exposures: list[int],
    *,
    alpha: float,
    beta: float,
    floor: float,
    clip_start: float,
    clip_end: float,
) -> np.ndarray:
    """N-way generalization of exposure_merge.py's pairwise IVW formula.

    Reduces exactly to the existing 2-way formula at N=2 (verified against
    exposure_merge.py's raw pre-gating IVW arithmetic — byte-identical).
    Reference scale is bracket 0 (shortest exposure). Deliberately omits the
    2-way merge's residual-disagreement gate and misalignment edge-fallback
    — plain N-way IVW average, see plan for why.

    Row-chunked (matches exposure_merge.py's _MERGE_CHUNK_ROWS pattern) —
    running this unchunked on a full 7200dpi frame OOM-kills the process.
    """
    from pyopticfilm.scan.exposure_merge import _smooth_confidence

    _CHUNK_ROWS = 128
    e0 = exposures[0]
    ref = np.asarray(frames[0])
    h, w = ref.shape[:2]
    out = np.empty((h, w, 3), dtype=np.uint16)

    for y0 in range(0, h, _CHUNK_ROWS):
        y1 = min(h, y0 + _CHUNK_ROWS)
        acc = None
        w_sum = None
        for raw, e in zip(frames, exposures, strict=True):
            raw_f = np.asarray(raw[y0:y1], dtype=np.float32)
            r = e / e0
            x = raw_f / r
            c = _smooth_confidence(raw_f, floor=floor, clip_start=clip_start, clip_end=clip_end)
            v = (alpha * np.maximum(raw_f, 0.0) + beta) / (r * r)
            weight = c / np.maximum(v, 1e-12)
            if acc is None:
                acc = weight * x
                w_sum = weight
            else:
                acc += weight * x
                w_sum += weight
        merged_chunk = acc / np.maximum(w_sum, 1e-12)
        out[y0:y1] = np.clip(merged_chunk, 0, 65535).astype(np.uint16)
    return out


def _align_n_way(frames: list[np.ndarray]) -> tuple[list[np.ndarray], list[tuple[float, float] | None]]:
    """Align every bracket >=1 to bracket 0, same function used for the
    2-bracket case today, just looped."""
    from pyopticfilm.pass_align import align_pass_to_reference

    ref = frames[0]
    aligned = [ref]
    shifts: list[tuple[float, float] | None] = [None]
    for frame in frames[1:]:
        warped, shift = align_pass_to_reference(ref, frame)
        aligned.append(warped)
        shifts.append(shift)
    return aligned, shifts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpi", type=int, default=7200, help="Scan resolution (default: 7200)")
    parser.add_argument(
        "--n-brackets",
        type=int,
        default=2,
        choices=range(2, 10),
        help="Number of exposure brackets, 2-9 (default: 2 = today's ME)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("me_debug_dump"),
        help="Output directory (default: ./me_debug_dump)",
    )
    args = parser.parse_args()

    targets = [
        t for t in list_lab_targets() if t.device_id is not None and "8100" in t.model.model
    ]
    if not targets:
        print("No connected 8100 V2 found. Plug it in and try again.", file=sys.stderr)
        return 1
    target = with_mock_mode(targets[0], False)
    print(f"Opening {target.label} ...")
    scanner, _rec = open_lab_scanner(target)

    # Force a fresh ASIC-shading measurement instead of reusing a cached
    # blob from an earlier run — only a fresh measurement populates
    # last_host_calib_dark/white on the ASIC object (the real per-column
    # strip data collected as a byproduct of ASIC shading, whether or not
    # ASIC shading itself validates). A cache hit re-uploads the old blob
    # via apply_colour_asic_shading() and skips that measurement entirely.
    scanner.calibrator.clear()

    kwargs = lab_scan_kwargs(target.model, dpi=args.dpi, kind="scan", crop_norm=None)

    out_dir = args.out if args.n_brackets == 2 else args.out / f"n{args.n_brackets}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.n_brackets == 2:
        # Unchanged original path — today's default ME, exactly.
        print(f"Scanning at {args.dpi}dpi, multi_exposure=True ...")
        t0 = time.monotonic()
        result: ScanImage = scanner.scan(**kwargs, multi_exposure=True, align_passes=True)
        elapsed = time.monotonic() - t0

        debug = scanner.last_me_debug
        if debug is None:
            print("scanner.last_me_debug is None — ME did not run?", file=sys.stderr)
            return 1

        tifffile.imwrite(out_dir / "merged_deliverable.tif", result.rgb)
        tifffile.imwrite(out_dir / "raw_short.tif", debug.rgb_short)
        tifffile.imwrite(out_dir / "raw_long.tif", debug.rgb_long)

        asic = scanner.asic
        host_dark = getattr(asic, "last_host_calib_dark", None)
        host_white = getattr(asic, "last_host_calib_white", None)
        calib_debug = {
            "asic_shading_ready": bool(getattr(asic, "asic_shading_ready", False)),
            "last_color_shading_host_ok": bool(getattr(asic, "last_color_shading_host_ok", False)),
            "last_color_shading_reject_reason": getattr(
                asic, "last_color_shading_reject_reason", None
            ),
            "host_calib_columns_captured": 0 if host_dark is None else len(host_dark),
        }
        if host_dark is not None and host_white is not None:
            np.savez(
                out_dir / "host_calib_columns.npz",
                dark=np.asarray(host_dark, dtype=np.uint16),
                white=np.asarray(host_white, dtype=np.uint16),
            )
            print(f"Captured {len(host_dark)} per-column dark/white strip entries.")

        stats = {
            "n_brackets": 2,
            "capture_wall_clock_s": elapsed,
            "calib_debug": calib_debug,
            "exposure_short": debug.exposure_short,
            "exposure_long": debug.exposure_long,
            "align_shift_long": debug.align_shift_long,
            "align_shift_ir": debug.align_shift_ir,
            "exposure_proposed": debug.exposure_proposed,
            "exposure_reason": debug.exposure_reason,
            "fusion_stats": (
                None
                if debug.fusion_stats is None
                else {
                    "mean_short_weight": debug.fusion_stats.mean_short_weight,
                    "mean_long_weight": debug.fusion_stats.mean_long_weight,
                    "zero_weight_pixels": debug.fusion_stats.zero_weight_pixels,
                    "total_pixels": debug.fusion_stats.total_pixels,
                    "zero_weight_fraction": debug.fusion_stats.zero_weight_fraction,
                    "mean_residual_confidence": debug.fusion_stats.mean_residual_confidence,
                    "exposure_ratio_used": debug.fusion_stats.exposure_ratio_used,
                }
            ),
        }
        (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
        print(f"Wrote merged_deliverable.tif, raw_short.tif, raw_long.tif, stats.json to {out_dir}/")
        print(json.dumps(stats, indent=2))
        return 0

    # N > 2: geometric exposure schedule, paired capture loop, offline N-way merge.
    from pyopticfilm.device.model_8100_v2 import MODEL_8100_V2
    from pyopticfilm.scan.exposure_merge import _SNR_ALPHA, _SNR_BETA, _SNR_CLIP_END, _SNR_CLIP_START, _SNR_FLOOR
    from pyopticfilm.scan.pipeline import ImagePipeline

    exposures = _exposure_schedule(args.n_brackets, args.dpi)
    print(f"Exposure schedule ({args.n_brackets} brackets): {exposures}")

    frames, capture_elapsed = _capture_n_brackets(scanner, kwargs, exposures)
    for i, frame in enumerate(frames):
        tifffile.imwrite(out_dir / f"raw_bracket_{i}_exp{exposures[i]}.tif", frame)

    aligned_frames, align_shifts = _align_n_way(frames)

    merged_linear = _merge_n_way(
        aligned_frames,
        exposures,
        alpha=_SNR_ALPHA,
        beta=_SNR_BETA,
        floor=_SNR_FLOOR,
        clip_start=_SNR_CLIP_START,
        clip_end=_SNR_CLIP_END,
    )

    pipeline = ImagePipeline(MODEL_8100_V2)
    merged = pipeline.expose_film_base(merged_linear, source="n-way ME test", preserve_headroom=True)
    merged = pipeline.clamp_border_highlights(merged)
    tifffile.imwrite(out_dir / "merged_deliverable.tif", merged)

    stats = {
        "n_brackets": args.n_brackets,
        "exposure_schedule": exposures,
        "capture_wall_clock_s": capture_elapsed,
        "align_shifts": align_shifts,
    }
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    print(f"Wrote {args.n_brackets} raw_bracket_*.tif, merged_deliverable.tif, stats.json to {out_dir}/")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
