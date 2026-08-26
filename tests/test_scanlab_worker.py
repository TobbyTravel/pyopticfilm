# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan Lab worker slot signatures (no display, no PyQt import)."""

from __future__ import annotations

import ast
from pathlib import Path

_WORKER = Path(__file__).resolve().parents[1] / "tools" / "scanlab" / "worker.py"


def _method_args(name: str) -> tuple[list[str], list[str]]:
    tree = ast.parse(_WORKER.read_text(encoding="utf-8"))
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == "ScanWorker"):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == name:
                positional = [a.arg for a in item.args.args if a.arg != "self"]
                keyword_only = [a.arg for a in item.args.kwonlyargs]
                return positional, keyword_only
    raise AssertionError(f"ScanWorker.{name} not found")


def test_run_scan_accepts_positional_signal_args():
    positional, keyword_only = _method_args("run_scan")
    assert positional == [
        "target",
        "dpi",
        "ir_pass",
        "me_pass",
        "crop_norm",
        "apply_calib",
        "me_exposure_mode",
    ]
    assert keyword_only == []


def test_run_prescan_accepts_positional_signal_args():
    # request_prescan = pyqtSignal(object, bool)
    positional, keyword_only = _method_args("run_prescan")
    assert positional == ["target", "apply_calib"]
    assert keyword_only == []
