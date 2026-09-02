# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen 8100 V2 ME configure / IR refusal — do not retarget to match new code."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pyopticfilm.asic.gl128 import Gl128
from pyopticfilm.device.model_8100_v2 import MODEL_8100_V2
from pyopticfilm.exceptions import ScanError
from pyopticfilm.scan.bringup import crop_scan_geometry
from pyopticfilm.scan.geometry import compute_geometry
from pyopticfilm.scan.session_gl128 import Gl128ScanSession, image_feed2_steps
from pyopticfilm.usb.fake import MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol


def test_image_feed2_uses_v2_full_frame_when_area_missing():
    geo, _ = crop_scan_geometry(MODEL_8100_V2, 1800, (0.1, 0.2, 0.9, 0.8))
    expected = MODEL_8100_V2.feed_to_scan_steps_for_area(geo.area)
    assert image_feed2_steps(MODEL_8100_V2, geo) == expected
    lost = replace(geo, area=None)
    from_y1 = MODEL_8100_V2.feed_to_scan_steps_for_area((0.0, lost.area_y1, 1.0, 1.0))
    assert image_feed2_steps(MODEL_8100_V2, lost) == from_y1
    assert MODEL_8100_V2.feed_to_scan_steps_for_area(None) == 13128


def test_gl128_configure_long_exposure_registers():
    usb = MockScannerTransport()
    proto = GenesysUsbProtocol(usb)
    asic = Gl128(proto, MODEL_8100_V2)
    asic._motor_moves_enabled = False
    asic.init()
    session = Gl128ScanSession(asic, MODEL_8100_V2)
    geo = compute_geometry(1200, model=MODEL_8100_V2)
    session._pass_exposure = MODEL_8100_V2.exposure_long
    session._pass_long_exposure = True
    session._configure(geo)

    exp = (
        (usb.registers.get(0x7D, 0) << 16)
        | (usb.registers.get(0x7E, 0) << 8)
        | usb.registers.get(0x7F, 0)
    )
    assert exp == 42000
    assert usb.registers.get(0xA5) == 0x01
    assert usb.registers.get(0xAB) == 0x01


def test_gl128_configure_7200_writes_v2_lperiod():
    usb = MockScannerTransport()
    proto = GenesysUsbProtocol(usb)
    asic = Gl128(proto, MODEL_8100_V2)
    asic._motor_moves_enabled = False
    asic.init()
    session = Gl128ScanSession(asic, MODEL_8100_V2)
    geo = compute_geometry(7200, model=MODEL_8100_V2, area=(0.0, 0.0, 1.0, 0.12))
    session._configure(geo)
    lperiod = (
        (usb.registers.get(0x28, 0) << 16)
        | (usb.registers.get(0x29, 0) << 8)
        | usb.registers.get(0x2A, 0)
    )
    assert lperiod == 16035


def test_infrared_combined_scan_is_refused():
    usb = MockScannerTransport()
    proto = GenesysUsbProtocol(usb)
    asic = Gl128(proto, MODEL_8100_V2)
    asic._motor_moves_enabled = True
    session = Gl128ScanSession(asic, MODEL_8100_V2)
    with pytest.raises(ScanError, match="no infrared"):
        session.run(resolution=150, area=(0.0, 0.0, 0.2, 0.1), infrared=True, apply_calib=False)
