# SPDX-License-Identifier: GPL-3.0-or-later
"""Timestamp-preserving, memory-bounded USBPcap event ledger.

``tools/scanlab/capture_pcap.py`` reads a whole capture into memory (raw file
bytes plus a ``UsbPcapPacket`` — including its full data payload — for every
packet) and drops pcap timestamps entirely. That is fine for Scan Lab's
image-decode use case on modest captures, but unusable here: some 8100 V2
the reference driver captures are 400 MB-1.8 GB, and timing between home/feed/status-poll/
bulk is the signal Phase 2-4 need most.

This module streams pcapng blocks from disk (one packet's bytes materialize,
get classified, and are discarded — nothing is retained proportional to file
size except summary counters), keeps the pcapng timestamp for every packet,
and NEVER stores or decodes bulk-IN/OUT payload bytes — only their length,
endpoint, and timestamp. Control-transfer payloads (register writes/reads,
buffer preambles) are tiny (<=64 bytes) and are decoded in full since that is
exactly the interesting, budget-safe part per the mission brief.

Output is a chronological event ledger (JSON) plus a summary (JSON + printed
table capped at ~40 rows) per capture. Nothing here prints or dumps bulk
payload content.
"""

from __future__ import annotations

import json
import struct
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Force this repo's src/ first — the venv's editable install can resolve
# `pyopticfilm` to a different clone on this machine (verified this
# session, though usb/protocol.py happened to be identical there).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pyopticfilm.usb.protocol import (
    REQUEST_BUFFER,
    REQUEST_REGISTER,
    REQUEST_TYPE_IN,
    REQUEST_TYPE_OUT,
    VALUE_BUFFER,
    VALUE_GET_REGISTER,
    VALUE_SET_REGISTER,
)

# USBPcap transfer types (desowin.org/usbpcap/captureformat.html)
TRANSFER_ISOCHRONOUS = 0
TRANSFER_INTERRUPT = 1
TRANSFER_CONTROL = 2
TRANSFER_BULK = 3
TRANSFER_NAMES = {0: "isochronous", 1: "interrupt", 2: "control", 3: "bulk"}

INFO_PDO_TO_FDO = 0x01  # completion / data-from-device

CONTROL_STAGE_SETUP = 0
CONTROL_STAGE_DATA = 1
CONTROL_STAGE_STATUS = 2
CONTROL_STAGE_COMPLETE = 3

_USBPCAP_HEADER = struct.Struct("<HQiHBHHBBI")
assert _USBPCAP_HEADER.size == 27

_REG_STATUS = 0x101


# --- pcapng streaming -------------------------------------------------------


@dataclass
class _Interface:
    ts_resol_exponent: int = 6  # default: microseconds (10^-6), per pcapng spec
    ts_resol_pow2: bool = False


def _decode_tsresol(byte_val: int) -> tuple[int, bool]:
    if byte_val & 0x80:
        return byte_val & 0x7F, True
    return byte_val & 0x7F, False


