# SPDX-License-Identifier: GPL-3.0-or-later
"""GL128 motor slope-table selection per feed (2026-08-30 real-hardware fault).

Two independent real captures (a second capture session's files, and the
original vendor capture ``04_color_7200.pcapng``) show every positioning
feed pair uploading ``SLOPE_TABLE_FAST`` for the first (reference) feed but
``SLOPE_TABLE_SLOW`` for the second (final positioning) feed — exact byte
match in both sources, every time. pyopticfilm previously always used the
fast ramp for both feeds. See docs/hw-ref/8100v2/PROGRESS.md.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from pyopticfilm.asic.gl128 import Gl128
from pyopticfilm.device.model_8100_v2 import MODEL_8100_V2
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.device.select import create_asic
from pyopticfilm.device.tables_8200i_se import SLOPE_TABLE_FAST, SLOPE_TABLE_SLOW
from pyopticfilm.usb.fake import MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol


def _track_ahb_writes(asic, protocol):
    ahb_writes: list[bytes] = []
    orig_write_ahb = protocol.write_ahb

    def _tracking_write_ahb(addr, data):
        r = asic.registers
        if addr in (r.AHB_SLOPE_SCAN, r.AHB_SLOPE_FAST):
            ahb_writes.append(bytes(data))
        return orig_write_ahb(addr, data)

    protocol.write_ahb = _tracking_write_ahb
    return ahb_writes


def _pack(words: tuple[int, ...]) -> bytes:
    out = bytearray(len(words) * 2)
    for i, word in enumerate(words):
        out[2 * i] = word & 0xFF
        out[2 * i + 1] = (word >> 8) & 0xFF
    return bytes(out)


def test_upload_fast_slopes_default_is_fast():
    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    asic._upload_fast_slopes()

    calls = asic.protocol.write_ahb.call_args_list
    assert len(calls) == 2
    for call in calls:
        assert call.args[1] == _pack(SLOPE_TABLE_FAST)


def test_upload_fast_slopes_use_slow_uploads_slow_table():
    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    asic._upload_fast_slopes(use_slow=True)

    calls = asic.protocol.write_ahb.call_args_list
    assert len(calls) == 2
    for call in calls:
        assert call.args[1] == _pack(SLOPE_TABLE_SLOW)


def test_feed_capture_threads_use_slow_slope_through(monkeypatch):
    import pyopticfilm.asic.gl128 as gl128_mod

    monkeypatch.setattr(gl128_mod.time, "sleep", lambda *_a, **_k: None)

    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    asic._upload_fast_slopes = MagicMock()
    asic._feed_done_indicated = MagicMock(return_value=False)
    asic.read_status_reliable = MagicMock(
        side_effect=lambda: type("S", (), {"is_at_home": True, "is_motor_enabled": False, "is_feeding_finished": True, "raw": 0})()
    )

    asic._feed_capture(100, timeout_s=1.0, require_motion=False, use_slow_slope=True)
    asic._upload_fast_slopes.assert_called_once_with(use_slow=True)

    asic._upload_fast_slopes.reset_mock()
    asic._feed_capture(100, timeout_s=1.0, require_motion=False, use_slow_slope=False)
    asic._upload_fast_slopes.assert_called_once_with(use_slow=False)


def test_position_for_full_frame_scan_uses_fast_then_slow_on_v2(monkeypatch):
    """End-to-end via mock hardware, 8100 V2: first feed FAST, second SLOW."""
    usb = MockScannerTransport()
    protocol = GenesysUsbProtocol(usb)
    asic = create_asic(protocol, MODEL_8100_V2)
    asic._motor_moves_enabled = True
    ahb_writes = _track_ahb_writes(asic, protocol)

    asic.position_for_full_frame_scan(scan_steps=13486)

    fast = _pack(SLOPE_TABLE_FAST)
    slow = _pack(SLOPE_TABLE_SLOW)
    assert len(ahb_writes) == 4  # 2 windows x 2 feeds
    assert ahb_writes[0] == fast
    assert ahb_writes[1] == fast
    assert ahb_writes[2] == slow
    assert ahb_writes[3] == slow


def test_position_for_full_frame_scan_stays_fast_on_se(monkeypatch):
    """Regression guard: SE is unaffected by the V2-only slope fix.

    This evidence is V2-only (see Model8200iSE.use_slow_final_positioning_
    feed's docstring) -- SE must keep using the fast ramp for both feeds,
    exactly as before this fix, until SE-specific evidence exists.
    """
    usb = MockScannerTransport()
    protocol = GenesysUsbProtocol(usb)
    asic = create_asic(protocol, MODEL_8200I_SE)
    asic._motor_moves_enabled = True
    ahb_writes = _track_ahb_writes(asic, protocol)

    asic.position_for_full_frame_scan(scan_steps=13704)

    fast = _pack(SLOPE_TABLE_FAST)
    assert len(ahb_writes) == 4
    assert all(w == fast for w in ahb_writes), "SE must stay fast-fast, not fast-slow"
