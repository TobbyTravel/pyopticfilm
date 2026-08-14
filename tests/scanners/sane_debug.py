# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse SANE ``SANE_DEBUG_SANEI_USB`` / genesys debug logs into traces.

SANE genesys does not dump a canonical JSON USB trace. The stable hardwareless
oracle is the *register program* (``write_register`` / ``reg[addr] = value``
lines). USB-level ``sanei_usb_control_msg`` lines are parsed when present so a
future Linux SANE run can emit the same JSON as :mod:`scanners.fake_usb`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from scanners.fake_usb import UsbTransaction

_CONTROL_MSG = re.compile(
    r"sanei_usb_control_msg:\s*rtype\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*,\s*"
    r"req\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*,\s*"
    r"value\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*,\s*"
    r"index\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*,\s*"
    r"len\s*=\s*(\d+)",
    re.IGNORECASE,
)
_HEX_LINE = re.compile(
    r"(?:^|\s)([0-9a-fA-F]{4}):\s*((?:[0-9a-fA-F]{2}\s*)+)",
)
_WRITE_REGISTER = re.compile(
    r"write_register\s*\(\s*(0x[0-9a-fA-F]+|\d+)\s*,\s*(0x[0-9a-fA-F]+|\d+)\s*\)",
    re.IGNORECASE,
)
_REG_ASSIGN = re.compile(
    r"reg\[(0x[0-9a-fA-F]+)\]\s*=\s*(0x[0-9a-fA-F]+)",
    re.IGNORECASE,
)
_ADDRESS_VALUE = re.compile(
    r"address:\s*(0x[0-9a-fA-F]+)\s*,\s*value:\s*(0x[0-9a-fA-F]+)",
    re.IGNORECASE,
)
_BULK_WRITE = re.compile(
    r"sanei_usb_write_bulk:.*?len\s*=\s*(\d+)",
    re.IGNORECASE,
)
_BULK_READ = re.compile(
    r"sanei_usb_read_bulk:.*?len\s*=\s*(\d+)",
    re.IGNORECASE,
)


def _num(text: str) -> int:
    return int(text, 0)


def _hex_bytes(text: str) -> bytes:
    return bytes(int(part, 16) for part in text.split() if part)


@dataclass
class SaneDebugParse:
    transactions: list[UsbTransaction] = field(default_factory=list)
    registers: dict[int, int] = field(default_factory=dict)


def parse_sane_debug(text: str) -> SaneDebugParse:
    """Extract USB transactions and a register program from a SANE debug log."""
    result = SaneDebugParse()
    pending: UsbTransaction | None = None
    pending_hex = bytearray()

    def flush_pending() -> None:
        nonlocal pending, pending_hex
        if pending is None:
            return
        if pending_hex:
            pending.data = bytes(pending_hex)
            if pending.operation == "control_write":
                pending.length = None
        result.transactions.append(pending)
        pending = None
        pending_hex = bytearray()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        ctrl = _CONTROL_MSG.search(line)
        if ctrl:
            flush_pending()
            rtype = _num(ctrl.group(1))
            req = _num(ctrl.group(2))
            value = _num(ctrl.group(3))
            index = _num(ctrl.group(4))
            length = int(ctrl.group(5))
            if rtype & 0x80:
                pending = UsbTransaction(
                    operation="control_read",
                    request_type=rtype,
                    request=req,
                    value=value,
                    index=index,
                    length=length,
                )
            else:
                pending = UsbTransaction(
                    operation="control_write",
                    request_type=rtype,
                    request=req,
                    value=value,
                    index=index,
                    data=b"",
                )
            continue

        bulk_w = _BULK_WRITE.search(line)
        if bulk_w:
            flush_pending()
            pending = UsbTransaction(
                operation="bulk_write",
                endpoint=0x02,
                data=b"",
                length=int(bulk_w.group(1)),
            )
            continue

        bulk_r = _BULK_READ.search(line)
        if bulk_r:
            flush_pending()
            pending = UsbTransaction(
                operation="bulk_read",
                endpoint=0x81,
                length=int(bulk_r.group(1)),
            )
            continue

        hex_line = _HEX_LINE.search(line)
        if hex_line and pending is not None:
            pending_hex.extend(_hex_bytes(hex_line.group(2)))
            continue

        write = _WRITE_REGISTER.search(line)
        if write:
            result.registers[_num(write.group(1))] = _num(write.group(2)) & 0xFF
            continue
        assign = _REG_ASSIGN.search(line)
        if assign:
            result.registers[_num(assign.group(1))] = _num(assign.group(2)) & 0xFF
            continue
        addr_val = _ADDRESS_VALUE.search(line)
        if addr_val:
            result.registers[_num(addr_val.group(1))] = _num(addr_val.group(2)) & 0xFF
            continue

    flush_pending()
    return result
