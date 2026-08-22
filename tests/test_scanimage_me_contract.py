# SPDX-License-Identifier: GPL-3.0-or-later
"""ScanImage NegPy contract and Scanner.last_me_debug wiring."""

from __future__ import annotations

from dataclasses import fields
from unittest.mock import MagicMock, patch

import numpy as np

from pyopticfilm.image import ScanImage
from pyopticfilm.scan.me_debug import MeScanDebug
from pyopticfilm.scanner import Scanner


def test_scanimage_fields_are_negpy_facing_only():
    names = {f.name for f in fields(ScanImage)}
    assert names == {"rgb", "dpi", "device_model", "ir"}


def test_scanner_last_me_debug_copied_from_session():
    rgb = np.zeros((4, 4, 3), dtype=np.uint16)
    ir = np.zeros((4, 4), dtype=np.uint16)
    short = np.full((4, 4, 3), 1000, dtype=np.uint16)
    long = np.full((4, 4, 3), 3000, dtype=np.uint16)
    debug = MeScanDebug(
        rgb_short=short,
        rgb_long=long,
        exposure_short=14000,
        exposure_long=42000,
    )
    image = ScanImage(rgb=rgb, dpi=1800, ir=ir)

    handle = MagicMock()
    handle.info.product_id = 0x1825
    handle.info.bcd_device = 0
    scanner = Scanner(handle)
    scanner._asic._initialized = True
    session = MagicMock()
    session.run.return_value = image
    session.last_me_debug = debug

    with patch("pyopticfilm.scan.session.create_session", return_value=session):
        out = scanner.scan(resolution=1800, mode="color", multi_exposure=True)

    assert out is image
    assert not hasattr(out, "rgb_short")
    assert scanner.last_me_debug is debug
    assert scanner.last_me_debug.rgb_short is short
    assert scanner.last_me_debug.rgb_long is long


def test_scanner_last_me_debug_none_when_session_has_no_me():
    image = ScanImage(rgb=np.zeros((2, 2, 3), dtype=np.uint16), dpi=600)
    handle = MagicMock()
    handle.info.product_id = 0x1825
    handle.info.bcd_device = 0
    scanner = Scanner(handle)
    scanner._asic._initialized = True
    session = MagicMock()
    session.run.return_value = image
    session.last_me_debug = None

    with patch("pyopticfilm.scan.session.create_session", return_value=session):
        scanner.scan(resolution=600, mode="color", multi_exposure=False)

    assert scanner.last_me_debug is None
