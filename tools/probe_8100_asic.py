# SPDX-License-Identifier: GPL-3.0-or-later
"""Bypass the supported-PID gate and do a SAFE ASIC identity probe.

Only perform read-only control transfers: no lamp, no motor, no AFE
power, no writes. Prints candidate status registers so we can tell
which Genesys ASIC the 8100 (07b3:1824) carries and whether the
vendor-request link answers at all.

Usage: .venv/bin/python tools/probe_8100_asic.py
"""
from __future__ import annotations

import sys

from pyopticfilm.usb.device import UsbDeviceHandle, UsbDeviceInfo, list_devices
from pyopticfilm.usb.protocol import GenesysUsbProtocol


def find_our_device() -> UsbDeviceInfo | None:
    for info in list_devices():
        if info.vendor_id == 0x07B3 and info.product_id == 0x1824:
            return info
    return None


def main() -> int:
    info = find_our_device()
    if info is None:
        print("no 07b3:1824 on the bus — is the scanner plugged in?")
        return 2
    print(f"device: {info.device_id}  bcd=0x{info.bcd_device:04x}  product={info.product!r}")

    # Constructor guard: __init__ rejects unsupported PIDs; bypass it.
    handle = UsbDeviceHandle.__new__(UsbDeviceHandle)
    handle.info = info
    handle._dev = None
    handle._interface = 0
    handle._ep_in = None
    handle._ep_out = None
    handle._claimed = False
    handle._needs_usb_reset = False
    handle.timeout_ms = 3000
    handle._open()
    print("USB open + interface claim OK (no udev rule needed on this box)")

    p = GenesysUsbProtocol(handle)
    candidates = (
        ("GL128 status @0x101 (high-addr read)", 0x101),
        ("GL845 status @0x41", 0x41),
        ("ASIC ID guess @0x30", 0x30),
        ("ASIC ID guess @0x31", 0x31),
        ("ASIC ID guess @0x00", 0x00),
        ("ASIC ID guess @0x01", 0x01),
    )
    for label, reg in candidates:
        try:
            val = p.read_register(reg)
            print(f"  {label:42s} = 0x{val:02x}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {label:42s} !! {type(exc).__name__}: {exc}")

    handle.close()
    print("closed cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
