# SPDX-License-Identifier: GPL-3.0-or-later
"""Re-export library fakes so existing protocol tests keep working."""

from __future__ import annotations

from pyopticfilm.usb.fake import (
    IDLE_STATUS_REG41,
    FakeUsbTransport,
    MockScannerTransport,
)
from pyopticfilm.usb.trace import UsbTransaction

__all__ = [
    "IDLE_STATUS_REG41",
    "FakeUsbTransport",
    "MockScannerTransport",
    "UsbTransaction",
]