def iter_pcapng_packets(path: Path):
    """Yield ``(timestamp_seconds_or_None, packet_bytes)`` streaming from disk.

    Only Enhanced Packet Blocks (type 6) carry a per-packet timestamp; Simple
    Packet / obsolete Packet Blocks yield ``None``. Interface Description
    Block ``if_tsresol`` options are honoured when present (falls back to the
    pcapng default of microsecond resolution).
    """
    interfaces: list[_Interface] = []
    with path.open("rb") as f:
        endian = "<"
        first = f.read(4)
        if len(first) < 4:
            return
        (block_type,) = struct.unpack_from("<I", first, 0)
        f.seek(0)
        if block_type != 0x0A0D0D0A:
            return  # not pcapng (classic pcap not handled here; all 11 files are pcapng)

        while True:
            head = f.read(8)
            if len(head) < 8:
                break
            block_type, block_len = struct.unpack_from(endian + "II", head, 0)
            if block_len < 12:
                break
            body_len = block_len - 12
            body = f.read(body_len)
            trailer = f.read(4)
            if len(body) < body_len or len(trailer) < 4:
                break

            if block_type == 0x0A0D0D0A:  # Section Header Block
                (magic,) = struct.unpack_from("<I", body, 0)
                endian = "<" if magic == 0x1A2B3C4D else ">"
                interfaces = []
            elif block_type == 1:  # Interface Description Block
                iface = _Interface()
                # options start at offset 8 (linktype u16, reserved u16, snaplen u32)
                off = 8
                while off + 4 <= len(body):
                    opt_code, opt_len = struct.unpack_from(endian + "HH", body, off)
                    off += 4
                    if opt_code == 0 and opt_len == 0:
                        break
                    val = body[off : off + opt_len]
                    padded = (opt_len + 3) & ~3
                    off += padded
                    if opt_code == 9 and val:  # if_tsresol
                        exp, pow2 = _decode_tsresol(val[0])
                        iface.ts_resol_exponent = exp
                        iface.ts_resol_pow2 = pow2
                interfaces.append(iface)
            elif block_type == 6 and len(body) >= 20:  # Enhanced Packet Block
                iface_id, ts_high, ts_low, caplen, _origlen = struct.unpack_from(
                    endian + "IIIII", body, 0
                )
                pkt = body[20 : 20 + caplen]
                ts_raw = (ts_high << 32) | ts_low
                ts_seconds = None
                if 0 <= iface_id < len(interfaces):
                    iface = interfaces[iface_id]
                    resol = (
                        2.0**-iface.ts_resol_exponent
                        if iface.ts_resol_pow2
                        else 10.0**-iface.ts_resol_exponent
                    )
                    ts_seconds = ts_raw * resol
                yield ts_seconds, pkt
            elif block_type == 3 and len(body) >= 4:  # Simple Packet Block
                (origlen,) = struct.unpack_from(endian + "I", body, 0)
                yield None, body[4 : 4 + origlen]
            # else: NRB, ISB, custom blocks — skip


@dataclass
class UsbEvent:
    """One classified event, timestamps preserved, bulk payload never stored."""

    index: int
    ts: float | None
    kind: str  # reg_write | reg_read | buffer_preamble | bulk_in | bulk_out | interrupt | control_other
    endpoint: int
    detail: dict = field(default_factory=dict)


def parse_usbpcap_header(payload: bytes):
    if len(payload) < _USBPCAP_HEADER.size:
        return None
    (
        header_len,
        irp,
        _status,
        _function,
        info,
        _bus,
        _device,
        endpoint,
        transfer,
        data_length,
    ) = _USBPCAP_HEADER.unpack_from(payload, 0)
    if header_len < _USBPCAP_HEADER.size or header_len > len(payload):
        return None
    data = payload[header_len : header_len + data_length]
    stage = None
    if transfer == TRANSFER_CONTROL and header_len >= _USBPCAP_HEADER.size + 1:
        stage = payload[_USBPCAP_HEADER.size]
    return transfer, endpoint & 0xFF, info & 0xFF, data, stage, irp


