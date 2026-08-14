#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert a SANE genesys / sanei_usb debug log into a scanner trace JSON.

Hardwareless SANE runs still need a responding USB device or a patched backend.
This tool only normalizes a log that SANE already produced:

    SANE_DEBUG_SANEI_USB=255 SANE_DEBUG_GENESYS=255 scanimage -d genesys:libusb:... \\
        --resolution 1800 --mode Color 2> sane.log

    python tools/sane_debug_to_trace.py sane.log \\
        --out tests/traces/sane/8200i/1800_rgb16_setup.json \\
        --model "OpticFilm 8200i" --dpi 1800 --revision <git-sha>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from scanners.sane_debug import parse_sane_debug
from scanners.trace_compare import dump_trace, extract_register_writes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="OpticFilm 8200i")
    parser.add_argument("--dpi", type=int, default=1800)
    parser.add_argument("--mode", default="rgb16")
    parser.add_argument("--revision", default="unknown")
    args = parser.parse_args()

    parsed = parse_sane_debug(args.log.read_text(encoding="utf-8", errors="replace"))
    registers = parsed.registers or extract_register_writes(parsed.transactions)
    dump_trace(
        args.out,
        transactions=parsed.transactions,
        registers=registers,
        meta={
            "source": "sane-backends",
            "revision": args.revision,
            "backend": "genesys",
            "model": args.model,
            "dpi": args.dpi,
            "mode": args.mode,
        },
    )
    print(
        f"wrote {args.out} ({len(parsed.transactions)} transactions, "
        f"{len(registers)} registers)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
