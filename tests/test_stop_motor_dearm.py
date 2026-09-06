# SPDX-License-Identifier: GPL-3.0-or-later
"""stop_motor() waits for MOTORENB to de-assert after AGOHOME park."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from pyopticfilm.asic.gl128 import Gl128
from pyopticfilm.asic.status import ScannerStatus
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE


def _status(*, home: bool, motor_enabled: bool) -> ScannerStatus:
    raw = 0
    if home:
        raw |= 0x08
    if motor_enabled:
        raw |= 0x01
    raw |= 0x40 | 0x10  # BUFEMPTY | LAMP
    return ScannerStatus.from_reg41(raw)


def _make_gl128() -> Gl128:
    proto = MagicMock()
    proto.read_register = MagicMock(return_value=0)
    gl = Gl128(proto, model=MODEL_8200I_SE)
    gl._initialized = True
    gl._park_ok = True
    return gl


def test_stop_motor_waits_for_motorenb_deassert():
    """After park, stop_motor spins until MOTORENB clears."""
    gl = _make_gl128()
    r = gl.registers
    gl._reg_cache[r.REG_0x02] = r.MTRPWR | r.AGOHOME

    # Skip the real wait_until_at_home — we only test the de-arm loop.
    gl.wait_until_at_home = MagicMock()

    # De-arm loop: 3 polls with MOTORENB set, then one clear.
    home_motor = _status(home=True, motor_enabled=True)
    home_idle = _status(home=True, motor_enabled=False)
    status_seq = [home_motor, home_motor, home_motor, home_idle]
    idx = 0

    def _read_status():
        nonlocal idx
        if idx < len(status_seq):
            st = status_seq[idx]
            idx += 1
            return st
        return home_idle

    gl.read_status = _read_status
    gl.read_status_reliable = _read_status

    with patch("pyopticfilm.asic.gl128.time") as mock_time:
        mock_time.monotonic = MagicMock(return_value=0.0)
        mock_time.sleep = MagicMock()
        gl.stop_motor()

    assert gl._park_ok is True
    assert idx == len(status_seq), "all de-arm statuses consumed"
    assert mock_time.sleep.call_count >= 3


def test_stop_motor_warns_if_motorenb_never_clears(caplog):
    """If MOTORENB stays stuck, stop_motor warns but does not raise."""
    gl = _make_gl128()
    r = gl.registers
    gl._reg_cache[r.REG_0x02] = r.MTRPWR | r.AGOHOME
    gl.wait_until_at_home = MagicMock()

    home_motor = _status(home=True, motor_enabled=True)
    gl.read_status_reliable = MagicMock(return_value=home_motor)

    with (
        patch("pyopticfilm.asic.gl128.time") as mock_time,
        caplog.at_level(logging.WARNING),
    ):
        # stop_motor computes: deadline = monotonic() + 6s
        # Then it checks monotonic() each loop; we advance past the deadline.
        mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0, 2.0, 3.0, 4.0, 7.0])
        mock_time.sleep = MagicMock()
        gl.stop_motor()

    assert gl._park_ok is True
    assert any("MOTORENB still set" in rec.message for rec in caplog.records)


def test_stop_motor_no_agohome_skips_dearm():
    """Without AGOHOME in reg 0x02, stop_motor just clears SCAN."""
    gl = _make_gl128()
    r = gl.registers
    gl._reg_cache[r.REG_0x02] = 0x00  # no AGOHOME

    gl.read_status = MagicMock()
    gl.stop_motor()

    # read_status should never be called (no park, no de-arm loop).
    gl.read_status.assert_not_called()
