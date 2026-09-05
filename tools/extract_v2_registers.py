# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 1: extract a raw GL128 register-event ledger from a USBPcap capture.

This is a *ground-truth* extraction tool for the 8100 V2 recapture
reconciliation (see ``docs/hw-ref/8100v2/2026-09-recapture/
claims-reconciliation.md``). It decodes ONLY:

  - USBPcap/pcapng packet framing (the ``USBPcap*`` header format, and the
    pcapng Enhanced/Simple Packet Block container around it).
  - The Genesys GL128 vendor-request protocol's register write
    (``VALUE_SET_REGISTER``) and register read (``VALUE_GET_REGISTER``)
    control transfers: address + value decode straight off
    ``bRequest``/``wValue``/``wIndex``/payload bytes.
  - Bulk transfer *metadata* (direction, size, timestamp) — never payload
    bytes, and no image reconstruction of any kind.

It deliberately does NOT use and does NOT reimplement any of
``tools/scanlab/capture_pcap.py``'s image-carving/heuristic code —
``carve_image_bulk``, ``coalesce_image_bulk``, ``_best_gl128_width``,
``optical_snapshot``'s width/lincnt-driven bulk sizing, etc. Those functions
encode assumptions about width/bytes-per-pixel/LINCNT convention that are
*exactly* what this reconciliation is trying to independently verify — using
them here would make the ledger circular evidence.

Where this script's logic comes from
-------------------------------------
- pcapng container streaming (Section Header / Interface Description /
  Enhanced Packet / Simple Packet blocks): mirrors
  ``tools/scanlab/capture_pcap.py:_iter_pcapng`` block-by-block, but streams
  from disk one block at a time (never materializes the whole file or every
  packet's payload) the way ``tools/capture_ledger.py:iter_pcapng_packets``
  already does for this project's other large-capture tooling. This matters
  here: this capture is ~877 MiB with ~918 MB of bulk-IN payload, and
  ``capture_pcap.py``'s ``analyze_usbpcap`` retains every bulk-IN chunk's
  bytes in memory (fine for its GUI image-decode use case, not for a
  register-ledger tool over this file size).
- USBPcap per-packet header layout (``_USBPCAP_HEADER`` struct, control
  stage bywte, transfer-type constants): identical struct format to
  ``tools/scanlab/capture_pcap.py``'s ``_USBPCAP_BASE`` /
  ``parse_usbpcap_packet`` (desowin.org/usbpcap/captureformat.html).
- ``VALUE_SET_REGISTER`` payload decode (address/value pairs, high-address
  0x100 flag): mirrors ``tools/scanlab/capture_pcap.py:_parse_set_register_
  payload`` / ``_apply_set_register`` exactly (same bit tests, same pairing).
- ``VALUE_GET_REGISTER`` address decode (``wIndex = 0x22 + (addr << 8)``):
  this is not a capture_pcap.py function — it is the literal encoding used
  by ``src/pyopticfilm/usb/protocol.py:GenesysUsbProtocol.read_register``
  (``address16 = 0x22 + (address << 8)``), so decoding it is just inverting
  that formula, not a new assumption. ``tools/capture_ledger.py`` already
  does this inversion (``addr = (windex - 0x22) >> 8``); reused verbatim.
- Constants (``REQUEST_TYPE_IN/OUT``, ``REQUEST_BUFFER``, ``VALUE_BUFFER``,
  ``VALUE_SET_REGISTER``, ``VALUE_GET_REGISTER``) are imported directly from
  ``pyopticfilm.usb.protocol`` — the single source of truth both
  ``capture_pcap.py`` and ``capture_ledger.py`` already import from.

Output
------
A JSON ledger: ``{"summary": {...}, "events": [...], "bulk": [...]}``.

- ``events``: chronological register writes/reads and buffer preambles.
  Every entry has ``packet_index`` (1-based, matching Wireshark/tshark
  ``frame.number`` for direct cross-check), ``timestamp`` (capture-relative
  seconds, or null), ``transfer_type`` (``"reg_write"`` | ``"reg_read"`` |
  ``"buffer_preamble"``), and for register events ``register_addr`` /
  ``register_value``; preamble events carry ``w_index`` / ``bulk_addr`` /
  ``bulk_size`` instead (``register_addr``/``register_value`` are null).
- ``bulk``: bulk transfer metadata only — ``packet_index``, ``timestamp``,
  ``direction`` (``"in"``/``"out"``), ``size``. No payload, no decode.

Use :func:`snapshot_at` (importable, or via ``--snapshot-at``) to reconstruct
the cumulative register file as of any packet index — i.e. "what were all
registers set to right before packet N".
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

# Force this repo's src/ first, matching tools/capture_ledger.py's pattern —
# the venv's editable install can resolve `pyopticfilm` to a different clone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pyopticfilm.usb.protocol import (  # noqa: E402
    REQUEST_BUFFER,
    REQUEST_TYPE_IN,
    REQUEST_TYPE_OUT,
    VALUE_BUFFER,
    VALUE_GET_REGISTER,
    VALUE_SET_REGISTER,
)

