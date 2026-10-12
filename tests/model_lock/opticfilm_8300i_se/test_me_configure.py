# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen 8300i SE ME configure / IR support — do not retarget to match new code."""

from __future__ import annotations

from dataclasses import replace

from pyopticfilm.asic.gl128 import Gl128
from pyopticfilm.device.model_8300i_se import MODEL_8300I_SE
from pyopticfilm.scan.bringup import crop_scan_geometry
from pyopticfilm.scan.geometry import compute_geometry
from pyopticfilm.scan.session_gl128 import Gl128ScanSession, image_feed2_steps
from pyopticfilm.usb.fake import MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol


def test_image_feed2_uses_ppi_map_when_area_missing():
    geo, _ = crop_scan_geometry(MODEL_8300I_SE, 1800, (0.1, 0.2, 0.9, 0.8))
    expected = MODEL_8300I_SE.feed_to_scan_steps_for_area(geo.area)
    assert image_feed2_steps(MODEL_8300I_SE, geo) == expected
    lost = replace(geo, area=None)
    from_y1 = MODEL_8300I_SE.feed_to_scan_steps_for_area((0.0, lost.area_y1, 1.0, 1.0))
    assert image_feed2_steps(MODEL_8300I_SE, lost) == from_y1
    full = replace(geo, area=None, area_y1=0.0)
    assert image_feed2_steps(MODEL_8300I_SE, full) == MODEL_8300I_SE.feed_to_scan_steps_for_dpi(
        1800
    )
    assert MODEL_8300I_SE.feed_to_scan_steps_for_area(None) == 13128


def test_gl128_configure_short_exposure_is_15000():
    usb = MockScannerTransport()
    proto = GenesysUsbProtocol(usb)
    asic = Gl128(proto, MODEL_8300I_SE)
    asic._motor_moves_enabled = False
    asic.init()
    session = Gl128ScanSession(asic, MODEL_8300I_SE)
    geo = compute_geometry(1200, model=MODEL_8300I_SE)
    session._pass_exposure = MODEL_8300I_SE.exposure_short
    session._pass_long_exposure = False
    session._configure(geo)

    exp = (
        (usb.registers.get(0x7D, 0) << 16)
        | (usb.registers.get(0x7E, 0) << 8)
        | usb.registers.get(0x7F, 0)
    )
    assert exp == 15000


def test_gl128_configure_long_exposure_registers():
    usb = MockScannerTransport()
    proto = GenesysUsbProtocol(usb)
    asic = Gl128(proto, MODEL_8300I_SE)
    asic._motor_moves_enabled = False
    asic.init()
    session = Gl128ScanSession(asic, MODEL_8300I_SE)
    geo = compute_geometry(1200, model=MODEL_8300I_SE)
    session._pass_exposure = MODEL_8300I_SE.exposure_long
    session._pass_long_exposure = True
    session._configure(geo)

    exp = (
        (usb.registers.get(0x7D, 0) << 16)
        | (usb.registers.get(0x7E, 0) << 8)
        | usb.registers.get(0x7F, 0)
    )
    assert exp == 42000
    assert usb.registers.get(0xA5) == 0x02
    assert usb.registers.get(0xAB) == 0x02


def test_gl128_configure_7200_writes_v2_matched_lperiod():
    usb = MockScannerTransport()
    proto = GenesysUsbProtocol(usb)
    asic = Gl128(proto, MODEL_8300I_SE)
    asic._motor_moves_enabled = False
    asic.init()
    session = Gl128ScanSession(asic, MODEL_8300I_SE)
    geo = compute_geometry(7200, model=MODEL_8300I_SE, area=(0.0, 0.0, 1.0, 0.12))
    session._configure(geo)
    lperiod = (
        (usb.registers.get(0x28, 0) << 16)
        | (usb.registers.get(0x29, 0) << 8)
        | usb.registers.get(0x2A, 0)
    )
    assert lperiod == 16035


def test_infrared_is_supported_on_8300i():
    assert MODEL_8300I_SE.supports_infrared is True