def build_ledger(path: Path, *, max_events: int | None = None, track_bulk_timing: bool = False):
    """Stream one capture, return ``(events, summary_dict, bulk_in_timeline)``.

    ``events`` holds only control-transfer detail plus size/timestamp for
    bulk/interrupt — never bulk payload bytes.

    ``bulk_in_timeline`` is ``[(ts, size), ...]`` for every bulk-IN
    completion, only populated when ``track_bulk_timing=True`` (off by
    default — cheap per capture, ~860KB of Python objects even on the
    largest file in this project's dataset, but skip it for callers that
    don't need cadence analysis). Never includes payload bytes, only size.
    """
    events: list[UsbEvent] = []
    bulk_in_sizes: Counter[int] = Counter()
    bulk_out_sizes: Counter[int] = Counter()
    bulk_in_timeline: list[tuple[float | None, int]] = []
    counts: Counter[str] = Counter()
    first_ts: float | None = None
    last_ts: float | None = None
    reg_write_count = 0
    reg_read_count = 0
    status_poll_count = 0
    preamble_count = 0
    bulk_in_bytes_total = 0
    bulk_out_bytes_total = 0

    # These captures collapse each control transfer to exactly two USBPcap
    # packets: stage=SETUP ("submit") carrying the 8-byte setup header plus,
    # for an OUT request, the full payload already concatenated; and
    # stage=COMPLETE carrying the IN response bytes (or nothing, for OUT).
    # Multiple control transfers interleave in the packet stream, so submit
    # and complete are paired by the USBPcap IRP pointer, not by adjacency.
    pending_reads: dict[int, tuple[int, float | None, int]] = {}  # irp -> (addr, ts_submit, idx_submit)
    # Separate from ASIC register reads: bRequest=REQUEST_REGISTER (0x0C),
    # a 1-byte "vendor probe" read at a raw wIndex — e.g. wIndex=0x21,
    # polled during fast feeds until it returns 0x04 (gl128.py
    # _FEED_PROBE_INDEX/_FEED_PROBE_DONE). Response is 1 byte, not the
    # (value, link_status) pair the ASIC register-read path returns.
    pending_probes: dict[int, tuple[int, float | None, int]] = {}  # irp -> (windex, ts_submit, idx_submit)
    probe_read_count = 0

    for idx, (ts, payload) in enumerate(iter_pcapng_packets(path)):
        parsed = parse_usbpcap_header(payload)
        if parsed is None:
            continue
        transfer, endpoint, info, data, stage, irp = parsed

        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts

        if transfer == TRANSFER_CONTROL:
            direction = "in" if (endpoint & 0x80) else "out"
            counts[f"control_{direction}"] += 1
            if stage == CONTROL_STAGE_SETUP and len(data) >= 8:
                bm, breq, wvalue, windex, wlen = struct.unpack_from("<BBHHH", data, 0)
                cmd = wvalue & 0xFF
                if bm == REQUEST_TYPE_IN and breq == REQUEST_BUFFER and cmd == VALUE_GET_REGISTER:
                    addr = (windex - 0x22) >> 8
                    if wvalue & 0x100:
                        addr |= 0x100
                    pending_reads[irp] = (addr, ts, idx)
                elif bm == REQUEST_TYPE_IN and breq == REQUEST_REGISTER and cmd == VALUE_GET_REGISTER:
                    pending_probes[irp] = (windex, ts, idx)
                elif bm == REQUEST_TYPE_OUT and breq == REQUEST_BUFFER and len(data) > 8:
                    payload_bytes = data[8 : 8 + wlen] if wlen and len(data) >= 8 + wlen else data[8:]
                    _classify_out(payload_bytes, wvalue, windex, idx, ts, events)
                    if cmd == VALUE_SET_REGISTER:
                        reg_write_count += len(payload_bytes) // 2
                    elif cmd == VALUE_BUFFER:
                        preamble_count += 1
            elif stage == CONTROL_STAGE_COMPLETE and irp in pending_reads and len(data) >= 2:
                addr, ts_submit, idx_submit = pending_reads.pop(irp)
                value, status = data[0], data[1]
                reg_read_count += 1
                if addr == _REG_STATUS:
                    status_poll_count += 1
                events.append(
                    UsbEvent(
                        idx_submit, ts_submit, "reg_read", endpoint,
                        {
                            "addr": hex(addr),
                            "value": value,
                            "link_status": status,
                            "ts_complete": ts,
                            "complete_index": idx,
                        },
                    )
                )
            elif stage == CONTROL_STAGE_COMPLETE and irp in pending_probes and len(data) >= 1:
                windex, ts_submit, idx_submit = pending_probes.pop(irp)
                probe_read_count += 1
                events.append(
                    UsbEvent(
                        idx_submit, ts_submit, "probe_read", endpoint,
                        {
                            "w_index": hex(windex),
                            "value": data[0],
                            "ts_complete": ts,
                            "complete_index": idx,
                        },
                    )
                )
        elif transfer == TRANSFER_BULK:
            direction = "in" if (endpoint & 0x80) else "out"
            is_complete = bool(info & INFO_PDO_TO_FDO)
            if direction == "in" and data and (is_complete or len(data) >= 64):
                n = len(data)
                bulk_in_sizes[n] += 1
                bulk_in_bytes_total += n
                counts["bulk_in"] += 1
                if track_bulk_timing:
                    bulk_in_timeline.append((ts, n))
            elif direction == "out" and data and is_complete:
                n = len(data)
                bulk_out_sizes[n] += 1
                bulk_out_bytes_total += n
                counts["bulk_out"] += 1
        elif transfer == TRANSFER_INTERRUPT:
            counts["interrupt"] += 1
        elif transfer == TRANSFER_ISOCHRONOUS:
            counts["isochronous"] += 1

        if max_events is not None and len(events) >= max_events:
            pass  # events list is control-only and small; no truncation needed in practice

    summary = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "duration_s": (last_ts - first_ts) if (first_ts is not None and last_ts is not None) else None,
        "transfer_counts": dict(counts),
        "register_write_events": reg_write_count,
        "register_read_events": reg_read_count,
        "status_poll_count": status_poll_count,
        "probe_read_events": probe_read_count,
        "buffer_preamble_count": preamble_count,
        "bulk_in_total_bytes": bulk_in_bytes_total,
        "bulk_out_total_bytes": bulk_out_bytes_total,
        "bulk_in_size_histogram_top10": bulk_in_sizes.most_common(10),
        "bulk_out_size_histogram_top10": bulk_out_sizes.most_common(10),
    }
    return events, summary, bulk_in_timeline


