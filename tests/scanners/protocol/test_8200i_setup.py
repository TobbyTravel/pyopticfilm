# SPDX-License-Identifier: GPL-3.0-or-later
"""GL845 OpticFilm 8200i setup traces over FakeUsbTransport (no hardware)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyopticfilm.device.model_8200i import MODEL_8200I
from pyopticfilm.device.sensor_lookup import maxwd_register_value
from scanners.fake_usb import FakeUsbTransport
from scanners.setup_8200i import run_8200i_setup
from scanners.trace_compare import (
    OPTICAL_REGISTER_KEYS,
    compare_registers,
    compare_transactions,
    extract_register_writes,
    load_trace,
)

TRACE_DIR = Path(__file__).resolve().parents[2] / "traces" / "python" / "8200i"
GOLDEN = TRACE_DIR / "1800_rgb16_setup.json"
SANE_REGISTERS = (
    Path(__file__).resolve().parents[2] / "traces" / "sane" / "8200i" / "1800_rgb16_setup.registers.json"
)


def _u16(regs: dict[int, int], addr: int) -> int:
    return ((regs[addr] & 0xFF) << 8) | (regs[addr + 1] & 0xFF)


def _u24(regs: dict[int, int], addr: int) -> int:
    return ((regs[addr] & 0xFF) << 16) | ((regs[addr + 1] & 0xFF) << 8) | (regs[addr + 2] & 0xFF)


def test_8200i_setup_optical_registers_match_geometry():
    """USB-decoded setup registers must match independently computed geometry."""
    usb, geo = run_8200i_setup()
    regs = extract_register_writes(usb.transactions)

    assert _u16(regs, 0x2C) == geo.register_dpiset
    assert _u16(regs, 0x30) == geo.pixel_startx
    assert _u16(regs, 0x32) == geo.pixel_endx
    assert _u16(regs, 0x38) == geo.exposure_lperiod
    assert regs[0x34] == geo.dummy_pixel
    assert _u24(regs, 0x35) == maxwd_register_value(
        MODEL_8200I, line_bytes=geo.line_bytes, channels=geo.channels
    )
    assert _u24(regs, 0x25) == geo.lincnt_register
    assert regs[0x01] & 0x02  # SHDAREA
    assert not (regs[0x01] & 0x20)  # DVDSET off
    assert not (regs[0x01] & 0x01)  # SCAN off until begin
    assert usb.transactions, "expected USB traffic"


def test_8200i_setup_matches_golden_usb_trace():
    if not GOLDEN.is_file():
        pytest.fail(f"missing golden USB trace {GOLDEN}")
    usb, _geo = run_8200i_setup()
    golden = load_trace(GOLDEN)
    diff = compare_transactions(golden.transactions, usb.transactions)
    assert diff is None, diff
    written = extract_register_writes(usb.transactions)
    reg_diff = compare_registers(golden.registers, written, keys=OPTICAL_REGISTER_KEYS)
    assert reg_diff is None, reg_diff


def test_sane_register_fixture_if_present():
    """Compare optical registers to an independently generated SANE dump.

    No SANE fixture is committed until a genesys debug run produces one.
    See docs/scanner-validation.md.
    """
    if not SANE_REGISTERS.is_file():
        pytest.skip("no SANE register fixture yet")
    usb, _geo = run_8200i_setup()
    python_regs = extract_register_writes(usb.transactions)
    sane = load_trace(SANE_REGISTERS)
    diff = compare_registers(sane.registers, python_regs, keys=OPTICAL_REGISTER_KEYS)
    assert diff is None, diff


def test_fake_transport_records_control_and_bulk():
    usb = FakeUsbTransport()
    usb, geo = run_8200i_setup(usb)
    assert any(t.operation == "control_write" for t in usb.transactions)
    assert any(t.operation == "bulk_write" for t in usb.transactions)
    assert geo.resolution == 1800
