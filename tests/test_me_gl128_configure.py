# SPDX-License-Identifier: GPL-3.0-or-later
"""GL128 multi-exposure configure tests."""

from __future__ import annotations

from pyopticfilm.asic.gl128 import Gl128
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.scan.geometry import compute_geometry
from pyopticfilm.scan.session_gl128 import Gl128ScanSession
from pyopticfilm.usb.fake import MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol


def test_gl128_configure_long_exposure_registers():
    usb = MockScannerTransport()
    proto = GenesysUsbProtocol(usb)
    asic = Gl128(proto, MODEL_8200I_SE)
    asic._motor_moves_enabled = False
    asic.init()
    session = Gl128ScanSession(asic, MODEL_8200I_SE)
    geo = compute_geometry(1200, model=MODEL_8200I_SE)
    session._pass_exposure = MODEL_8200I_SE.exposure_long
    session._configure(geo)

    exp = (
        (usb.registers.get(0x7D, 0) << 16)
        | (usb.registers.get(0x7E, 0) << 8)
        | usb.registers.get(0x7F, 0)
    )
    assert exp == 42000
    assert usb.registers.get(0xA5) == 0x01
    assert usb.registers.get(0xAB) == 0x01

    session._pass_exposure = MODEL_8200I_SE.exposure_short
    session._configure(geo)
    exp_short = (
        (usb.registers.get(0x7D, 0) << 16)
        | (usb.registers.get(0x7E, 0) << 8)
        | usb.registers.get(0x7F, 0)
    )
    assert exp_short == 14000
    assert usb.registers.get(0xA5) == 0x02
