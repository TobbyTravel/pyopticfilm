# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen 8200i SE USB quiet-drain pacing — do not retarget to match new code."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.scan.geometry import compute_geometry
from pyopticfilm.scan.session_gl128 import (
    _QUIET_DRAIN_LAG,
    IMAGE_USB_PACE_S,
    Gl128ScanSession,
)


def _geometry(*, dpi: int = 7200, lines: int = 4) -> object:
    return compute_geometry(dpi, model=MODEL_8200I_SE, area=(0.0, 0.0, 1.0, 0.05))


def test_chunk_bytes_prefers_line_alignment():
    geometry = _geometry()
    line = geometry.line_bytes
    assert Gl128ScanSession._chunk_bytes(geometry, line * 3) == line
    assert Gl128ScanSession._chunk_bytes(geometry, line + 10) == line


def test_acquire_no_throttle_when_host_keeps_pace():
    """Adaptive drain sleeps only when the host outruns the line period."""
    geometry = _geometry()
    total = geometry.total_bytes
    session = Gl128ScanSession(MagicMock(), MODEL_8200I_SE)
    session.asic = MagicMock()
    session.asic.image_usb_pace_s = IMAGE_USB_PACE_S
    session.asic.protocol = MagicMock()
    session.asic.read_status.return_value = MagicMock(is_buffer_empty=False)

    line = geometry.line_bytes
    chunks = [
        b"\x00" * line,
        b"\x00" * (total - line),
    ]
    session.asic.protocol.bulk_read_exact.side_effect = chunks

    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    # Each chunk: t0 then elapsed well past one line period -> no throttle.
    mono = iter([0.0, 10.0, 20.0, 30.0])

    with patch.object(Gl128ScanSession, "_wait_data"), patch(
        "pyopticfilm.scan.session_gl128.time.sleep", side_effect=_sleep
    ), patch("pyopticfilm.scan.session_gl128.time.monotonic", side_effect=lambda: next(mono)):
        out = session._acquire(geometry, progress=None, cancel=None)

    assert len(out) == total
    assert sleeps == []


def test_acquire_throttle_matches_lperiod_at_7200():
    """7200 LPERIOD is ~3.55 ms/line; do not cap sleep at the old 3 ms ceiling."""
    geometry = _geometry(dpi=7200)
    line = geometry.line_bytes
    chunk = Gl128ScanSession._chunk_bytes(geometry, geometry.total_bytes)
    geom = MagicMock()
    geom.total_bytes = chunk
    geom.line_bytes = line
    geom.disable_buffer_full_move = geometry.disable_buffer_full_move
    geom.resolution = 7200
    session = Gl128ScanSession(MagicMock(), MODEL_8200I_SE)
    session.asic = MagicMock()
    session.asic.image_usb_pace_s = IMAGE_USB_PACE_S
    session.asic.protocol = MagicMock()
    session.asic.read_status.return_value = MagicMock(is_buffer_empty=False)
    session.asic.protocol.bulk_read_exact.return_value = b"\x00" * chunk

    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    mono = iter([0.0, 0.0])

    with patch.object(Gl128ScanSession, "_wait_data"), patch(
        "pyopticfilm.scan.session_gl128.time.sleep", side_effect=_sleep
    ), patch("pyopticfilm.scan.session_gl128.time.monotonic", side_effect=lambda: next(mono)):
        session._acquire(geom, progress=None, cancel=None)

    chunk_lines = chunk / line
    expected = session._line_interval_s(geometry) * chunk_lines * _QUIET_DRAIN_LAG
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(expected)
    assert sleeps[0] > IMAGE_USB_PACE_S * chunk_lines


def test_gl128_default_adaptive_usb_drain():
    from unittest.mock import MagicMock

    from pyopticfilm.asic.gl128 import DEFAULT_IMAGE_USB_PACE_S, Gl128

    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    assert asic.image_usb_pace_s == DEFAULT_IMAGE_USB_PACE_S
    assert DEFAULT_IMAGE_USB_PACE_S > 0.0


def test_verify_geometry_usb_span_rejects_bad_line_bytes():
    geometry = _geometry(dpi=1800)
    session = Gl128ScanSession(MagicMock(), MODEL_8200I_SE)
    bad = replace(geometry, line_bytes=geometry.line_bytes + 1)
    with pytest.raises(Exception, match="line_bytes"):
        session._verify_geometry_usb_span(bad)