# --- USBPcap / pcapng framing -----------------------------------------------
# Transfer-type and stage constants per desowin.org/usbpcap/captureformat.html
# — identical values to tools/scanlab/capture_pcap.py's USBPCAP_TRANSFER_*.
TRANSFER_ISOCHRONOUS = 0
TRANSFER_INTERRUPT = 1
TRANSFER_CONTROL = 2
TRANSFER_BULK = 3

INFO_PDO_TO_FDO = 0x01  # completion / data-from-device

CONTROL_STAGE_SETUP = 0
CONTROL_STAGE_DATA = 1
CONTROL_STAGE_STATUS = 2
CONTROL_STAGE_COMPLETE = 3

# Same struct as tools/scanlab/capture_pcap.py's `_USBPCAP_BASE`.
_USBPCAP_HEADER = struct.Struct("<HQiHBHHBBI")
assert _USBPCAP_HEADER.size == 27


@dataclass
class _Interface:
    ts_resol_exponent: int = 6  # pcapng default: microseconds (10^-6)
    ts_resol_pow2: bool = False


def _decode_tsresol(byte_val: int) -> tuple[int, bool]:
    if byte_val & 0x80:
        return byte_val & 0x7F, True
    return byte_val & 0x7F, False


def iter_pcapng_packets(path: Path):
    """Stream ``(timestamp_seconds_or_None, packet_bytes)`` from a pcapng file.

    Reads one block at a time; never holds the whole file or prior packets in
    memory. See module docstring — this mirrors
    ``tools/capture_ledger.py:iter_pcapng_packets``, which is itself the
    streaming generalization of ``tools/scanlab/capture_pcap.py:_iter_pcapng``
    (same block types/offsets, disk-streamed instead of whole-buffer parsed).
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
            raise ValueError(
                f"{path}: not a pcapng file (classic .pcap not supported by "
                "this tool; all 8100 V2 2026-09 captures are pcapng)"
            )

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
                off = 8  # linktype u16, reserved u16, snaplen u32
                while off + 4 <= len(body):
                    opt_code, opt_len = struct.unpack_from(endian + "HH", body, off)
                    off += 4
                    if opt_code == 0 and opt_len == 0:
                        break
                    val = body[off : off + opt_len]
                    off += (opt_len + 3) & ~3
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
            elif block_type == 2 and len(body) >= 16:  # Packet Block (obsolete)
                caplen = struct.unpack_from(endian + "I", body, 8)[0]
                yield None, body[16 : 16 + caplen]
            # else: NRB/ISB/custom blocks — skip


def parse_usbpcap_header(payload: bytes):
    """Split one USBPcap packet body into (transfer, endpoint, info, data, stage, irp).

    Identical struct/field layout to
    ``tools/scanlab/capture_pcap.py:parse_usbpcap_packet``.
    """
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


# --- GL128 register write/read decode ---------------------------------------


def parse_set_register_payload(value_field: int, payload: bytes) -> list[tuple[int, int]]:
    """Decode a ``VALUE_SET_REGISTER`` OUT payload into ``(addr, value)`` pairs.

    Mirrors ``tools/scanlab/capture_pcap.py:_parse_set_register_payload``
    exactly: payload is a flat run of (addr_byte, value_byte) pairs; the
    0x100 bit of wValue marks every address in this transfer as
    high-address (``addr |= 0x100``).
    """
    high = bool(value_field & 0x100)
    out: list[tuple[int, int]] = []
    i = 0
    while i + 1 < len(payload):
        addr = payload[i]
        val = payload[i + 1]
        if high:
            addr |= 0x100
        out.append((addr, val))
        i += 2
    return out


def register_read_addr(wvalue: int, windex: int) -> int:
    """Invert ``GenesysUsbProtocol.read_register``'s address encoding.

    ``src/pyopticfilm/usb/protocol.py``: ``address16 = 0x22 + (address << 8)``,
    with ``usb_value |= 0x100`` when ``address > 0xFF``. So
    ``address = (windex - 0x22) >> 8``, with the high bit folded in from
    wValue for addresses above 0xFF. Same inversion tools/capture_ledger.py
    already uses.
    """
    addr = (windex - 0x22) >> 8
    if wvalue & 0x100:
        addr |= 0x100
    return addr & 0xFFFF


# --- ledger event types -------------------------------------------------


@dataclass
class RegEvent:
    packet_index: int  # 1-based; matches tshark/Wireshark frame.number
    timestamp: float | None
    transfer_type: str  # "reg_write" | "reg_read" | "buffer_preamble"
    register_addr: int | None = None
    register_value: int | None = None
    # buffer_preamble extras
    w_index: int | None = None
    bulk_addr: int | None = None
    bulk_size: int | None = None
    # reg_read extras (response arrives on a later packet)
    complete_packet_index: int | None = None
    complete_timestamp: float | None = None
    link_status: int | None = None

    def to_json(self) -> dict:
        d = {
            "packet_index": self.packet_index,
            "timestamp": self.timestamp,
            "transfer_type": self.transfer_type,
            "register_addr": self.register_addr,
            "register_value": self.register_value,
        }
        if self.transfer_type == "buffer_preamble":
            d["w_index"] = self.w_index
            d["bulk_addr"] = self.bulk_addr
            d["bulk_size"] = self.bulk_size
        if self.transfer_type == "reg_read":
            d["complete_packet_index"] = self.complete_packet_index
            d["complete_timestamp"] = self.complete_timestamp
            d["link_status"] = self.link_status
        return d


@dataclass
class BulkEvent:
    packet_index: int
    timestamp: float | None
    direction: str  # "in" | "out"
    size: int
    endpoint: int

    def to_json(self) -> dict:
        return {
            "packet_index": self.packet_index,
            "timestamp": self.timestamp,
            "direction": self.direction,
            "size": self.size,
            "endpoint": self.endpoint,
        }


def extract(path: Path) -> tuple[list[RegEvent], list[BulkEvent], dict]:
    """Walk one capture; return (register/preamble events, bulk metadata, summary)."""
    events: list[RegEvent] = []
    bulk: list[BulkEvent] = []
    # irp -> (addr, ts_submit, idx_submit) for outstanding GET_REGISTER reads.
    pending_reads: dict[int, tuple[int, float | None, int]] = {}

    n_packets = 0
    n_reg_write_pairs = 0
    n_reg_reads = 0
    n_preambles = 0
    n_bulk_in = 0
    n_bulk_out = 0
    bulk_in_bytes = 0
    bulk_out_bytes = 0
    first_ts: float | None = None
    last_ts: float | None = None

    for zero_idx, (ts, payload) in enumerate(iter_pcapng_packets(path)):
        idx = zero_idx + 1  # 1-based, matches Wireshark frame.number
        n_packets += 1
        parsed = parse_usbpcap_header(payload)
        if parsed is None:
            continue
        transfer, endpoint, info, data, stage, irp = parsed

        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts

        if transfer == TRANSFER_CONTROL:
            if stage == CONTROL_STAGE_SETUP and len(data) >= 8:
                bm, breq, wvalue, windex, wlen = struct.unpack_from("<BBHHH", data, 0)
                cmd = wvalue & 0xFF
                if bm == REQUEST_TYPE_IN and breq == REQUEST_BUFFER and cmd == VALUE_GET_REGISTER:
                    addr = register_read_addr(wvalue, windex)
                    pending_reads[irp] = (addr, ts, idx)
                elif bm == REQUEST_TYPE_OUT and breq == REQUEST_BUFFER and len(data) > 8:
                    payload_bytes = (
                        data[8 : 8 + wlen] if wlen and len(data) >= 8 + wlen else data[8:]
                    )
                    if cmd == VALUE_SET_REGISTER:
                        for addr, val in parse_set_register_payload(wvalue, payload_bytes):
                            events.append(
                                RegEvent(idx, ts, "reg_write", register_addr=addr, register_value=val)
                            )
                            n_reg_write_pairs += 1
                    elif cmd == VALUE_BUFFER and len(payload_bytes) >= 8:
                        bulk_addr = int.from_bytes(payload_bytes[0:4], "little")
                        bulk_size = int.from_bytes(payload_bytes[4:8], "little")
                        events.append(
                            RegEvent(
                                idx,
                                ts,
                                "buffer_preamble",
                                w_index=int(windex),
                                bulk_addr=bulk_addr,
                                bulk_size=bulk_size,
                            )
                        )
                        n_preambles += 1
            elif stage == CONTROL_STAGE_COMPLETE and irp in pending_reads and len(data) >= 2:
                addr, ts_submit, idx_submit = pending_reads.pop(irp)
                value, link_status = data[0], data[1]
                events.append(
                    RegEvent(
                        idx_submit,
                        ts_submit,
                        "reg_read",
                        register_addr=addr,
                        register_value=value,
                        complete_packet_index=idx,
                        complete_timestamp=ts,
                        link_status=link_status,
                    )
                )
                n_reg_reads += 1
            elif (
                stage == CONTROL_STAGE_COMPLETE
                and len(data) >= 10
                and (info & INFO_PDO_TO_FDO) == 0
            ):
                # Fallback layout (session captures sometimes put setup+data on
                # COMPLETE instead of SETUP) — same fallback capture_pcap.py's
                # analyze_usbpcap uses for OUT VALUE_SET_REGISTER/VALUE_BUFFER.
                bm, breq, wvalue, windex, wlen = struct.unpack_from("<BBHHH", data, 0)
                if (
                    bm == REQUEST_TYPE_OUT
                    and breq == REQUEST_BUFFER
                    and wlen > 0
                    and len(data) >= 8 + wlen
                ):
                    payload_bytes = data[8 : 8 + wlen]
                    cmd = wvalue & 0xFF
                    if cmd == VALUE_SET_REGISTER:
                        for addr, val in parse_set_register_payload(wvalue, payload_bytes):
                            events.append(
                                RegEvent(idx, ts, "reg_write", register_addr=addr, register_value=val)
                            )
                            n_reg_write_pairs += 1
                    elif cmd == VALUE_BUFFER and len(payload_bytes) >= 8:
                        bulk_addr = int.from_bytes(payload_bytes[0:4], "little")
                        bulk_size = int.from_bytes(payload_bytes[4:8], "little")
                        events.append(
                            RegEvent(
                                idx,
                                ts,
                                "buffer_preamble",
                                w_index=int(windex),
                                bulk_addr=bulk_addr,
                                bulk_size=bulk_size,
                            )
                        )
                        n_preambles += 1
        elif transfer == TRANSFER_BULK:
            direction = "in" if (endpoint & 0x80) else "out"
            is_complete = bool(info & INFO_PDO_TO_FDO)
            if direction == "in" and data and (is_complete or len(data) >= 64):
                n = len(data)
                bulk.append(BulkEvent(idx, ts, "in", n, endpoint))
                n_bulk_in += 1
                bulk_in_bytes += n
            elif direction == "out" and data and is_complete:
                n = len(data)
                bulk.append(BulkEvent(idx, ts, "out", n, endpoint))
                n_bulk_out += 1
                bulk_out_bytes += n

    summary = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "usbpcap_packet_count": n_packets,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "duration_s": (last_ts - first_ts) if (first_ts is not None and last_ts is not None) else None,
        "register_write_events": n_reg_write_pairs,
        "register_read_events": n_reg_reads,
        "buffer_preamble_count": n_preambles,
        "bulk_in_count": n_bulk_in,
        "bulk_out_count": n_bulk_out,
        "bulk_in_total_bytes": bulk_in_bytes,
        "bulk_out_total_bytes": bulk_out_bytes,
    }
    return events, bulk, summary


def snapshot_at(events: list[RegEvent], packet_index: int) -> dict[int, int]:
    """Cumulative register file (addr -> value) as of ``packet_index`` (inclusive).

    Only ``reg_write`` events update the register file (reads don't change
    ASIC state). Events are assumed chronological in ``packet_index`` order,
    which is how :func:`extract` produces them.
    """
    regs: dict[int, int] = {}
    for ev in events:
        if ev.packet_index > packet_index:
            break
        if ev.transfer_type == "reg_write" and ev.register_addr is not None:
            regs[ev.register_addr] = ev.register_value
    return regs


def u16(regs: dict[int, int], addr: int) -> int | None:
    if addr not in regs or (addr + 1) not in regs:
        return None
    return ((regs[addr] & 0xFF) << 8) | (regs[addr + 1] & 0xFF)


def u24(regs: dict[int, int], addr: int) -> int | None:
    if addr not in regs or (addr + 1) not in regs or (addr + 2) not in regs:
        return None
    return (
        ((regs[addr] & 0xFF) << 16)
        | ((regs[addr + 1] & 0xFF) << 8)
        | (regs[addr + 2] & 0xFF)
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("capture", type=Path, help="Path to .pcapng capture")
    ap.add_argument("--out", type=Path, required=True, help="Output ledger JSON path")
    ap.add_argument(
        "--snapshot-at",
        type=int,
        default=None,
        help="Also print the cumulative register snapshot as of this packet_index",
    )
    args = ap.parse_args(argv)

    events, bulk, summary = extract(args.capture)

    out_obj = {
        "summary": summary,
        "events": [e.to_json() for e in events],
        "bulk": [b.to_json() for b in bulk],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as f:
        json.dump(out_obj, f)

    print(json.dumps(summary, indent=2))
    print(f"events: {len(events)}  bulk: {len(bulk)}  -> {args.out}", file=sys.stderr)

    if args.snapshot_at is not None:
        regs = snapshot_at(events, args.snapshot_at)
        print(f"\nregister snapshot at packet_index<={args.snapshot_at}:", file=sys.stderr)
        for addr in sorted(regs):
            print(f"  0x{addr:03X} = 0x{regs[addr]:02X} ({regs[addr]})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
