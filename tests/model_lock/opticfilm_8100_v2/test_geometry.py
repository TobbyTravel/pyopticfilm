# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen 8100 V2 STR/END window oracles — do not retarget to match new code."""

from __future__ import annotations

from pyopticfilm.device.model_8100_v2 import MODEL_8100_V2
from pyopticfilm.scan.bringup import bringup_scan_geometry, crop_scan_geometry

NATIVE = 7200
PREVIEW_STR = 242
FRAME_SHIFT = MODEL_8100_V2.optical_end_inactive_native


def test_endpixel_dummy_scales_as_native_clocks():
    n = MODEL_8100_V2.optical_end_inactive_native
    assert n == 96
    assert n * 1200 // NATIVE == 16
    assert n * 1800 // NATIVE == 24
    assert n * 3600 // NATIVE == 48


def test_full_window_str_is_dpi_independent():
    starts = []
    for dpi in (1200, 1800, 3600, 7200):
        g, _ = bringup_scan_geometry(MODEL_8100_V2, dpi, profile="preview_safe")
        starts.append(g.pixel_startx)
        assert g.pixels % 2 == 0
        assert g.pixel_endx - g.pixel_startx == g.optical_pixels
        assert g.line_bytes == g.pixels * 3 * (g.depth // 8)
        assert g.usb_end_drop == 96 * dpi // NATIVE
    assert len(set(starts)) == 1
    assert starts[0] == PREVIEW_STR + FRAME_SHIFT


def test_same_crop_str_matches_at_1800_and_3600():
    area = (0.15, 0.1, 0.72, 0.8)
    g1800, _ = crop_scan_geometry(MODEL_8100_V2, 1800, area)
    g3600, _ = crop_scan_geometry(MODEL_8100_V2, 3600, area)
    assert g1800.pixel_startx == g3600.pixel_startx
    assert abs(g1800.pixel_endx - g3600.pixel_endx) < 8
    assert g3600.usb_end_drop == g1800.usb_end_drop * 2


def test_calib_geometry_does_not_drop_usb_end():
    from pyopticfilm.scan.geometry import compute_calib_geometry

    g = compute_calib_geometry(1800, model=MODEL_8100_V2)
    assert g.disable_buffer_full_move
    assert g.usb_end_drop == 0
    assert g.pixel_startx == 120
