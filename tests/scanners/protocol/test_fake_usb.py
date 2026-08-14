# SPDX-License-Identifier: GPL-3.0-or-later
"""FakeUsbTransport recording and error-queue tests."""

from __future__ import annotations

import pytest

from pyopticfilm.exceptions import UsbError
from pyopticfilm.usb.protocol import (
    INDEX,
    REQUEST_BUFFER,
    REQUEST_TYPE_OUT,
    VALUE_SET_REGISTER,
    GenesysUsbProtocol,
)
from scanners.fake_usb import FakeUsbTransport, UsbTransaction
from scanners.trace_compare import collapse_status_polls, compare_transactions, extract_register_writes


def test_structured_transaction_json_roundtrip():
    txn = UsbTransaction(
        operation="control_write",
        request_type=0x40,
        request=0x04,
        value=0x83,
        index=0,
        data=b"\x6c\x4c",
    )
    restored = UsbTransaction.from_json(txn.to_json())
    assert restored == txn


def test_records_register_write_order():
    usb = FakeUsbTransport()
    proto = GenesysUsbProtocol(usb)
    proto.write_register(0x6C, 0x4C)
    proto.write_register(0x6D, 0x80)
    assert [t.operation for t in usb.transactions] == ["control_write", "control_write"]
    assert extract_register_writes(usb.transactions) == {0x6C: 0x4C, 0x6D: 0x80}


def test_batched_register_write():
    usb = FakeUsbTransport()
    usb.control_msg(
        REQUEST_TYPE_OUT,
        REQUEST_BUFFER,
        VALUE_SET_REGISTER,
        INDEX,
        bytes((0x01, 0xAA, 0x02, 0xBB)),
    )
    assert usb.registers[0x01] == 0xAA
    assert usb.registers[0x02] == 0xBB


def test_queued_usb_error():
    usb = FakeUsbTransport()
    usb.queue_error(UsbError("boom"))
    with pytest.raises(UsbError, match="boom"):
        GenesysUsbProtocol(usb).write_register(0x01, 0x00)


def test_collapse_status_polls():
    poll = UsbTransaction(
        operation="control_read",
        request_type=0xC0,
        request=0x04,
        value=0x8E,
        index=(0x22 + (0x41 << 8)) & 0xFFFF,
        length=2,
    )
    other = UsbTransaction(
        operation="control_write",
        request_type=0x40,
        request=0x04,
        value=0x83,
        index=0,
        data=b"\x01\x00",
    )
    collapsed = collapse_status_polls([poll, poll, poll, other, poll, poll])
    assert len(collapsed) == 3
    assert collapsed[0] is poll
    assert collapsed[1] is other
    assert collapsed[2] is poll


def test_compare_reports_first_byte_difference():
    left = [
        UsbTransaction(operation="bulk_write", endpoint=0x02, data=b"\x02\x10\x34\x0f"),
    ]
    right = [
        UsbTransaction(operation="bulk_write", endpoint=0x02, data=b"\x02\x10\x34\x0e"),
    ]
    diff = compare_transactions(left, right, collapse_polls=False)
    assert diff is not None
    assert "Transaction 0 differs" in diff
    assert "byte offset 3" in diff
