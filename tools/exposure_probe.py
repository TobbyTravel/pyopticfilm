# SPDX-License-Identifier: GPL-3.0-or-later
"""Exposure probe / sweep for GL128 OpticFilm scanners.

Scans the *currently loaded film* once per exposure step through the
**raw/calibrated linear pipeline** (no film-base makeup, no IVW merge), exactly
the per-pass path ``Gl128ScanSession._run_multi_pass`` uses, so each plane is a
clean sensor-physics measurement at a known ``REG_EXPOSURE`` value.

Purpose:
  * measure where the loaded film's response saturates (evidence-based
    ``max_exposure`` candidate for the ME long bin),
  * measure linearity of DN vs exposure (mid-bin values borrow the long
    pixel-clock bin; the sweep shows whether that is usable),
  * measure the film-base level (feeds a "recommend exposure per film" idea).

Usage::

    env -u PYTHONPATH uv run --all-groups \
        python -m tools.exposure_probe [--smoke] [--dpi 1200]

``--smoke`` runs only the 14k reference step (full-stack validation, ~1 pass).

Outputs to ``--outdir`` (default ``/home/tobby/exposure_probe``):
per-step 16-bit linear TIFFs, a log-normalized contact sheet PNG (labels in
stdout / the JSON summary), and ``exposure_probe_results.csv`` +
``exposure_probe_summary.json`` with per-step stats and the derived fit
metrics. Commit the CSV/JSON, not the big TIFFs/PNG.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_FULL_SCALE = 65535.0
#: Default sweep ladder: capture-validated bins plus headroom and a floor step.
DEFAULT_LADDER: tuple[int, ...] = (3500, 7000, 14000, 28000, 42000, 50000, 56000, 58000)
#: Exposure used as the film-base reference step for the density estimate.
BASELINE_EXPOSURE = 14000


@dataclass
class StepResult:
    exposure: int
    width: int
    height: int
    duration_s: float
    mean_dn: float
    mean_std_dn: float
    p05: float
    p50: float
    p90: float
    p99: float
    clip_92: tuple[float, float, float]
    clip_995: tuple[float, float, float]


def _stats(exposure: int, plane: np.ndarray, duration: float) -> StepResult:
    """Per-step DN statistics on a linear uint16 HxWx3 plane."""
    a = plane.astype(np.float64)
    lum = a.mean(axis=2)
    thr92 = 0.92 * _FULL_SCALE
    thr995 = 0.995 * _FULL_SCALE
    return StepResult(
        exposure=int(exposure),
        width=int(plane.shape[1]),
        height=int(plane.shape[0]),
        duration_s=duration,
        mean_dn=float(a.mean()),
        mean_std_dn=float(a.std()),
        p05=float(np.percentile(lum, 5)),
        p50=float(np.percentile(lum, 50)),
        p90=float(np.percentile(lum, 90)),
        p99=float(np.percentile(lum, 99)),
        clip_92=tuple(float(np.mean(a[:, :, c] >= thr92)) for c in range(3)),
        clip_995=tuple(float(np.mean(a[:, :, c] >= thr995)) for c in range(3)),
    )


def _linear_fit(results: list[StepResult]) -> dict:
    """Least-squares meanDN ~ slope*E + intercept on linear-region steps.

    Linear-region = steps whose p90 is below 85% of full scale (not clipping).
    ``estimated_fs_exposure`` is the exposure whose fitted mean DN reaches
    ~90% full scale — the evidence-based ceiling candidate.
    """
    lin = [r for r in results if r.p90 < 0.85 * _FULL_SCALE]
    xs = np.array([r.exposure for r in lin], dtype=np.float64)
    ys = np.array([r.mean_dn for r in lin], dtype=np.float64)
    if len(xs) < 2:
        return {"fitted_steps": len(xs), "note": "fewer than 2 linear steps"}
    slope, intercept = np.polyfit(xs, ys, 1)
    pred = slope * xs + intercept
    ss_res = float(np.sum((ys - pred) ** 2))
    ss_tot = float(np.sum((ys - ys.mean()) ** 2))
    r2 = 1.0 - ss_res / max(ss_tot, 1e-12)
    tgt = 0.90 * _FULL_SCALE
    fs_exp = float((tgt - intercept) / slope) if slope > 0 else float("inf")
    return {
        "fitted_steps": len(xs),
        "fitted_exposures": [int(r.exposure) for r in lin],
        "slope_dn_per_unit": float(slope),
        "intercept_dn": float(intercept),
        "r2": float(r2),
        "estimated_mean_fullscale_exposure": fs_exp,
    }


def _suggest(results: list[StepResult]) -> dict:
    """Film-density-aware exposure suggestion for a single-pass scan.

    Film base = darkest 5% luma at the baseline exposure (14k). In the linear
    regime DN scales with exposure, so the exposure that lands the film base
    ~25% down the 16-bit scale (a workable single-pass target for dense film;
    NegPy meters Dmin from the base level) is ``E_base * 0.25*FS / base_dn``.
    Clamped to the swept range as a sanity bound.
    """
    base = next((r for r in results if r.exposure == BASELINE_EXPOSURE), None)
    if base is None or base.p05 < 1.0:
        return {"note": "no usable baseline step (14k) in results"}
    e_min = min(r.exposure for r in results)
    e_max = max(r.exposure for r in results)
    target_dn = 0.25 * _FULL_SCALE
    suggested = BASELINE_EXPOSURE * target_dn / max(base.p05, 1.0)
    return {
        "film_base_dn_at_14k": float(base.p05),
        "target_dn": float(target_dn),
        "suggested_single_pass_exposure": float(suggested),
        "clamped_to_swept_range": bool(e_min <= suggested <= e_max),
        "interpretation": (
            "suggested exposure maximises single-pass SNR by placing the film "
            "base ~25% DN; valid only while DN vs exposure is linear "
            "(check r2 and the p90 clip gate in the fit block)"
        ),
    }


def _write_png_gray(path: str | Path, data: np.ndarray) -> None:
    """Tiny stdlib 8-bit grayscale PNG writer (PIL not in the core venv)."""
    import struct
    import zlib

    path = Path(path)
    h, w = data.shape
    raw = b"".join(b"\x00" + data[y].tobytes() for y in range(h))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)  # 8-bit grayscale
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        fh.write(chunk(b"IEND", b""))


def _resize_nn(im: np.ndarray, w: int, h: int) -> np.ndarray:
    """Nearest-neighbour resize of a 2-D uint8 array (np only)."""
    ys, xs = np.ogrid[:h, :w]
    ry = (ys * im.shape[0] // h).astype(np.intp)
    rx = (xs * im.shape[1] // w).astype(np.intp)
    return im[ry, rx]


def _sheet(results: list[StepResult], planes: dict[int, np.ndarray], out: Path) -> None:
    """Contact sheet: log-normalized 8-bit tiles, one per exposure, left→right.

    Per-tile labels are printed to stdout and recorded in the JSON summary so
    the sheet stays a dependency-free grayscale PNG.
    """
    h = 400
    pad = 16
    tiles = []
    for r in results:
        a = np.clip(np.asarray(planes[r.exposure], dtype=np.float64).mean(axis=2), 1.0, _FULL_SCALE)
        norm = 255.0 * (np.log1p(a) / np.log1p(_FULL_SCALE))
        im = norm.astype(np.uint8)
        scale = h / im.shape[0]
        tiles.append(_resize_nn(im, max(8, int(im.shape[1] * scale)), h))
    gaps = np.full((h, pad), 16, dtype=np.uint8)
    parts: list[np.ndarray] = []
    for t in tiles:
        parts.append(t)
        parts.append(gaps)
    _write_png_gray(out, np.concatenate(parts, axis=1))
    for r, t in zip(results, tiles):
        print(
            f"  sheet tile (w={t.shape[1]}): E={r.exposure} mean={r.mean_dn:.0f} "
            f"p50={r.p50:.0f} p90={r.p90:.0f} clip92_max={100 * max(r.clip_92):.2f}%"
        )


def _save_tiff(path: Path, plane: np.ndarray, dpi: int) -> None:
    import tifffile

    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, plane, photometric="rgb", resolution=(dpi, dpi))


def run(args: argparse.Namespace) -> int:
    from pyopticfilm import Scanner
    from pyopticfilm.scan.geometry import compute_geometry
    from pyopticfilm.scan.session import ScanSession, create_session

    ladder = (BASELINE_EXPOSURE,) if args.smoke else args.ladder
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    with Scanner.open(args.device) as scanner:
        model = scanner.model
        print(f"Open: {model.model} {model.__class__.__name__} pid=0x{model.usb_product_id:04x}")
        if not getattr(model, "scan_ready", False) and model.usb_product_id not in (0x1824, 0x1825):
            raise SystemExit("model not scan-ready; refusing to sweep")
        if not scanner.asic.is_at_home():
            scanner.home()

        # GL128: establish the repeatable AGOHOME park position before any
        # real pass (first-scan fix). Same mechanism as Scanner.scan's prime,
        # but with the probe crop instead of the full window: at 1800 dpi a
        # full-frame scan from feed2=13704 overruns the scan-window end and
        # the motor guard refuses it. The prime is discarded — any valid
        # geometry establishes the park position.
        prime = create_session(scanner.asic, model, scanner.calibrator)
        print("Priming pass (discarded, probe crop, 14k)…", flush=True)
        t = time.monotonic()
        prime.run(resolution=args.dpi, mode="color", area=args.area, apply_calib=True)
        print(f"  prime done in {time.monotonic() - t:.1f}s", flush=True)

        geometry = compute_geometry(args.dpi, model=model, area=args.area)
        x1, y1, x2, y2 = (args.area if args.area is not None else (0.0, 0.0, 1.0, 1.0))
        w_mm = (x2 - x1) * model.x_size_ta_mm
        h_mm = (y2 - y1) * model.y_size_ta_mm
        print(
            f"Probe geometry: {args.dpi} dpi area={tuple(args.area)} "
            f"~{w_mm:.2f}x{h_mm:.2f} mm over the TA window "
            f"(feed2={model.feed_to_scan_steps_for_area(args.area)} steps)",
            flush=True,
        )

        results: list[StepResult] = []
        planes: dict[int, np.ndarray] = {}
        for idx, exposure in enumerate(ladder):
            session: ScanSession = create_session(scanner.asic, model, scanner.calibrator)
            session._pass_exposure = int(exposure)
            print(f"[{idx + 1}/{len(ladder)}] scanning exposure={exposure} …", flush=True)
            t = time.monotonic()
            try:
                raw = session.acquire_raw(
                    geometry, method="transparency", lamp_on=True, start_motor=True
                )
            finally:
                session._pass_exposure = None
            dur = time.monotonic() - t

            # Same host-calib decision as the ME per-pass path.
            calib = scanner.calibrator.find_for_scan(method="transparency", geometry=geometry)
            use_host = (
                calib is not None
                and scanner.calibrator.should_apply_host_calib()
            )
            dark = calib.dark if use_host else None
            white = calib.white if use_host else None
            planar = bool(getattr(model, "usb_planar_rgb", False))
            plane = session.pipeline.assemble(
                raw,
                geometry,
                dark=dark,
                white=white,
                planar=planar,
                expose_base=False,  # keep the plane raw/linear — probe is the point
            )
            plane = np.asarray(plane, dtype=np.uint16)
            results.append(_stats(exposure, plane, dur))
            planes[int(exposure)] = plane
            r = results[-1]
            print(
                f"  {r.width}x{r.height} in {dur:.1f}s  mean={r.mean_dn:.0f} "
                f"p05={r.p05:.0f} p50={r.p50:.0f} p90={r.p90:.0f} p99={r.p99:.0f} "
                f"clip92_max={100 * max(r.clip_92):.2f}%"
            )
            _save_tiff(outdir / f"exposure_{exposure}.tif", plane, dpi=args.dpi)

    fit = _linear_fit(results)
    suggestion = _suggest(results)

    rows = [
        [
            s.exposure, s.width, s.height, s.duration_s, s.mean_dn, s.mean_std_dn,
            s.p05, s.p50, s.p90, s.p99,
            s.clip_92[0], s.clip_92[1], s.clip_92[2],
            s.clip_995[0], s.clip_995[1], s.clip_995[2],
        ]
        for s in results
    ]
    csv_path = outdir / "exposure_probe_results.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "exposure", "width", "height", "duration_s", "mean_dn", "mean_std_dn",
                "p05", "p50", "p90", "p99",
                "clip92_r", "clip92_g", "clip92_b",
                "clip995_r", "clip995_g", "clip995_b",
            ]
        )
        w.writerows(rows)

    summary = {
        "device": f"{model.vendor} {model.model}",
        "pid": f"0x{model.usb_product_id:04x}",
        "dpi": args.dpi,
        "area": tuple(args.area),
        "crop_mm": [round(w_mm, 2), round(h_mm, 2)],
        "baseline_exposure": BASELINE_EXPOSURE,
        "per_step": [
            {
                "exposure": s.exposure, "mean_dn": s.mean_dn, "p05": s.p05,
                "p50": s.p50, "p90": s.p90, "p99": s.p99,
                "clip92_max": max(s.clip_92), "duration_s": s.duration_s,
            }
            for s in results
        ],
        "linear_fit": fit,
        "suggestion": suggestion,
    }
    json_path = outdir / "exposure_probe_summary.json"
    with open(json_path, "w") as fh:
        json.dump(summary, fh, indent=2)

    _sheet(results, planes, outdir / "exposure_probe_sheet.png")

    print("\n=== Exposure probe summary ===")
    print(json.dumps(fit, indent=2))
    print(json.dumps(suggestion, indent=2))
    print(f"\nWrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {outdir / 'exposure_probe_sheet.png'}")
    print("Per-step TIFF: exposure_<E>.tif (linear, no film-base makeup)")
    return 0


def _ladder_arg(value: str) -> list[int]:
    try:
        return [int(v) for v in value.split(",") if v.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected comma-separated ints, got {value!r}")


def _floats_arg(value: str) -> list[float]:
    try:
        return [float(v) for v in value.split(",") if v.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected comma-separated floats, got {value!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default=None, help="USB device id (default: auto-open)")
    parser.add_argument(
        "--dpi", type=int, default=1800, help="probe resolution (default 1800)"
    )
    parser.add_argument(
        "--ladder", type=_ladder_arg,
        default=list(DEFAULT_LADDER),
        help=f"comma-separated exposures (default: {','.join(map(str, DEFAULT_LADDER))})",
    )
    parser.add_argument(
        "--area", type=_floats_arg, default=None,
        help="x1,y1,x2,y2 normalized 0..1, e.g. 0.25,0.35,0.75,0.85 (default: 0.08,0.35,0.72,0.85)",
    )
    parser.add_argument("--outdir", type=Path, default=Path("/home/tobby/exposure_probe"))
    parser.add_argument("--smoke", action="store_true", help="run only the 14k baseline step")
    args = parser.parse_args(argv)
    if args.area is not None:
        if len(args.area) != 4:
            raise SystemExit("--area takes exactly 4 values in [0,1]")
        if any(v < 0.0 or v > 1.0 for v in args.area):
            raise SystemExit("--area values must be normalized 0..1")
    else:
        args.area = (0.08, 0.35, 0.72, 0.85)  # ~64% width x 50% height, window-safe
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
