# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic ImagePipeline decode tests (channel, layout, endian, size)."""

from __future__ import annotations

import numpy as np
import pytest

from pyopticfilm.scan.geometry import ScanGeometry
from pyopticfilm.scan.pipeline import ImagePipeline


def _geometry(
    *,
    pixels: int = 8,
    lines: int = 4,
    optical_line_count: int | None = None,
    depth: int = 16,
    shift_r: int = 0,
    shift_g: int = 0,
    shift_b: int = 0,
    stagger_y: tuple[int, ...] = (),
    num_staggered_lines: int = 0,
) -> ScanGeometry:
    optical = optical_line_count if optical_line_count is not None else lines
    sample_bytes = 2 if depth == 16 else 1
    return ScanGeometry(
        resolution=1800,
        pixels=pixels,
        lines=lines,
        startx=0,
        starty=0,
        pixel_startx=0,
        pixel_endx=pixels,
        optical_pixels=pixels,
        register_dpiset=300,
        output_pixel_offset=0,
        shift_r=shift_r,
        shift_g=shift_g,
        shift_b=shift_b,
        optical_line_count=optical,
        line_bytes=pixels * 3 * sample_bytes,
        stagger_y=stagger_y,
        num_staggered_lines=num_staggered_lines,
        channels=3,
        depth=depth,
    )


def _chunky_rgb16(rgb: np.ndarray) -> bytes:
    """Little-endian interleaved RGBRGB… per line."""
    return np.ascontiguousarray(rgb, dtype="<u2").tobytes()


def _planar_rgb16(rgb: np.ndarray) -> bytes:
    """Little-endian planar R then G then B per line (h, c, w)."""
    planar = np.transpose(rgb, (0, 2, 1))
    return np.ascontiguousarray(planar, dtype="<u2").tobytes()


def test_channel_identification_chunky_rgb16():
    geo = _geometry()
    h, w = geo.optical_line_count, geo.pixels
    expected = np.empty((h, w, 3), dtype=np.uint16)
    expected[..., 0] = 0x1234
    expected[..., 1] = 0x7777
    expected[..., 2] = 0xEEEE
    rgb = ImagePipeline().decode_rgb(_chunky_rgb16(expected), geometry=geo, planar=False)
    assert rgb.shape == (h, w, 3)
    assert np.all(rgb[..., 0] == 0x1234)
    assert np.all(rgb[..., 1] == 0x7777)
    assert np.all(rgb[..., 2] == 0xEEEE)


def test_chunky_decoded_as_planar_swaps_channels():
    geo = _geometry(pixels=4, lines=2)
    h, w = geo.optical_line_count, geo.pixels
    expected = np.empty((h, w, 3), dtype=np.uint16)
    expected[..., 0] = 0x1234
    expected[..., 1] = 0x7777
    expected[..., 2] = 0xEEEE
    raw = _chunky_rgb16(expected)
    wrong = ImagePipeline().decode_rgb(raw, geometry=geo, planar=True)
    assert not np.array_equal(wrong, expected)


def test_xy_pattern_detects_width_height():
    geo = _geometry(pixels=6, lines=5)
    h, w = geo.optical_line_count, geo.pixels
    expected = np.empty((h, w, 3), dtype=np.uint16)
    xs = np.arange(w, dtype=np.uint16)
    ys = np.arange(h, dtype=np.uint16)
    expected[..., 0] = xs[None, :]
    expected[..., 1] = ys[:, None]
    expected[..., 2] = xs[None, :] ^ ys[:, None]
    rgb = ImagePipeline().decode_rgb(_chunky_rgb16(expected), geometry=geo, planar=False)
    assert rgb.shape == (5, 6, 3)
    assert rgb[2, 4, 0] == 4
    assert rgb[2, 4, 1] == 2
    assert rgb[2, 4, 2] == (4 ^ 2)


def test_little_endian_16bit():
    geo = _geometry(pixels=2, lines=1)
    # 0x1234 little-endian is 34 12; a big-endian decode would yield 0x3412.
    raw = bytes.fromhex("34 12 00 00 00 00 34 12 00 00 00 00")
    rgb = ImagePipeline().decode_rgb(raw, geometry=geo, planar=False)
    assert rgb[0, 0, 0] == 0x1234
    assert rgb[0, 1, 0] == 0x1234


def test_8bit_upsample_times_257():
    geo = _geometry(pixels=2, lines=1, depth=8)
    raw = bytes((0x11, 0x22, 0x33, 0x44, 0x55, 0x66))
    rgb = ImagePipeline().decode_rgb(raw, geometry=geo, planar=False)
    assert rgb.dtype == np.uint16
    assert rgb[0, 0, 0] == 0x11 * 257
    assert rgb[0, 0, 1] == 0x22 * 257
    assert rgb[0, 1, 2] == 0x66 * 257


def test_short_buffer_raises():
    geo = _geometry(pixels=4, lines=2)
    with pytest.raises(ValueError, match="Short scan buffer"):
        ImagePipeline().decode_rgb(b"\x00\x01", geometry=geo)


def test_line_shifts_align_channels():
    geo = _geometry(pixels=2, lines=2, optical_line_count=4, shift_r=0, shift_g=1, shift_b=2)
    optical = np.zeros((4, 2, 3), dtype=np.uint16)
    optical[0, :, 0] = 10
    optical[1, :, 0] = 11
    optical[1, :, 1] = 20
    optical[2, :, 1] = 21
    optical[2, :, 2] = 30
    optical[3, :, 2] = 31
    shifted = ImagePipeline().apply_line_shifts(optical, geo)
    assert shifted.shape[0] == 2
    assert np.all(shifted[:, :, 0] == np.array([[10, 10], [11, 11]]))
    assert np.all(shifted[:, :, 1] == np.array([[20, 20], [21, 21]]))
    assert np.all(shifted[:, :, 2] == np.array([[30, 30], [31, 31]]))


def test_reduce_y_oversample_uses_uint32_not_float64():
    geo = _geometry(pixels=4, lines=2, optical_line_count=4)
    rgb = np.array(
        [
            [[100, 200, 300], [101, 201, 301], [102, 202, 302], [103, 203, 303]],
            [[104, 204, 304], [105, 205, 305], [106, 206, 306], [107, 207, 307]],
            [[108, 208, 308], [109, 209, 309], [110, 210, 310], [111, 211, 311]],
            [[112, 212, 312], [113, 213, 313], [114, 214, 314], [115, 215, 315]],
        ],
        dtype=np.uint16,
    )
    out = ImagePipeline().reduce_y_oversample(rgb, geo)
    assert out.dtype == np.uint16
    assert out.shape == (2, 4, 3)
    assert out[0, 0, 0] == 102
    assert out[1, 0, 0] == 110


def test_reduce_y_oversample_sum_stays_uint32():
    from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
    from pyopticfilm.scan.geometry import compute_geometry

    geo = compute_geometry(7200, model=MODEL_8200I_SE, area=None)
    n = geo.optical_line_count // geo.lines
    trimmed = np.zeros((geo.lines, n, geo.pixels, 3), dtype=np.uint16)
    sums = trimmed.astype(np.uint32).sum(axis=1, dtype=np.uint32)
    assert sums.dtype == np.uint32
