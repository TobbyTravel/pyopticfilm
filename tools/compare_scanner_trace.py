#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare two scanner traces (USB transactions and/or register programs).

Usage:
    python tools/compare_scanner_trace.py tests/traces/sane/8200i/1800_rgb16_setup.json \\
        tests/traces/python/8200i/1800_rgb16_setup.json

    python tools/compare_scanner_trace.py a.json b.json --registers-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from scanners.trace_compare import (
    OPTICAL_REGISTER_KEYS,
    compare_registers,
    compare_transactions,
    load_trace,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", type=Path, help="Reference trace (SANE or golden Python)")
    parser.add_argument("actual", type=Path, help="Trace to check")
    parser.add_argument(
        "--registers-only",
        action="store_true",
        help="Compare the optical register program only (recommended vs SANE)",
    )
    parser.add_argument(
        "--all-registers",
        action="store_true",
        help="Compare every register present in either file",
    )
    parser.add_argument(
        "--no-collapse-polls",
        action="store_true",
        help="Do not collapse consecutive status (0x41) reads",
    )
    args = parser.parse_args()

    expected = load_trace(args.expected)
    actual = load_trace(args.actual)

    keys = None if args.all_registers else OPTICAL_REGISTER_KEYS
    if expected.registers or actual.registers:
        reg_diff = compare_registers(expected.registers, actual.registers, keys=keys)
        if reg_diff:
            print(reg_diff)
            if args.registers_only:
                return 1
        elif args.registers_only:
            print("Register programs match")
            return 0

    if args.registers_only:
        print("No registers in one or both files")
        return 1

    txn_diff = compare_transactions(
        expected.transactions,
        actual.transactions,
        collapse_polls=not args.no_collapse_polls,
    )
    if txn_diff:
        print(txn_diff)
        return 1
    print("USB transactions match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
