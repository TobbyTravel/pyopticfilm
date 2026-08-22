# SPDX-License-Identifier: GPL-3.0-or-later
"""Prescan crop coordinate helpers."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.scan.bringup import (
    PRESCAN_DPI,
    clamp_area,
    default_frame_crop_norm,
    image_crop_to_scan_area,
    scan_area_to_image_crop,
)
from pyopticfilm.scan.geometry import ScanGeometry
from pyopticfilm.scan.pipeline import ImagePipeline


def test_prescan_dpi_is_1200() -> None:
    assert PRESCAN_DPI == 1200


def test_image_crop_passes_through_when_pixels_corrected_in_assemble() -> None:
    """Crop widget coords map 1:1 to TA area once mirror_x is applied in assemble()."""
    crop = (0.1, 0.2, 0.4, 0.8)
    assert image_crop_to_scan_area(MODEL_8200I_SE, crop) == clamp_area(crop)
    back = scan_area_to_image_crop(MODEL_8200I_SE, crop)
    assert back == pytest.approx(clamp_area(crop))


def test_image_crop_no_flip_without_mirror() -> None:
    model = SimpleNamespace(mirror_x=False)
    crop = (0.1, 0.2, 0.4, 0.8)
    assert image_crop_to_scan_area(model, crop) == clamp_area(crop)


def test_default_frame_crop_is_in_unit_square() -> None:
    area = default_frame_crop_norm(MODEL_8200I_SE)
    x1, y1, x2, y2 = area
    assert 0.0 <= x1 < x2 <= 1.0
    assert 0.0 <= y1 < y2 <= 1.0


def _geometry(*, pixels: int = 8, lines: int = 4) -> ScanGeometry:
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
        shift_r=0,
        shift_g=0,
        shift_b=0,
        optical_line_count=lines,
        line_bytes=pixels * 3 * 2,
        stagger_y=(),
        num_staggered_lines=0,
        channels=3,
        depth=16,
    )


def test_assemble_flips_x_when_mirror_x() -> None:
    """Asymmetric columns verify mirror_x is applied once in ImagePipeline.assemble()."""
    geo = _geometry(pixels=6, lines=2)
    w = geo.pixels
    rgb = np.zeros((geo.lines, w, 3), dtype=np.uint16)
    rgb[:, 0, 0] = 0x1111
    rgb[:, w - 1, 0] = 0xEEEE
    pipe = ImagePipeline(MODEL_8200I_SE)
    pipe.decode_rgb = lambda *_a, **_k: rgb.copy()  # type: ignore[method-assign]
    pipe.expose_film_base = lambda arr, **_kw: arr  # type: ignore[method-assign]
    pipe.clamp_border_highlights = lambda arr, **_kw: arr  # type: ignore[method-assign]
    out = pipe.assemble(b"", geo, dark=None, white=None, planar=False)
    assert out[0, 0, 0] == 0xEEEE
    assert out[0, w - 1, 0] == 0x1111
