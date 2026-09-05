# SPDX-License-Identifier: GPL-3.0-or-later
"""Layer-2 decoder: raw UsbTransaction -> semantic event, confidence-tagged.

This is the SAME classification logic as ``tools/capture_ledger.py``
(hand-verified against real captures across Phase 1-4 of the 8100 V2
investigation), factored out so it can run over BOTH sources:

  * live traffic, via usb/trace.py's RecordingTransport (this module's
    primary target)
  * pcap-derived events, via capture_ledger.py (kept as its own tool for
    now - see decode_pcap_event() for the adapter, used only by tests that
    cross-check the two paths agree)

Never mutates or replaces the raw UsbTransaction. Every DecodedEvent keeps a
back-reference to it (``raw``) plus a `confidence` field - PROVEN/STRONG/
LIKELY/SPECULATIVE/UNKNOWN - so an uncertain interpretation is visible as
such rather than silently presented as fact. Register read/write and
feed-probe framing (bRequest/wValue meanings) are PROVEN: verified against
11 real USBPcap captures in docs/hw-ref/8100v2/. What a given address
*means* physically is a separate, per-address question this module does
NOT answer - that's the state-machine/register-dependency-graph layer on
top of this one, not yet built (see experimental/scanner_lab README gaps).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pyopticfilm.usb.protocol import (
    REQUEST_BUFFER,
    REQUEST_REGISTER,
    REQUEST_TYPE_IN,
    REQUEST_TYPE_OUT,
    VALUE_BUFFER,
    VALUE_GET_REGISTER,
    VALUE_SET_REGISTER,
)
from pyopticfilm.usb.trace import UsbTransaction

Confidence = Literal["PROVEN", "STRONG", "LIKELY", "SPECULATIVE", "UNKNOWN"]

DECODER_NAME = "gl128_control_decoder"
DECODER_VERSION = "1"


@dataclass
class DecodedEvent:
    kind: str  # reg_write | reg_read | probe_read | buffer_preamble | bulk_in | bulk_out | unclassified
    fields: dict[str, Any] = field(default_factory=dict)
    confidence: Confidence = "UNKNOWN"
    decoder_name: str = DECODER_NAME
    decoder_version: str = DECODER_VERSION
    raw: UsbTransaction | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "fields": self.fields,
            "confidence": self.confidence,
            "decoder_name": self.decoder_name,
            "decoder_version": self.decoder_version,
        }
        # Carry the raw transaction's timing/correlation IDs along so a
        # decoded-events-only consumer (milestone/phase timelines, run
        # diffing) doesn't need to re-join against usb_raw.jsonl by index.
        # This is metadata about the raw event, not a reinterpretation of
        # it - the raw file remains the source of truth for the bytes.
        if self.raw is not None:
            if self.raw.t0 is not None:
                out["raw_t0"] = self.raw.t0
            if self.raw.t1 is not None:
                out["raw_t1"] = self.raw.t1
            if self.raw.error is not None:
                out["error"] = self.raw.error
            for corr_field in (
                "investigation_id",
                "experiment_id",
                "run_id",
                "scan_id",
                "operation_id",
            ):
                val = getattr(self.raw, corr_field, None)
                if val is not None:
                    out[corr_field] = val
        return out


def decode_transaction(txn: UsbTransaction) -> DecodedEvent | None:
    """Decode one live UsbTransaction. Returns None for pure bulk data (no
    per-byte decoding is attempted there - see module docstring: bulk
    payload semantics are the image pipeline's job, not this decoder's)."""
    if txn.operation == "control_write":
        return _decode_control_write(txn)
    if txn.operation == "control_read":
        return _decode_control_read(txn)
    if txn.operation == "bulk_in" or txn.operation == "bulk_read":
        return DecodedEvent(
            kind="bulk_in",
            fields={"length": txn.length or (len(txn.data) if txn.data else None)},
            confidence="PROVEN",
            raw=txn,
        )
    if txn.operation == "bulk_write":
        return DecodedEvent(
            kind="bulk_out",
            fields={"length": len(txn.data) if txn.data else 0},
            confidence="PROVEN",
            raw=txn,
        )
    return None


def _decode_control_write(txn: UsbTransaction) -> DecodedEvent:
    req_type, req, value, data = txn.request_type, txn.request, txn.value, txn.data or b""
    cmd = (value or 0) & 0xFF
    high = bool((value or 0) & 0x100)

    if req_type == REQUEST_TYPE_OUT and req == REQUEST_BUFFER and cmd == VALUE_SET_REGISTER:
        pairs = []
        i = 0
        while i + 1 < len(data):
            addr = data[i] | (0x100 if high else 0)
            val = data[i + 1]
            pairs.append({"addr": hex(addr), "value": val})
            i += 2
        return DecodedEvent(
            kind="reg_write", fields={"pairs": pairs}, confidence="PROVEN", raw=txn
        )

    if req_type == REQUEST_TYPE_OUT and req == REQUEST_BUFFER and cmd == VALUE_BUFFER and len(data) >= 8:
        bulk_addr = int.from_bytes(data[0:4], "little")
        bulk_size = int.from_bytes(data[4:8], "little")
        return DecodedEvent(
            kind="buffer_preamble",
            fields={"w_index": hex(txn.index or 0), "bulk_addr": hex(bulk_addr), "bulk_size": bulk_size},
            confidence="PROVEN",
            raw=txn,
        )

    return DecodedEvent(
        kind="unclassified_control_write",
        fields={"request_type": req_type, "request": req, "value": value, "index": txn.index},
        confidence="UNKNOWN",
        raw=txn,
    )


def _decode_control_read(txn: UsbTransaction) -> DecodedEvent:
    req_type, req, value, index = txn.request_type, txn.request, txn.value, txn.index
    cmd = (value or 0) & 0xFF
    response = txn.response or b""

    if req_type == REQUEST_TYPE_IN and req == REQUEST_BUFFER and cmd == VALUE_GET_REGISTER:
        # SANE: address16 = 0x22 + (address << 8); invert to recover address.
        addr = ((index or 0) - 0x22) >> 8
        if (value or 0) & 0x100:
            addr |= 0x100
        fields: dict[str, Any] = {"addr": hex(addr)}
        if len(response) >= 2:
            fields["value"] = response[0]
            fields["link_status"] = response[1]
        return DecodedEvent(kind="reg_read", fields=fields, confidence="PROVEN", raw=txn)

    if req_type == REQUEST_TYPE_IN and req == REQUEST_REGISTER and cmd == VALUE_GET_REGISTER:
        fields = {"w_index": hex(index or 0)}
        if response:
            fields["value"] = response[0]
        return DecodedEvent(kind="probe_read", fields=fields, confidence="PROVEN", raw=txn)

    return DecodedEvent(
        kind="unclassified_control_read",
        fields={"request_type": req_type, "request": req, "value": value, "index": index},
        confidence="UNKNOWN",
        raw=txn,
    )


def format_decoded_line(ev: DecodedEvent) -> str:
    """One-line human-readable rendering, raw bytes NOT included (see format_usb_log_line for raw)."""
    if ev.kind == "reg_write":
        pairs = ", ".join(f"{p['addr']}={p['value']:#04x}" for p in ev.fields.get("pairs", []))
        return f"REG_WRITE  {pairs}"
    if ev.kind == "reg_read":
        addr = ev.fields.get("addr", "?")
        val = ev.fields.get("value")
        link = ev.fields.get("link_status")
        val_s = f"{val:#04x}" if isinstance(val, int) else "?"
        link_s = f" link={link:#04x}" if isinstance(link, int) else ""
        return f"REG_READ   {addr}={val_s}{link_s}"
    if ev.kind == "probe_read":
        val = ev.fields.get("value")
        val_s = f"{val:#04x}" if isinstance(val, int) else "?"
        return f"PROBE_READ {ev.fields.get('w_index', '?')}={val_s}"
    if ev.kind == "buffer_preamble":
        return f"BULK_PREAMBLE addr={ev.fields.get('bulk_addr')} size={ev.fields.get('bulk_size')}"
    if ev.kind == "bulk_in":
        return f"BULK_IN    {ev.fields.get('length')}b"
    if ev.kind == "bulk_out":
        return f"BULK_OUT   {ev.fields.get('length')}b"
    return f"UNCLASSIFIED {ev.kind} {ev.fields} [confidence={ev.confidence}]"
