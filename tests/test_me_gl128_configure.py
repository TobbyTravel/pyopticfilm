# SPDX-License-Identifier: GPL-3.0-or-later
"""GL128 multi-exposure configure tests."""

from __future__ import annotations

from pyopticfilm.asic.gl128 import Gl128
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.scan.geometry import compute_geometry
from pyopticfilm.scan.session_gl128 import Gl128ScanSession, clamp_me_long_for_dpi
from pyopticfilm.usb.fake import MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol


def test_clamp_me_long_for_dpi_bounds():
    assert clamp_me_long_for_dpi(7200, 85000) == 42000
    assert clamp_me_long_for_dpi(7200, 42000) == 42000
    assert clamp_me_long_for_dpi(7200, 14000) == 14000
    assert clamp_me_long_for_dpi(7200, 10000) == 14000
    assert clamp_me_long_for_dpi(3600, 85000) == 85000
    assert clamp_me_long_for_dpi(3600, 90000) == 85000
    assert clamp_me_long_for_dpi(1800, 10000) == 14000
    assert clamp_me_long_for_dpi(1200, 42000) == 42000


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


def test_gl128_acquire_reannounces_usb_sized_blocks():
    """Chunked VALUE_BUFFER — one announce per line or USB block."""
    from unittest.mock import MagicMock

    from pyopticfilm.asic.registers import Gl128Registers
    from pyopticfilm.usb.device import BULK_MAX_SIZE

    total = BULK_MAX_SIZE * 2 + 100
    geometry = MagicMock()
    geometry.total_bytes = total
    geometry.disable_buffer_full_move = False
    geometry.line_bytes = BULK_MAX_SIZE

    proto = MagicMock()
    begins: list[int] = []

    def begin(size, *, index=0, addr=0x10000000):
        begins.append(int(size))

    remaining = {"n": total}

    def exact(size):
        n = min(int(size), remaining["n"])
        remaining["n"] -= n
        return b"\x00" * n

    proto.bulk_read_begin.side_effect = begin
    proto.bulk_read_exact.side_effect = exact

    asic = MagicMock()
    asic.protocol = proto
    asic.read_status.return_value = MagicMock(is_buffer_empty=False)
    asic.image_usb_pace_s = 0.0

    session = Gl128ScanSession(asic, MODEL_8200I_SE)
    session.se_regs = Gl128Registers()
    raw = session._acquire(geometry, progress=None, cancel=None)
    assert len(raw) == total
    assert begins == [BULK_MAX_SIZE, BULK_MAX_SIZE, 100]
    assert proto.bulk_read_exact.call_count == 3


def test_gl128_acquire_paces_between_chunks(monkeypatch):
    from unittest.mock import MagicMock, patch

    from pyopticfilm.asic.registers import Gl128Registers
    from pyopticfilm.usb.device import BULK_MAX_SIZE

    sleeps: list[float] = []
    monkeypatch.setattr(
        "pyopticfilm.scan.session_gl128.time.sleep",
        lambda s: sleeps.append(float(s)),
    )

    total = BULK_MAX_SIZE * 2
    geometry = MagicMock()
    geometry.total_bytes = total
    geometry.disable_buffer_full_move = False
    geometry.line_bytes = BULK_MAX_SIZE
    geometry.resolution = 7200

    proto = MagicMock()
    remaining = {"n": total}

    def exact(size):
        n = min(int(size), remaining["n"])
        remaining["n"] -= n
        return b"\x00" * n

    proto.bulk_read_exact.side_effect = exact

    asic = MagicMock()
    asic.protocol = proto
    asic.read_status.return_value = MagicMock(is_buffer_empty=False)
    asic.image_usb_pace_s = 0.003

    session = Gl128ScanSession(asic, MODEL_8200I_SE)
    session.se_regs = Gl128Registers()
    mono = iter([0.0, 5.0, 5.0, 10.0])
    with patch.object(Gl128ScanSession, "_wait_data"), patch(
        "pyopticfilm.scan.session_gl128.time.monotonic", side_effect=lambda: next(mono)
    ):
        session._acquire(geometry, progress=None, cancel=None)
    assert sleeps == []