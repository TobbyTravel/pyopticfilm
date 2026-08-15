# SPDX-License-Identifier: GPL-3.0-or-later
"""Lab bring-up for OpticFilm 8200i (GL845) — temporary unlock, does not flip scan_ready.

Run only with the scanner on the bench and a hand near the power switch for
home/motor steps. Public ``scan_ready`` stays False until this sequence
completes with a real image + park (see docs/sane-opticfilm.md).

Usage (from repo root)::

    uv run python tools/bringup_gl845_8200i.py --dry-run
    uv run python tools/bringup_gl845_8200i.py --steps open,status,lamp
    uv run python tools/bringup_gl845_8200i.py --steps home,tiny,park
    uv run python tools/bringup_gl845_8200i.py --steps calib,ir

``--allow-unvalidated`` is required for motor/image steps (lab unlock only).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo tools/ is not a package; keep imports local to pyopticfilm.


STEPS = (
    "open",
    "status",
    "lamp",
    "home",
    "tiny",
    "park",
    "calib",
    "ir",
)


def _parse_steps(raw: str) -> list[str]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    unknown = [p for p in parts if p not in STEPS]
    if unknown:
        raise SystemExit(f"Unknown steps {unknown}; choose from {', '.join(STEPS)}")
    return parts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--steps",
        default="open,status,lamp",
        help=f"Comma list from: {', '.join(STEPS)}",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=900,
        help="Tiny-scan / calib DPI (default 900)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("bringup_8200i_out"),
        help="Directory for TIFF dumps",
    )
    parser.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Lab unlock for home/scan/park (required for motor steps)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned sequence without opening USB",
    )
    args = parser.parse_args(argv)
    steps = _parse_steps(args.steps)
    motorish = {"home", "tiny", "park", "calib", "ir"}
    if motorish.intersection(steps) and not args.allow_unvalidated and not args.dry_run:
        raise SystemExit(
            "Motor/image steps need --allow-unvalidated (lab unlock). "
            "scan_ready is not flipped by this tool."
        )

    print("Bring-up plan (GL845 OpticFilm 8200i):")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")
    print(
        "After a successful image + park on real hardware, flip only "
        "MODEL_8200I.scan_ready = True in device/model_8200i.py."
    )
    if args.dry_run:
        return 0

    from pyopticfilm.device.model_8200i import MODEL_8200I
    from pyopticfilm.scanner import Scanner

    if MODEL_8200I.scan_ready:
        print("Note: MODEL_8200I.scan_ready is already True.")

    scanner = Scanner.open()
    try:
        if scanner.model.asic != "GL845" or "8200i" not in scanner.model.model:
            raise SystemExit(
                f"Expected OpticFilm 8200i GL845, got {scanner.model.model} "
                f"({scanner.model.asic})"
            )
        if args.allow_unvalidated:
            scanner._allow_unvalidated_scan = True  # lab harness unlock
            scanner.arm_bringup_motor()

        args.out.mkdir(parents=True, exist_ok=True)

        if "open" in steps:
            print(f"open: {scanner.model.vendor} {scanner.model.model}")
        if "status" in steps:
            st = scanner.status()
            print(f"status: {st}")
        if "lamp" in steps:
            scanner.lamp_on()
            print("lamp: on")
            scanner.lamp_off()
            print("lamp: off")
        if "home" in steps:
            print("home: seeking (hand near power)…")
            scanner.home()
            print("home: ok")
        if "tiny" in steps:
            print(f"tiny: {args.dpi} dpi crop, apply_calib=False…")
            image = scanner.scan(
                resolution=args.dpi,
                mode="color",
                area=(0.0, 0.0, 0.15, 0.1),
                apply_calib=False,
            )
            path = args.out / f"tiny_{args.dpi}.tif"
            _save_rgb(image.rgb, path)
            print(f"tiny: wrote {path} shape={image.rgb.shape}")
        if "park" in steps:
            print("park:…")
            scanner.park()
            print("park: ok")
        if "calib" in steps:
            print(f"calib+scan: host calib at {args.dpi} dpi…")
            image = scanner.scan(
                resolution=args.dpi,
                mode="color",
                area=(0.0, 0.0, 0.3, 0.2),
                apply_calib=True,
            )
            path = args.out / f"calib_{args.dpi}.tif"
            _save_rgb(image.rgb, path)
            print(f"calib: wrote {path}")
            scanner.park()
        if "ir" in steps:
            if not scanner.model.supports_infrared:
                raise SystemExit("Model reports no IR")
            print("ir: pass…")
            image = scanner.scan(
                resolution=args.dpi,
                mode="infrared",
                area=(0.0, 0.0, 0.15, 0.1),
                apply_calib=True,
            )
            path = args.out / f"ir_{args.dpi}.tif"
            plane = image.ir if image.ir is not None else image.rgb[:, :, 1]
            _save_gray(plane, path)
            print(f"ir: wrote {path}")
            scanner.park()
    finally:
        scanner.close()

    print(
        "Done. If image + park succeeded, set MODEL_8200I.scan_ready = True "
        "and extend sibling smoke tests."
    )
    return 0


def _save_rgb(rgb, path: Path) -> None:
    import numpy as np

    arr = np.asarray(rgb)
    np.save(path.with_suffix(".npy"), arr)
    # Also write a crude PPM preview (8-bit) for quick eyeballing without Pillow.
    if arr.dtype == np.uint16:
        arr8 = (arr / 257).astype(np.uint8)
    else:
        arr8 = arr.astype(np.uint8)
    h, w, _ = arr8.shape
    ppm = path.with_suffix(".ppm")
    with ppm.open("wb") as fh:
        fh.write(f"P6\n{w} {h}\n255\n".encode("ascii"))
        fh.write(np.ascontiguousarray(arr8).tobytes())


def _save_gray(plane, path: Path) -> None:
    import numpy as np

    arr = np.asarray(plane)
    np.save(path.with_suffix(".npy"), arr)
    if arr.dtype == np.uint16:
        arr8 = (arr / 257).astype(np.uint8)
    else:
        arr8 = arr.astype(np.uint8)
    h, w = arr8.shape
    pgm = path.with_suffix(".pgm")
    with pgm.open("wb") as fh:
        fh.write(f"P5\n{w} {h}\n255\n".encode("ascii"))
        fh.write(np.ascontiguousarray(arr8).tobytes())



if __name__ == "__main__":
    sys.exit(main())
