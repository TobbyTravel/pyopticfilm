# SPDX-License-Identifier: GPL-3.0-or-later
"""Dump a pyopticfilm USB/register trace for OpticFilm 8200i 1800 dpi RGB16 setup.

Usage:
    python tools/dump_python_setup_trace.py
    python tools/dump_python_setup_trace.py --out tests/traces/python/8200i/1800_rgb16_setup.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from scanners.setup_8200i import run_8200i_setup
from scanners.trace_compare import dump_trace, extract_register_writes

DEFAULT_OUT = ROOT / "tests" / "traces" / "python" / "8200i" / "1800_rgb16_setup.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dpi", type=int, default=1800)
    args = parser.parse_args()

    usb, geo = run_8200i_setup(dpi=args.dpi)
    dump_trace(
        args.out,
        transactions=usb.transactions,
        registers=extract_register_writes(usb.transactions),
        meta={
            "source": "pyopticfilm",
            "backend": "pyopticfilm",
            "model": "OpticFilm 8200i",
            "asic": "GL845",
            "dpi": int(geo.resolution),
            "mode": "rgb16",
            "phase": "init+configure",
            "pixels": geo.pixels,
            "lines": geo.lines,
            "dpiset": geo.register_dpiset,
            "lincnt": geo.lincnt_register,
        },
    )
    print(f"wrote {args.out} ({len(usb.transactions)} transactions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
