# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse USBPcap / pcapng captures and decode Genesys image bulks for Scan Lab."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field, replace
from pathlib import Path

from pyopticfilm.device.protocol import FilmModel
from pyopticfilm.device.select import model_is_scan_ready
from pyopticfilm.scan.geometry import ScanGeometry, compute_geometry
from pyopticfilm.scan.pipeline import ImagePipeline
from pyopticfilm.usb.protocol import (
    REQUEST_BUFFER,
    REQUEST_TYPE_OUT,
    VALUE_BUFFER,
    VALUE_SET_REGISTER,
)
from pyopticfilm.usb.trace import format_usb_log_line
from tools.scanlab.backend import lab_scan_kwargs, nonse_safe_area, usb_log_divider

# USBPcap transfer types (desowin.org/usbpcap/captureformat.html)
USBPCAP_TRANSFER_ISOCHRONOUS = 0
USBPCAP_TRANSFER_INTERRUPT = 1
USBPCAP_TRANSFER_CONTROL = 2
USBPCAP_TRANSFER_BULK = 3

USBPCAP_INFO_PDO_TO_FDO = 0x01  # complete / from device

USBPCAP_CONTROL_STAGE_SETUP = 0
USBPCAP_CONTROL_STAGE_DATA = 1
USBPCAP_CONTROL_STAGE_STATUS = 2
USBPCAP_CONTROL_STAGE_COMPLETE = 3

#: SilverFast / SE image buffer preamble uses wIndex=0x08.
_IMAGE_BUFFER_WINDEX = 0x08

#: Native optical resolution for GL128 STR/END units.
_GL128_NATIVE_DPI = 7200

_USBPCAP_BASE = struct.Struct("<HQiHBHHBBI")
assert _USBPCAP_BASE.size == 27


@dataclass(frozen=True)
class UsbPcapPacket:
    """One USBPcap URB payload (after the pcap record header)."""

    transfer: int
    endpoint: int
    info: int
    status: int
    function: int
    data: bytes
    control_stage: int | None = None


@dataclass(frozen=True)
class BufferPreamble:
    """Genesys ``VALUE_BUFFER`` (0x82) OUT: declares upcoming bulk size."""

    packet_index: int
    w_index: int
    bulk_addr: int
    bulk_size: int


@dataclass
class CaptureAnalysis:
    """Parsed capture useful for Lab decode + motor register compare."""

    path: Path
    packets: list[UsbPcapPacket] = field(default_factory=list)
    register_writes: list[tuple[int, int]] = field(default_factory=list)
    #: ``(packet_index, addr, value)`` chronologically for snapshots.
    register_events: list[tuple[int, int, int]] = field(default_factory=list)
    #: Last value per ASIC address (from SET_REGISTER payloads).
    registers: dict[int, int] = field(default_factory=dict)
    bulk_ins: list[bytes] = field(default_factory=list)
    buffer_preambles: list[BufferPreamble] = field(default_factory=list)
    #: Preamble used for :meth:`image_bulk` (if any).
    image_preamble: BufferPreamble | None = None

    @property
    def largest_bulk_in(self) -> bytes | None:
        """Deprecated alias — prefer :meth:`image_bulk`."""
        return self.image_bulk

    @property
    def image_bulk(self) -> bytes | None:
        """Image payload carved after the buffer preamble when present.

        SilverFast announces the image with ``VALUE_BUFFER`` / ``wIndex=0x08``
        and ``bulk_size = LINCNT × width × 3``. Bulk-IN URBs after that
        (including short 512-byte packets) concatenate to exactly that size.
        Without a preamble, fall back to coalescing large bulk INs.
        """
        carved = carve_image_bulk(self)
        if carved:
            return carved
        return coalesce_image_bulk(self.bulk_ins)


def image_chunk_mode(bulk_ins: list[bytes]) -> int | None:
    """Dominant image URB size (one optical line on the wire).

    Chooses the size that contributes the most total payload among large
    packets (>= 2048). Tiny fixtures fall back to the max chunk size.
    """
    if not bulk_ins:
        return None
    from collections import Counter

    counts = Counter(len(b) for b in bulk_ins)
    max_sz = max(counts)
    if max_sz >= 2048:
        pool = {sz: n for sz, n in counts.items() if sz >= 2048}
    else:
        pool = {sz: n for sz, n in counts.items() if sz >= 64}
    if not pool:
        return max_sz
    # Prefer larger URBs when totals tie (status 512 vs short image lines).
    return max(pool.items(), key=lambda kv: (kv[0] * kv[1], kv[0]))[0]


