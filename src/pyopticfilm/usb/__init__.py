# SPDX-License-Identifier: GPL-3.0-or-later
"""USB device discovery and I/O."""

from pyopticfilm.usb.device import UsbDeviceHandle, find_devices, list_devices
from pyopticfilm.usb.fake import FakeUsbTransport, MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol
from pyopticfilm.usb.trace import RecordingTransport, UsbTransaction

__all__ = [
    "FakeUsbTransport",
    "GenesysUsbProtocol",
    "MockScannerTransport",
    "RecordingTransport",
    "UsbDeviceHandle",
    "UsbTransaction",
    "find_devices",
    "list_devices",
]
