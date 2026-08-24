# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless mock-scanner scans (no PyQt, no hardware)."""

from __future__ import annotations

import threading

import pytest

from pyopticfilm.device.model_8200i import MODEL_8200I
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.device.select import create_asic, model_is_scan_ready
from pyopticfilm.exceptions import AsicError, ScanCancelled
from pyopticfilm.scan.session import create_session
from pyopticfilm.scanner import Scanner
from pyopticfilm.usb.fake import FakeDeviceHandle, MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol
from pyopticfilm.usb.trace import RecordingTransport, UsbTransaction, format_usb_log_line

_TINY = (0.0, 0.0, 0.08, 0.08)


def test_scan_ready_unchanged_by_open_fake():
    assert model_is_scan_ready(MODEL_8200I) is False
    scanner = Scanner.open_fake(MODEL_8200I)
    try:
        assert model_is_scan_ready(scanner.model) is False
        assert scanner._allow_unvalidated_scan is True
    finally:
        scanner.close()


def test_real_path_still_locks_non_se_without_open_fake():
    handle = FakeDeviceHandle.for_model(MODEL_8200I)
    proto = GenesysUsbProtocol(MockScannerTransport())
    scanner = Scanner(handle, proto, model=MODEL_8200I)
    try:
        with pytest.raises(AsicError, match="locked out"):
            scanner.scan(resolution=900, area=_TINY, apply_calib=False)
    finally:
        scanner.close()


def test_open_fake_8200i_scan_pattern_and_progress():
    seen: list[float] = []
    scanner = Scanner.open_fake(MODEL_8200I)
    try:
        image = scanner.scan(
            resolution=900,
            area=_TINY,
            apply_calib=False,
            progress=seen.append,
        )
    finally:
        scanner.close()
    assert image.rgb.ndim == 3 and image.rgb.shape[2] == 3
    assert image.rgb.max() > 0
    assert seen and seen[-1] == pytest.approx(1.0)


def test_open_fake_cancel():
    scanner = Scanner.open_fake(MODEL_8200I)
    cancel = threading.Event()
    cancel.set()
    try:
        with pytest.raises(ScanCancelled):
            scanner.scan(resolution=900, area=_TINY, apply_calib=False, cancel=cancel)
    finally:
        scanner.close()


def test_gl128_session_mock_scan():
    usb = MockScannerTransport()
    asic = create_asic(GenesysUsbProtocol(usb), MODEL_8200I_SE)
    asic._motor_moves_enabled = True
    image = create_session(asic, MODEL_8200I_SE).run(
        resolution=150,
        area=_TINY,
        apply_calib=False,
    )
    assert image.rgb.max() > 0
    assert any(t.operation == "bulk_read" for t in usb.transactions)


def test_gl128_scanner_primes_once_before_first_requested_scan(monkeypatch):
    """A discarded initial pass gives GL128 its proven AGOHOME park cycle."""
    import pyopticfilm.scan.session as session_module

    scanner = Scanner.open_fake(MODEL_8200I_SE)
    sentinel = object()
    runs: list[dict[str, object]] = []

    class FakeSession:
        last_me_debug = None

        def run(self, **kwargs):
            runs.append(kwargs)
            return sentinel

    monkeypatch.setattr(session_module, "create_session", lambda *args: FakeSession())
    try:
        assert scanner.scan(resolution=150, area=_TINY, apply_calib=False) is sentinel
        assert len(runs) == 2
        assert runs[0]["progress"] is None
        assert runs[0]["cancel"] is None
        assert runs[0]["multi_exposure"] is False
        assert runs[0]["infrared"] is False
        assert runs[1]["resolution"] == 150

        assert scanner.scan(resolution=150, area=_TINY, apply_calib=False) is sentinel
        assert len(runs) == 3
    finally:
        scanner.close()


def test_recording_transport_listener():
    lines: list[str] = []

    def on_txn(txn: UsbTransaction) -> None:
        lines.append(format_usb_log_line(txn))

    rec = RecordingTransport(MockScannerTransport(), listener=on_txn)
    proto = GenesysUsbProtocol(rec)
    proto.write_register(0x01, 0x22)
    assert rec.transactions
    assert lines and "control_write" in lines[0]
