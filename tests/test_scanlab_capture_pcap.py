# SPDX-License-Identifier: GPL-3.0-or-later
"""USBPcap capture parse + Lab decode helpers (no display)."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from pyopticfilm.device.model_7400 import MODEL_8100
from pyopticfilm.usb.protocol import (
    REQUEST_BUFFER,
    REQUEST_TYPE_OUT,
    VALUE_SET_REGISTER,
)
from tools.scanlab.capture_pcap import (
    USBPCAP_CONTROL_STAGE_SETUP,
    USBPCAP_INFO_PDO_TO_FDO,
    USBPCAP_TRANSFER_BULK,
    USBPCAP_TRANSFER_CONTROL,
    analyze_usbpcap,
    build_usbpcap_packet,
    decode_capture_bulk,
    format_capture_usb_log_lines,
    motor_register_diff,
    optical_snapshot,
    wrap_pcap_classic,
)


def _reg_write_packets(pairs: list[tuple[int, int]]) -> list[bytes]:
    """USBPcap SETUP with embedded OUT data (real Windows capture layout)."""
    payload = b"".join(bytes((a & 0xFF, v & 0xFF)) for a, v in pairs)
    setup = struct.pack(
        "<BBHHH",
        REQUEST_TYPE_OUT,
        REQUEST_BUFFER,
        VALUE_SET_REGISTER,
        0,
        len(payload),
    )
    return [
        build_usbpcap_packet(
            transfer=USBPCAP_TRANSFER_CONTROL,
            endpoint=0x00,
            info=0,
            data=setup + payload,
            control_stage=USBPCAP_CONTROL_STAGE_SETUP,
        ),
    ]


def _u16_bytes(addr: int, value: int) -> list[tuple[int, int]]:
    return [(addr, (value >> 8) & 0xFF), (addr + 1, value & 0xFF)]


def _u24_bytes(addr: int, value: int) -> list[tuple[int, int]]:
    return [
        (addr, (value >> 16) & 0xFF),
        (addr + 1, (value >> 8) & 0xFF),
        (addr + 2, value & 0xFF),
    ]


def test_analyze_usbpcap_registers_and_bulk(tmp_path: Path):
    # Optical window: DPISET=200 (1200 dpi on 8100), STR=0 END=16, LINCNT=2, FEEDL=100
    pairs: list[tuple[int, int]] = []
    pairs += _u16_bytes(0x2C, 200)
    pairs += _u16_bytes(0x30, 0)
    pairs += _u16_bytes(0x32, 16)
    pairs += _u24_bytes(0x25, 2)
    pairs += _u24_bytes(0x3D, 100)
    pairs.append((0x04, 0x60))  # 16-bit

    pixels, lines, channels = 16, 2, 3
    # Chunky RGB16: each pixel R,G,B little-endian
    row = []
    for x in range(pixels):
        row.extend([x & 0xFF, 0x00, 0x10, 0x00, 0x20, 0x00])  # R=x, G=0x10, B=0x20
    bulk = bytes(row) * lines
    # Split into USB-sized chunks with small status INs between (real captures
    # use 512-byte status; fixtures stay below the image chunk size).
    chunk = 64
    chunks = [bulk[i : i + chunk] for i in range(0, len(bulk), chunk)]

    packets = _reg_write_packets(pairs)
    for c in chunks:
        packets.append(
            build_usbpcap_packet(
                transfer=USBPCAP_TRANSFER_BULK,
                endpoint=0x81,
                info=USBPCAP_INFO_PDO_TO_FDO,
                data=c,
            )
        )
        packets.append(
            build_usbpcap_packet(
                transfer=USBPCAP_TRANSFER_BULK,
                endpoint=0x81,
                info=USBPCAP_INFO_PDO_TO_FDO,
                data=b"\x00" * 16,
            )
        )
    pcap = tmp_path / "8100_sample.pcap"
    pcap.write_bytes(wrap_pcap_classic(packets))

    analysis = analyze_usbpcap(pcap)
    assert analysis.image_bulk == bulk
    assert len(analysis.bulk_ins) == len(chunks) + len(chunks)  # image + status
    snap = optical_snapshot(analysis.registers, asic="GL845")
    assert snap["dpiset"] == 200
    assert snap["strpixel"] == 0
    assert snap["endpixel"] == 16
    assert snap["lincnt"] == 2
    assert snap["feedl"] == 100

    rgb, geo = decode_capture_bulk(MODEL_8100, analysis, dpi=1200, planar=False)
    assert rgb.shape == (lines, pixels, channels)
    assert int(rgb[0, 5, 0]) == 5  # R
    assert int(rgb[0, 5, 1]) == 0x10
    assert int(rgb[0, 5, 2]) == 0x20
    assert geo.pixels == 16

    # Planar layout of the same bytes looks different
    rgb_p, _ = decode_capture_bulk(MODEL_8100, analysis, dpi=1200, planar=True)
    assert rgb_p.shape == rgb.shape
    assert not np.array_equal(rgb_p, rgb)

    diff = "\n".join(motor_register_diff(MODEL_8100, analysis, dpi=1200))
    assert "Capture DPISET=200" in diff
    assert "Capture LINCNT=2" in diff
    assert "Capture FEEDL=100" in diff

    log = "\n".join(format_capture_usb_log_lines(analysis))
    assert "======== CAPTURE" in log
    assert "control_write" in log
    assert "bulk_read" in log
    assert "register writes" in log
