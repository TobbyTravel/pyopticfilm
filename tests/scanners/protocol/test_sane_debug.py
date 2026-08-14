# SPDX-License-Identifier: GPL-3.0-or-later
"""SANE debug-log parser tests (canned log, no SANE install required)."""

from __future__ import annotations

from pathlib import Path

from scanners.sane_debug import parse_sane_debug

SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sane_debug_sample.log"


def test_parse_sane_debug_sample():
    parsed = parse_sane_debug(SAMPLE.read_text(encoding="utf-8"))
    writes = [t for t in parsed.transactions if t.operation == "control_write"]
    assert writes[0].request_type == 0x40
    assert writes[0].request == 4
    assert writes[0].value == 131  # VALUE_SET_REGISTER
    assert writes[0].data == b"\x2c\x01"
    assert writes[1].data == b"\x2d\x2c"

    bulk_w = next(t for t in parsed.transactions if t.operation == "bulk_write")
    assert bulk_w.data == b"\x00\x11\x22\x33"

    bulk_r = next(t for t in parsed.transactions if t.operation == "bulk_read")
    assert bulk_r.length == 8

    reads = [t for t in parsed.transactions if t.operation == "control_read"]
    assert reads[0].request_type == 0xC0
    assert reads[0].value == 142  # VALUE_GET_REGISTER
    assert reads[0].length == 2

    assert parsed.registers[0x2C] == 0x01
    assert parsed.registers[0x2D] == 0x2C
    assert parsed.registers[0x30] == 0x00
    assert parsed.registers[0x32] == 0x0A
