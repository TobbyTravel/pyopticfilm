#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cancel mid-scan, close, reopen, second scan — log GL128 recover state.

Usage (scanner connected, WinUSB/libusb bound)::

    uv run python scripts/repro_cancel_recover.py
    uv run python scripts/repro_cancel_recover.py --dpi 1200 --cancel-at 0.05
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import traceback

from pyopticfilm import Scanner
from pyopticfilm.exceptions import PlustekError, ScanCancelled
from pyopticfilm.logging import enable_debug_logging, get_logger
from pyopticfilm.scan.bringup import bringup_scan_geometry, is_opticfilm_8200i_se

logger = get_logger("repro_cancel_recover")


def _scan_kwargs(scanner: Scanner, dpi: int) -> dict:
    """Use Lab preview_safe geometry on SE so LINCNT fits the feed2 window."""
    kwargs: dict = {"resolution": dpi, "mode": "color"}
    if is_opticfilm_8200i_se(scanner.model):
        geo, _meta = bringup_scan_geometry(scanner.model, dpi, profile="preview_safe")
        kwargs["geometry"] = geo
        print(f"  using preview_safe geometry lincnt={geo.lincnt_register} travel={geo.travel_mm:.1f}mm")
    return kwargs


def _snap(scanner: Scanner, label: str) -> None:
    asic = scanner.asic
    try:
        st = asic.read_status_reliable()
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] status read FAILED: {exc}")
        return
    try:
        r01 = asic.protocol.read_register(0x01)
        r02 = asic.protocol.read_register(0x02)
        r03 = asic.protocol.read_register(0x03)
    except Exception as exc:  # noqa: BLE001
        r01 = r02 = r03 = -1
        print(f"[{label}] reg read FAILED: {exc}")
    park = getattr(asic, "_park_ok", None)
    print(
        f"[{label}] status=0x{st.raw:02x} home={st.is_at_home} "
        f"motor={st.is_motor_enabled} buf_empty={st.is_buffer_empty} "
        f"feed_fsh={st.is_feeding_finished} scan_fsh={st.is_scanning_finished} "
        f"0x01=0x{r01:02x} 0x02=0x{r02:02x} 0x03=0x{r03:02x} "
        f"_park_ok={park}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpi", type=int, default=1200)
    parser.add_argument(
        "--cancel-at",
        type=float,
        default=0.05,
        help="Cancel when scan progress >= this fraction (0..1)",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--position-only",
        action="store_true",
        help="On second open, only run position_for_full_frame_scan (no full image)",
    )
    args = parser.parse_args()
    if args.debug:
        enable_debug_logging()

    cancel = threading.Event()

    def progress(p: float) -> None:
        print(f"  progress {p:.3f}")
        if p >= args.cancel_at and not cancel.is_set():
            cancel.set()
            print(f"*** cancel.set() at progress={p:.3f}")

    print("=== PASS 1: scan then cancel ===")
    t0 = time.monotonic()
    with Scanner.open() as scanner:
        print(f"opened {scanner.device_id} model={scanner.model.model}")
        if not scanner.asic._initialized:
            scanner.asic.init()
        _snap(scanner, "after_init")
        if not scanner.asic.is_at_home():
            print("not home — refusing (no reverse-home)")
            return 2
        try:
            scanner.scan(
                **_scan_kwargs(scanner, args.dpi),
                progress=progress,
                cancel=cancel,
            )
            print("UNEXPECTED: scan completed without cancel")
            _snap(scanner, "after_full_scan")
            return 3
        except ScanCancelled as exc:
            print(f"ScanCancelled: {exc} (+{time.monotonic() - t0:.1f}s)")
            _snap(scanner, "after_cancel_end_scan")
        except PlustekError as exc:
            print(f"PlustekError during pass1: {exc}")
            traceback.print_exc()
            _snap(scanner, "after_pass1_error")
            return 4

        stop_calls = {"n": 0}
        orig_stop = scanner.asic.stop_motor

        def counting_stop() -> None:
            stop_calls["n"] += 1
            print(f"*** stop_motor call #{stop_calls['n']} during close")
            _snap(scanner, f"before_stop_motor_{stop_calls['n']}")
            orig_stop()
            _snap(scanner, f"after_stop_motor_{stop_calls['n']}")

        scanner.asic.stop_motor = counting_stop  # type: ignore[method-assign]
        print("=== closing (NegPy-style: lamp_off + stop_motor) ===")

    print("close finished")
    # USB reset after aborted bulk can re-enumerate; wait briefly before reopen.
    time.sleep(2.0)
    print(f"=== PASS 2: reopen + {'position-only' if args.position_only else 'scan'} ===")
    t1 = time.monotonic()
    scanner2 = None
    last_err: Exception | None = None
    for attempt in range(1, 6):
        try:
            scanner2 = Scanner.open()
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"reopen attempt {attempt}/5 failed: {exc}")
            time.sleep(1.0)
    if scanner2 is None:
        print(f"PASS2 FAIL: could not reopen after cancel: {last_err}")
        return 9

    try:
        with scanner2 as scanner:
            print(f"reopened {scanner.device_id}")
            if not scanner.asic._initialized:
                scanner.asic.init()
            _snap(scanner, "pass2_after_init")
            if not scanner.asic.is_at_home():
                print("PASS2 FAIL: not at home after reopen — would raise on home()")
                try:
                    scanner.home()
                except PlustekError as exc:
                    print(f"home() raised: {exc}")
                return 5

            if args.position_only:
                print("position_for_full_frame_scan…")
                before_home = scanner.asic.is_at_home()
                # Match preview_safe feed2 (13128), not full-frame 13704.
                from pyopticfilm.scan.bringup import preview_safe_scan_area

                _area, meta = preview_safe_scan_area(scanner.model, args.dpi)
                feed2 = int(meta.get("feed2") or scanner.model.feed_to_scan_steps)
                scanner.asic.position_for_full_frame_scan(scan_steps=feed2)
                after_home = scanner.asic.is_at_home()
                _snap(scanner, "pass2_after_position")
                moved = before_home and not after_home
                print(
                    f"PASS2 position: left_home={moved} feed2={feed2} "
                    f"(+{time.monotonic() - t1:.1f}s)"
                )
                return 0 if moved else 6

            print(f"second scan at {args.dpi} dpi (no cancel)…")
            saw_progress = False

            def progress2(p: float) -> None:
                nonlocal saw_progress
                saw_progress = True
                if p < 0.02 or p > 0.98 or int(p * 20) != int((p - 0.001) * 20):
                    print(f"  pass2 progress {p:.3f}")

            image = scanner.scan(
                **_scan_kwargs(scanner, args.dpi),
                progress=progress2,
            )
            _snap(scanner, "pass2_after_scan")
            print(
                f"PASS2 OK: shape={image.rgb.shape} progress_seen={saw_progress} "
                f"(+{time.monotonic() - t1:.1f}s)"
            )
            return 0
    except PlustekError as exc:
        print(f"PASS2 FAIL: {exc} (+{time.monotonic() - t1:.1f}s)")
        traceback.print_exc()
        return 7
    except Exception as exc:
        print(f"PASS2 FAIL unexpected: {exc}")
        traceback.print_exc()
        return 8


if __name__ == "__main__":
    sys.exit(main())