def coalesce_image_bulk(bulk_ins: list[bytes]) -> bytes | None:
    """Join the longest run of image-sized bulk-IN chunks into one buffer.

    Real Genesys captures interleave ~512-byte status INs between image URBs.
    Those must be skipped without breaking the run. Chunk size is taken from
    :func:`image_chunk_mode` (not the max — a single outlier used to collapse
    the image to one line). Packets that are ``mode`` or ``mode+512`` (USB
    sometimes glues the status word onto the image URB) contribute ``mode``
    bytes each.
    """
    if not bulk_ins:
        return None
    mode_sz = image_chunk_mode(bulk_ins)
    if mode_sz is None or mode_sz < 1:
        return max(bulk_ins, key=len)
    threshold = max(64, int(mode_sz * 0.9))
    # Skip status-sized INs; break the run on other mid-size payloads.
    skip_below = min(2048, max(64, mode_sz // 2)) if mode_sz >= 2048 else max(1, mode_sz // 2)

    best = b""
    cur = bytearray()
    for b in bulk_ins:
        n = len(b)
        if n >= threshold:
            if mode_sz <= n <= mode_sz + 1024:
                cur.extend(b[:mode_sz])
            else:
                cur.extend(b)
        elif n < skip_below:
            continue
        else:
            if len(cur) > len(best):
                best = bytes(cur)
            cur = bytearray()
    if len(cur) > len(best):
        best = bytes(cur)
    return best or max(bulk_ins, key=len)


def pick_image_preamble(
    preambles: list[BufferPreamble],
    *,
    expected_size: int | None = None,
) -> BufferPreamble | None:
    """Prefer ``wIndex=0x08`` sized like the image; else the largest preamble."""
    if not preambles:
        return None
    indexed = [p for p in preambles if p.w_index == _IMAGE_BUFFER_WINDEX]
    pool = indexed or list(preambles)
    if expected_size is not None and expected_size > 0:
        exact = [p for p in pool if p.bulk_size == expected_size]
        if exact:
            return exact[-1]
        near = sorted(pool, key=lambda p: abs(p.bulk_size - expected_size))
        if near and abs(near[0].bulk_size - expected_size) < expected_size * 0.05:
            return near[0]
    return max(pool, key=lambda p: p.bulk_size)


def carve_image_bulk(analysis: CaptureAnalysis) -> bytes | None:
    """Concatenate bulk-IN payloads after the image buffer preamble."""
    # Expected size from last register snapshot (SilverFast 8-bit host formula).
    snap = optical_snapshot(analysis.registers, asic="GL128")
    expected = None
    if (
        snap["dpiset"]
        and snap["strpixel"] is not None
        and snap["endpixel"] is not None
        and snap["lincnt"]
        and snap["endpixel"] > snap["strpixel"]
    ):
        dpi = int(snap["dpiset"]) * 6
        factor = max(1, _GL128_NATIVE_DPI // max(1, dpi))
        width = (int(snap["endpixel"]) - int(snap["strpixel"])) // factor
        expected = int(snap["lincnt"]) * width * 3

    preamble = pick_image_preamble(analysis.buffer_preambles, expected_size=expected)
    if preamble is None or preamble.bulk_size < 64:
        return None
    analysis.image_preamble = preamble
    need = int(preamble.bulk_size)
    out = bytearray()
    for pkt in analysis.packets[preamble.packet_index + 1 :]:
        if pkt.transfer != USBPCAP_TRANSFER_BULK:
            continue
        if not (pkt.endpoint & 0x80) or not pkt.data:
            continue
        piece = pkt.data
        room = need - len(out)
        if room <= 0:
            break
        if len(piece) > room:
            piece = piece[:room]
        out.extend(piece)
        if len(out) >= need:
            break
    if len(out) < need:
        return None
    return bytes(out[:need])


def _u16(regs: dict[int, int], addr: int) -> int | None:
    if addr not in regs or (addr + 1) not in regs:
        return None
    return ((regs[addr] & 0xFF) << 8) | (regs[addr + 1] & 0xFF)


def _u24(regs: dict[int, int], addr: int) -> int | None:
    if addr not in regs or (addr + 1) not in regs or (addr + 2) not in regs:
        return None
    return (
        ((regs[addr] & 0xFF) << 16)
        | ((regs[addr + 1] & 0xFF) << 8)
        | (regs[addr + 2] & 0xFF)
    )


def parse_usbpcap_packet(payload: bytes) -> UsbPcapPacket | None:
    """Parse one LINKTYPE_USBPCAP packet body."""
    if len(payload) < _USBPCAP_BASE.size:
        return None
    (
        header_len,
        _irp,
        status,
        function,
        info,
        _bus,
        _device,
        endpoint,
        transfer,
        data_length,
    ) = _USBPCAP_BASE.unpack_from(payload, 0)
    if header_len < _USBPCAP_BASE.size or header_len > len(payload):
        return None
    data = payload[header_len : header_len + data_length]
    stage = None
    if transfer == USBPCAP_TRANSFER_CONTROL and header_len >= _USBPCAP_BASE.size + 1:
        stage = payload[_USBPCAP_BASE.size]
    return UsbPcapPacket(
        transfer=transfer,
        endpoint=endpoint & 0xFF,
        info=info & 0xFF,
        status=status,
        function=function,
        data=bytes(data),
        control_stage=stage,
    )


def _iter_pcap_classic(data: bytes) -> list[bytes]:
    """Yield link-layer payloads from a classic pcap file."""
    if len(data) < 24:
        return []
    magic = struct.unpack_from("<I", data, 0)[0]
    swapped = magic in {0xD4C3B2A1, 0x4D3CB2A1}
    if magic not in {0xA1B2C3D4, 0xA1B23C4D, 0xD4C3B2A1, 0x4D3CB2A1}:
        return []
    endian = ">" if swapped else "<"
    hdr = struct.Struct(endian + "IIII")
    rec = struct.Struct(endian + "IIII")
    off = 24
    out: list[bytes] = []
    while off + rec.size <= len(data):
        _ts_sec, _ts_usec, incl_len, _orig = rec.unpack_from(data, off)
        off += rec.size
        if incl_len < 0 or off + incl_len > len(data):
            break
        out.append(data[off : off + incl_len])
        off += incl_len
    _ = hdr  # silence unused — global header already skipped
    return out


def _iter_pcapng(data: bytes) -> list[bytes]:
    """Yield packet data from pcapng Enhanced Packet / Simple Packet blocks."""
    if len(data) < 12:
        return []
    # SHB: type, total_length, byte_order_magic
    endian = "<"
    if len(data) >= 12:
        typ0 = struct.unpack_from("<I", data, 0)[0]
        if typ0 == 0x0A0D0D0A:
            if struct.unpack_from("<I", data, 8)[0] == 0x1A2B3C4D:
                endian = "<"
            elif struct.unpack_from(">I", data, 8)[0] == 0x1A2B3C4D:
                endian = ">"
    u32 = struct.Struct(endian + "I")
    out: list[bytes] = []
    off = 0
    while off + 8 <= len(data):
        block_type = u32.unpack_from(data, off)[0]
        block_len = u32.unpack_from(data, off + 4)[0]
        if block_len < 12 or off + block_len > len(data):
            break
        body = data[off + 8 : off + block_len - 4]
        if block_type == 6 and len(body) >= 20:  # Enhanced Packet Block
            # iface_id, ts_high, ts_low, caplen, origlen
            caplen = u32.unpack_from(body, 12)[0]
            pkt = body[20 : 20 + caplen]
            out.append(pkt)
        elif block_type == 3 and len(body) >= 4:  # Simple Packet Block
            origlen = u32.unpack_from(body, 0)[0]
            pkt = body[4 : 4 + origlen]
            out.append(pkt)
        elif block_type == 2 and len(body) >= 16:  # Packet Block (obsolete)
            caplen = u32.unpack_from(body, 8)[0]
            pkt = body[16 : 16 + caplen]
            out.append(pkt)
        off += block_len
        # pcapng blocks are padded to 32-bit; total_length already includes padding
    return out


def load_usbpcap_packets(path: Path | str) -> list[UsbPcapPacket]:
    """Load USBPcap packets from ``.pcap`` / ``.pcapng``."""
    raw = Path(path).read_bytes()
    link_payloads = _iter_pcapng(raw)
    if not link_payloads:
        link_payloads = _iter_pcap_classic(raw)
    packets: list[UsbPcapPacket] = []
    for payload in link_payloads:
        pkt = parse_usbpcap_packet(payload)
        if pkt is not None:
            packets.append(pkt)
    return packets


def _parse_set_register_payload(value_field: int, payload: bytes) -> list[tuple[int, int]]:
    """Decode Genesys ``VALUE_SET_REGISTER`` data as ``(addr, value)`` pairs."""
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


def _apply_set_register(
    analysis: CaptureAnalysis,
    wvalue: int,
    payload: bytes,
    *,
    packet_index: int = -1,
) -> None:
    pairs = _parse_set_register_payload(wvalue, payload)
    for addr, val in pairs:
        analysis.register_writes.append((addr, val))
        analysis.registers[addr] = val
        if packet_index >= 0:
            analysis.register_events.append((packet_index, addr, val))


def registers_before_packet(
    analysis: CaptureAnalysis, packet_index: int, *, asic: str | None = None
) -> dict[int, int]:
    """Register file as of the last write at or before ``packet_index``."""
    regs: dict[int, int] = {}
    for pi, addr, val in analysis.register_events:
        if pi > packet_index:
            break
        regs[addr] = val
    if not regs:
        regs = dict(analysis.registers)
    return regs


def analyze_usbpcap(path: Path | str) -> CaptureAnalysis:
    """Parse a capture and collect register writes + bulk IN payloads."""
    path = Path(path)
    packets = load_usbpcap_packets(path)
    analysis = CaptureAnalysis(path=path, packets=packets)

    pending_setup: tuple[int, int, int, int, int] | None = None
    # (bmRequestType, bRequest, wValue, wIndex, wLength)

    for pkt_index, pkt in enumerate(packets):
        if pkt.transfer == USBPCAP_TRANSFER_CONTROL:
            if pkt.control_stage == USBPCAP_CONTROL_STAGE_SETUP and len(pkt.data) >= 8:
                bm, breq, wvalue, windex, wlen = struct.unpack_from("<BBHHH", pkt.data, 0)
                pending_setup = (bm, breq, wvalue, windex, wlen)
                # USBPcap often embeds OUT data immediately after the 8-byte setup
                # (no separate DATA-stage URB). Session captures use this layout.
                if (
                    bm == REQUEST_TYPE_OUT
                    and breq == REQUEST_BUFFER
                    and len(pkt.data) > 8
                ):
                    payload = (
                        pkt.data[8 : 8 + wlen]
                        if wlen > 0 and len(pkt.data) >= 8 + wlen
                        else pkt.data[8:]
                    )
                    cmd = wvalue & 0xFF
                    if cmd == VALUE_SET_REGISTER:
                        _apply_set_register(
                            analysis, wvalue, payload, packet_index=pkt_index
                        )
                    elif cmd == VALUE_BUFFER and len(payload) >= 8:
                        analysis.buffer_preambles.append(
                            BufferPreamble(
                                packet_index=pkt_index,
                                w_index=int(windex),
                                bulk_addr=int.from_bytes(payload[0:4], "little"),
                                bulk_size=int.from_bytes(payload[4:8], "little"),
                            )
                        )
            elif (
                pkt.control_stage == USBPCAP_CONTROL_STAGE_DATA
                and pending_setup is not None
                and pkt.data
            ):
                bm, breq, wvalue, windex, _wlen = pending_setup
                if bm == REQUEST_TYPE_OUT and breq == REQUEST_BUFFER:
                    cmd = wvalue & 0xFF
                    if cmd == VALUE_SET_REGISTER:
                        _apply_set_register(
                            analysis, wvalue, pkt.data, packet_index=pkt_index
                        )
                    elif cmd == VALUE_BUFFER and len(pkt.data) >= 8:
                        analysis.buffer_preambles.append(
                            BufferPreamble(
                                packet_index=pkt_index,
                                w_index=int(windex),
                                bulk_addr=int.from_bytes(pkt.data[0:4], "little"),
                                bulk_size=int.from_bytes(pkt.data[4:8], "little"),
                            )
                        )
                pending_setup = None
            elif pkt.control_stage in {
                USBPCAP_CONTROL_STAGE_STATUS,
                USBPCAP_CONTROL_STAGE_COMPLETE,
                None,
            }:
                # Some captures put setup+data differently; try raw 8+payload on complete OUT
                if (
                    pkt.control_stage == USBPCAP_CONTROL_STAGE_COMPLETE
                    and len(pkt.data) >= 10
                    and (pkt.info & USBPCAP_INFO_PDO_TO_FDO) == 0
                ):
                    bm, breq, wvalue, windex, wlen = struct.unpack_from("<BBHHH", pkt.data, 0)
                    if (
                        bm == REQUEST_TYPE_OUT
                        and breq == REQUEST_BUFFER
                        and wlen > 0
                        and len(pkt.data) >= 8 + wlen
                    ):
                        payload = pkt.data[8 : 8 + wlen]
                        cmd = wvalue & 0xFF
                        if cmd == VALUE_SET_REGISTER:
                            _apply_set_register(
                                analysis, wvalue, payload, packet_index=pkt_index
                            )
                        elif cmd == VALUE_BUFFER and len(payload) >= 8:
                            analysis.buffer_preambles.append(
                                BufferPreamble(
                                    packet_index=pkt_index,
                                    w_index=int(windex),
                                    bulk_addr=int.from_bytes(payload[0:4], "little"),
                                    bulk_size=int.from_bytes(payload[4:8], "little"),
                                )
                            )

        elif pkt.transfer == USBPCAP_TRANSFER_BULK:
            # IN endpoint: bit 7 set
            if (pkt.endpoint & 0x80) and (pkt.info & USBPCAP_INFO_PDO_TO_FDO) and pkt.data:
                analysis.bulk_ins.append(pkt.data)
            elif (pkt.endpoint & 0x80) and pkt.data and len(pkt.data) >= 64:
                # Fallback: accept IN payloads even if info bit missing
                analysis.bulk_ins.append(pkt.data)

    return analysis


def optical_snapshot(
    regs: dict[int, int], *, asic: str | None = None
) -> dict[str, int | None]:
    """Key optical / motor registers from a write snapshot.

    GL128 (8200i SE) uses 24-bit ``STRPIXEL``/``ENDPIXEL`` at ``0x82``/``0x85``.
    GL845-family tables use 16-bit values at ``0x30``/``0x32``.
    """
    asic_u = (asic or "").upper()
    if asic_u in {"GL128", "GL124"}:
        strpixel = _u24(regs, 0x82)
        endpixel = _u24(regs, 0x85)
        lperiod = _u24(regs, 0x28)
    else:
        strpixel = _u16(regs, 0x30)
        endpixel = _u16(regs, 0x32)
        lperiod = _u16(regs, 0x38)
        # If GL845-style addresses are empty but GL128 ones are present, fall back.
        if strpixel is None and endpixel is None:
            strpixel = _u24(regs, 0x82)
            endpixel = _u24(regs, 0x85)
    return {
        "dpiset": _u16(regs, 0x2C),
        "strpixel": strpixel,
        "endpixel": endpixel,
        "lincnt": _u24(regs, 0x25),
        "feedl": _u24(regs, 0x3D),
        "lperiod": lperiod,
        "dummy": regs.get(0x34),
        "reg01": regs.get(0x01),
        "reg04": regs.get(0x04),
    }


def capture_looks_gl128(analysis: CaptureAnalysis) -> bool:
    """True when the capture has SE-style buffer preamble + STRPIXEL at 0x82."""
    if any(
        p.w_index == _IMAGE_BUFFER_WINDEX and p.bulk_size >= 64 for p in analysis.buffer_preambles
    ) and _u24(analysis.registers, 0x82) is not None and _u24(analysis.registers, 0x85) is not None:
        return True
    snap = optical_snapshot(analysis.registers, asic="GL128")
    return (
        snap["strpixel"] is not None
        and snap["endpixel"] is not None
        and snap["endpixel"] > snap["strpixel"]
        and snap["dpiset"] is not None
        and snap["lincnt"] is not None
    )


def model_for_capture_decode(analysis: CaptureAnalysis, selected: FilmModel) -> FilmModel:
    """Prefer 8200i SE tables when the capture is clearly GL128."""
    if capture_looks_gl128(analysis):
        from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE

        return MODEL_8200I_SE
    return selected


def dpi_from_dpiset(model: FilmModel, dpiset: int | None) -> int | None:
    if dpiset is None:
        return None
    table = getattr(model, "register_dpiset_by_dpi", {}) or {}
    for dpi, val in table.items():
        if int(val) == int(dpiset):
            return int(dpi)
    return None


def _best_gl128_width(bulk_len: int, hint: int) -> int:
    """Choose a 16-bit RGB line width that divides ``bulk_len``, near ``hint``."""
    hint = max(hint, 1)
    best_w = hint
    best_key: tuple[int, int] | None = None
    for dw in range(65):
        for sign in (0, -1, 1) if dw == 0 else (-1, 1):
            w = hint + sign * dw
            if w < 8:
                continue
            lb = w * 6
            if bulk_len % lb != 0:
                continue
            key = (abs(w - hint), -w)
            if best_key is None or key < best_key:
                best_key = key
                best_w = w
        if dw == 0 and best_key is not None:
            return best_w
    return best_w


def geometry_for_capture_decode(
    model: FilmModel,
    analysis: CaptureAnalysis,
    *,
    dpi: int | None = None,
    area: tuple[float, float, float, float] | None = None,
) -> ScanGeometry:
    """Build geometry for decoding the carved image bulk, preferring capture regs."""
    asic = str(getattr(model, "asic", "") or "")
    asic_u = asic.upper()
    # Prefer register state just before the image preamble (avoids teardown noise).
    pre = analysis.image_preamble
    if pre is not None and analysis.register_events:
        regs = registers_before_packet(analysis, pre.packet_index, asic=asic)
    else:
        regs = analysis.registers
    snap = optical_snapshot(regs, asic=asic)
    inferred_dpi = dpi_from_dpiset(model, snap["dpiset"])
    if inferred_dpi is None and snap["dpiset"]:
        inferred_dpi = int(snap["dpiset"]) * 6
    # Capture DPISET wins over the Lab PPI spinner — wrong DPI shears the image
    # (width = span / (7200/dpi)).
    use_dpi = int(inferred_dpi or dpi or min(model.resolutions_dpi))
    if area is None:
        if model_is_scan_ready(model):
            geo = compute_geometry(use_dpi, model=model, area=None)
        else:
            area = nonse_safe_area(model)
            geo = compute_geometry(use_dpi, model=model, area=area)
    else:
        geo = compute_geometry(use_dpi, model=model, area=area)

    # Ensure image_bulk / preamble are resolved before sizing.
    bulk = analysis.image_bulk or b""
    if analysis.image_preamble is not None and analysis.image_preamble is not pre:
        pre = analysis.image_preamble
        regs = registers_before_packet(analysis, pre.packet_index, asic=asic)
        snap = optical_snapshot(regs, asic=asic)
        inferred_dpi = dpi_from_dpiset(model, snap["dpiset"])
        if inferred_dpi is None and snap["dpiset"]:
            inferred_dpi = int(snap["dpiset"]) * 6
        new_dpi = int(inferred_dpi or dpi or min(model.resolutions_dpi))
        if new_dpi != use_dpi:
            use_dpi = new_dpi
            if area is None:
                if model_is_scan_ready(model):
                    geo = compute_geometry(use_dpi, model=model, area=None)
                else:
                    geo = compute_geometry(
                        use_dpi, model=model, area=nonse_safe_area(model)
                    )
            else:
                geo = compute_geometry(use_dpi, model=model, area=area)

    channels = 3
    strp = snap["strpixel"]
    endp = snap["endpixel"]
    lincnt = snap["lincnt"]

    # --- GL128 (8200i SE): host sizes as LINCNT×width×3, wire is 16-bit chunky ---
    if asic_u in {"GL128", "GL124"} and strp is not None and endp is not None and endp > strp:
        factor = max(1, _GL128_NATIVE_DPI // max(1, use_dpi))
        span_w = (int(endp) - int(strp)) // factor
        # Authoritative width from declared bulk size when it divides cleanly.
        width = span_w if span_w >= 1 else int(geo.pixels)
        if lincnt and bulk and int(lincnt) > 0 and len(bulk) % (int(lincnt) * 3) == 0:
            width = len(bulk) // (int(lincnt) * 3)
        elif bulk:
            # Nearby width search: pick stride that divides the buffer and is
            # closest to the span-derived width (fixes off-by-few shearing).
            width = _best_gl128_width(len(bulk), span_w if span_w >= 1 else width)
        if width < 1:
            width = int(geo.pixels)
        depth = 16
        line_bytes = width * channels * (depth // 8)
        usb_rows = (int(lincnt) // 2) if lincnt else 0
        if line_bytes > 0 and bulk:
            fit = len(bulk) // line_bytes
            if fit >= 1 and (
                usb_rows <= 0 or fit < usb_rows or len(bulk) == fit * line_bytes
            ):
                usb_rows = fit
        usb_rows = max(1, usb_rows)
        out_lines = (
            (int(lincnt) // factor) if lincnt else max(1, usb_rows // max(1, factor // 2))
        )
        out_lines = max(1, min(out_lines, usb_rows))
        # Keep ld_shift_* from compute_geometry (tri-linear CCD line offsets).
        return replace(
            geo,
            pixels=width,
            optical_pixels=width,
            depth=depth,
            line_bytes=line_bytes,
            resolution=use_dpi,
            register_dpiset=int(snap["dpiset"] or geo.register_dpiset),
            pixel_startx=int(strp),
            pixel_endx=int(endp),
            register_lincnt=int(lincnt or usb_rows),
            optical_line_count=usb_rows,
            lines=out_lines,
            stagger_y=(),
            num_staggered_lines=0,
        )

    # --- GL845-family / fallback ---
    mode = image_chunk_mode(analysis.bulk_ins)
    depth = 16
    if mode and mode % 6 == 0:
        depth = 16
    elif (mode and mode % 3 == 0) or (
        snap["reg04"] is not None and (snap["reg04"] & 0x40) == 0
    ):
        depth = 8
    bpp = depth // 8

    line_bytes = 0
    if mode and mode % (channels * bpp) == 0 and bulk and len(bulk) % mode == 0:
        line_bytes = int(mode)
    if (
        line_bytes <= 0
        and strp is not None
        and endp is not None
        and endp > strp
    ):
        cand = int(endp - strp) * channels * bpp
        if cand > 0 and (not bulk or len(bulk) % cand == 0):
            line_bytes = cand
    if line_bytes <= 0 and geo.line_bytes > 0 and bulk and len(bulk) % geo.line_bytes == 0:
        line_bytes = int(geo.line_bytes)

    if line_bytes > 0 and line_bytes % (channels * bpp) == 0:
        pixels = line_bytes // (channels * bpp)
        geo = replace(
            geo,
            pixels=pixels,
            optical_pixels=pixels,
            depth=depth,
            line_bytes=line_bytes,
            resolution=use_dpi,
            register_dpiset=int(snap["dpiset"] or geo.register_dpiset),
        )

    if strp is not None and endp is not None and endp > strp:
        geo = replace(geo, pixel_startx=int(strp), pixel_endx=int(endp))

    if lincnt is not None and lincnt > 0:
        geo = replace(geo, register_lincnt=int(lincnt))

    if bulk and geo.line_bytes > 0:
        rows = len(bulk) // geo.line_bytes
        if rows >= 1:
            geo = replace(geo, optical_line_count=rows, lines=rows)
    return geo


def decode_capture_bulk(
    model: FilmModel,
    analysis: CaptureAnalysis,
    *,
    dpi: int | None = None,
    planar: bool = False,
    area: tuple[float, float, float, float] | None = None,
):
    """Decode coalesced bulk IN through :class:`ImagePipeline` (no calib)."""
    import numpy as np

    bulk = analysis.image_bulk
    if not bulk:
        raise ValueError("No bulk IN image data found in capture")
    geo = geometry_for_capture_decode(model, analysis, dpi=dpi, area=area)
    need = geo.total_bytes
    if len(bulk) < need:
        raise ValueError(
            f"Bulk too short for geometry: {len(bulk)} < {need} bytes "
            f"({len(analysis.bulk_ins)} chunks coalesced)"
        )
    pipe = ImagePipeline(model)
    rgb = pipe.decode_rgb(bulk[:need], geometry=geo, planar=planar)
    rgb = pipe.reduce_y_oversample(rgb, geo)
    try:
        rgb = pipe.apply_line_shifts(rgb, geo)
    except ValueError:
        pass
    try:
        rgb = pipe.apply_y_stagger(rgb, geo)
    except ValueError:
        pass
    rgb = pipe.apply_host_downsample(rgb, geo)
    return np.asarray(rgb, dtype=np.uint16), geo


def motor_register_diff(
    model: FilmModel,
    analysis: CaptureAnalysis,
    *,
    dpi: int,
    crop_norm: tuple[float, float, float, float] | None = None,
) -> list[str]:
    """Compare capture FEEDL/LINCNT/DPISET to what Lab would program."""
    asic = str(getattr(model, "asic", "") or "")
    snap = optical_snapshot(analysis.registers, asic=asic)
    kw = lab_scan_kwargs(model, dpi=dpi, kind="scan", crop_norm=crop_norm)
    if "geometry" in kw and kw["geometry"] is not None:
        geo = kw["geometry"]
    else:
        geo = compute_geometry(dpi, model=model, area=kw.get("area"))

    # Lab FEEDL ≈ starty << step_type (see session._configure)
    step_type = int(getattr(model.motor_profile, "step_type", 0) or 0)
    lab_feedl = int(geo.starty) << step_type
    lab_lincnt = int(geo.lincnt_register)
    lab_dpiset = int(geo.register_dpiset)

    lines: list[str] = []
    lines.append(f"Lab geometry dpi={dpi} starty={geo.starty} pixels={geo.pixels}")
    lines.append(f"Lab would program DPISET={lab_dpiset} LINCNT={lab_lincnt} FEEDL≈{lab_feedl}")
    if snap["dpiset"] is not None:
        mark = "OK" if snap["dpiset"] == lab_dpiset else "DIFF"
        lines.append(f"Capture DPISET={snap['dpiset']} [{mark}]")
    else:
        lines.append("Capture DPISET=(missing)")
    if snap["lincnt"] is not None:
        mark = "OK" if snap["lincnt"] == lab_lincnt else "DIFF"
        lines.append(f"Capture LINCNT={snap['lincnt']} [{mark}]")
    else:
        lines.append("Capture LINCNT=(missing)")
    if snap["feedl"] is not None:
        mark = "OK" if snap["feedl"] == lab_feedl else "DIFF"
        lines.append(f"Capture FEEDL={snap['feedl']} [{mark}]")
    else:
        lines.append("Capture FEEDL=(missing)")
    if snap["strpixel"] is not None and snap["endpixel"] is not None:
        lines.append(
            f"Capture STRPIXEL={snap['strpixel']} ENDPIXEL={snap['endpixel']} "
            f"(width={snap['endpixel'] - snap['strpixel']})"
        )
    bulk = analysis.image_bulk
    if bulk is not None:
        pre = analysis.image_preamble
        if pre is not None:
            lines.append(
                f"Image bulk: {len(bulk)} bytes carved after VALUE_BUFFER "
                f"wIndex=0x{pre.w_index:02x} (declared {pre.bulk_size})"
            )
        else:
            mode = image_chunk_mode(analysis.bulk_ins)
            lines.append(
                f"Image bulk: {len(bulk)} bytes coalesced from {len(analysis.bulk_ins)} "
                f"IN chunk(s) (mode {mode})"
            )
    lines.append(f"Buffer preambles: {len(analysis.buffer_preambles)}")
    lines.append(f"Register writes parsed: {len(analysis.register_writes)}")
    return lines


def _format_control_pcap_line(pkt: UsbPcapPacket, *, max_data: int = 24) -> str | None:
    """Format a control URB for the Lab USB log (SETUP / embedded-data preferred)."""
    from pyopticfilm.usb.trace import UsbTransaction

    if pkt.control_stage not in {
        USBPCAP_CONTROL_STAGE_SETUP,
        USBPCAP_CONTROL_STAGE_COMPLETE,
        None,
    }:
        # DATA/STATUS stages are covered by SETUP-with-payload or skipped.
        return None
    if len(pkt.data) < 8:
        return None
    # COMPLETE often repeats setup; only log host→device completes with payload.
    if pkt.control_stage == USBPCAP_CONTROL_STAGE_COMPLETE and (
        (pkt.info & USBPCAP_INFO_PDO_TO_FDO) or len(pkt.data) <= 8
    ):
        return None
    bm, breq, wvalue, windex, wlen = struct.unpack_from("<BBHHH", pkt.data, 0)
    payload = pkt.data[8:] if len(pkt.data) > 8 else b""
    if wlen > 0 and len(payload) > wlen:
        payload = payload[:wlen]
    is_in = bool(bm & 0x80)
    if is_in and not payload:
        return format_usb_log_line(
            UsbTransaction(
                operation="control_read",
                request_type=bm,
                request=breq,
                value=wvalue,
                index=windex,
                length=wlen,
            ),
            max_data=max_data,
        )
    return format_usb_log_line(
        UsbTransaction(
            operation="control_read" if is_in else "control_write",
            request_type=bm,
            request=breq,
            value=wvalue,
            index=windex,
            length=wlen if not payload else len(payload),
            data=payload or None,
        ),
        max_data=max_data,
    )


def format_capture_usb_log_lines(
    analysis: CaptureAnalysis, *, max_data: int = 24
) -> list[str]:
    """USB log lines for a parsed capture (same style as live Lab traffic).

    Consecutive identical bulk-IN lengths are collapsed (``×N``) so image
    transfers stay readable.
    """
    lines: list[str] = [usb_log_divider(f"CAPTURE {analysis.path.name}")]
    pending: tuple[int, int, int] | None = None  # ep, length, count

    def flush_bulk() -> None:
        nonlocal pending
        if pending is None:
            return
        ep, length, count = pending
        if count <= 1:
            lines.append(f"bulk_read  ep=0x{ep:02x} length={length}")
        else:
            lines.append(f"bulk_read  ep=0x{ep:02x} length={length} ×{count}")
        pending = None

    for pkt in analysis.packets:
        if pkt.transfer == USBPCAP_TRANSFER_BULK:
            if (pkt.endpoint & 0x80) and pkt.data:
                ep = int(pkt.endpoint) & 0xFF
                n = len(pkt.data)
                if pending is not None and pending[0] == ep and pending[1] == n:
                    pending = (ep, n, pending[2] + 1)
                else:
                    flush_bulk()
                    pending = (ep, n, 1)
            elif pkt.data:
                flush_bulk()
                from pyopticfilm.usb.trace import UsbTransaction

                lines.append(
                    format_usb_log_line(
                        UsbTransaction(
                            operation="bulk_write",
                            endpoint=int(pkt.endpoint) & 0xFF,
                            data=pkt.data,
                            length=len(pkt.data),
                        ),
                        max_data=max_data,
                    )
                )
            continue

        if pkt.transfer != USBPCAP_TRANSFER_CONTROL:
            continue
        flush_bulk()
        line = _format_control_pcap_line(pkt, max_data=max_data)
        if line is not None:
            lines.append(line)

    flush_bulk()
    lines.append(
        f"(capture summary: {len(analysis.packets)} packets, "
        f"{len(analysis.bulk_ins)} bulk IN, "
        f"{len(analysis.register_writes)} register writes, "
        f"{len(analysis.buffer_preambles)} buffer preambles)"
    )
    return lines


def build_usbpcap_packet(
    *,
    transfer: int,
    endpoint: int,
    info: int,
    data: bytes,
    control_stage: int | None = None,
    status: int = 0,
    function: int = 0,
) -> bytes:
    """Build a USBPcap packet body (for tests)."""
    header_len = _USBPCAP_BASE.size + (1 if control_stage is not None else 0)
    base = _USBPCAP_BASE.pack(
        header_len,
        0,
        status,
        function,
        info,
        1,
        1,
        endpoint,
        transfer,
        len(data),
    )
    if control_stage is not None:
        return base + bytes((control_stage & 0xFF,)) + data
    return base + data


def wrap_pcap_classic(packets: list[bytes], *, linktype: int = 249) -> bytes:
    """Wrap USBPcap bodies in a classic pcap (LINKTYPE_USBPCAP=249)."""
    out = bytearray()
    out += struct.pack("<IIII", 0xA1B2C3D4, 0x00020004, 0, 0)
    out += struct.pack("<II", 65535, linktype)
    for pkt in packets:
        out += struct.pack("<IIII", 0, 0, len(pkt), len(pkt))
        out += pkt
    return bytes(out)