def _classify_out(payload_bytes: bytes, wvalue: int, windex: int, idx: int, ts, events: list[UsbEvent]) -> None:
    cmd = wvalue & 0xFF
    if cmd == VALUE_SET_REGISTER:
        high = bool(wvalue & 0x100)
        i = 0
        while i + 1 < len(payload_bytes):
            addr = payload_bytes[i]
            val = payload_bytes[i + 1]
            if high:
                addr |= 0x100
            events.append(UsbEvent(idx, ts, "reg_write", 0, {"addr": hex(addr), "value": val}))
            i += 2
    elif cmd == VALUE_BUFFER and len(payload_bytes) >= 8:
        bulk_addr = int.from_bytes(payload_bytes[0:4], "little")
        bulk_size = int.from_bytes(payload_bytes[4:8], "little")
        events.append(
            UsbEvent(
                idx, ts, "buffer_preamble", 0,
                {"w_index": hex(windex), "bulk_addr": hex(bulk_addr), "bulk_size": bulk_size},
            )
        )


def registers_at(events: list[UsbEvent], packet_index: int) -> dict[str, int]:
    """Register snapshot from the last write at or before ``packet_index``."""
    regs: dict[str, int] = {}
    for ev in events:
        if ev.index > packet_index:
            break
        if ev.kind == "reg_write":
            regs[ev.detail["addr"]] = ev.detail["value"]
    return regs


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: capture_ledger.py <capture.pcapng> [out_dir] [--bulk-timing]",
            file=sys.stderr,
        )
        return 2
    path = Path(argv[1])
    rest = argv[2:]
    track_timing = "--bulk-timing" in rest
    positional = [a for a in rest if a != "--bulk-timing"]
    out_dir = Path(positional[0]) if positional else Path("docs/hw-ref/8100v2/ledgers")
    out_dir.mkdir(parents=True, exist_ok=True)

    events, summary, bulk_in_timeline = build_ledger(path, track_bulk_timing=track_timing)

    stem = path.stem
    (out_dir / f"{stem}.summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / f"{stem}.events.json").write_text(
        json.dumps([asdict(e) for e in events], indent=2)
    )
    print(json.dumps(summary, indent=2))
    print(f"events: {len(events)} written to {out_dir / f'{stem}.events.json'}", file=sys.stderr)
    if track_timing:
        (out_dir / f"{stem}.bulk_in_timing.json").write_text(json.dumps(bulk_in_timeline))
        print(
            f"bulk-in timing: {len(bulk_in_timeline)} entries written to "
            f"{out_dir / f'{stem}.bulk_in_timing.json'}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
