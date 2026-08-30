# SPDX-License-Identifier: GPL-3.0-or-later
"""GL128 STR/END native-space window vs SilverFast session 03/04 oracles."""

from __future__ import annotations

from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.scan.bringup import bringup_scan_geometry, crop_scan_geometry
from pyopticfilm.scan.geometry import compute_geometry

MM_PER_INCH = 25.4
NATIVE = 7200

# SilverFast USB windows (include the per-line ENDPIXEL dummy suffix).
# Programmed image STR/END are these plus optical_end_inactive_native so
# dummy-trim + mirror_x does not leave the frame STR-heavy.
SESSION_03_STR = 242
SESSION_03_END = 10610
SESSION_03_PX_1200 = 1728
SESSION_04_STR = 578
SESSION_04_END = 10490
SESSION_04_PX_1800 = 2478
FRAME_SHIFT = MODEL_8200I_SE.optical_end_inactive_native


def test_endpixel_dummy_scales_as_native_clocks():
    """Dummy+transition is 96 native clocks → 16 / 24 / 48 output px."""
    n = MODEL_8200I_SE.optical_end_inactive_native
    assert n == 96
    assert n * 1200 // NATIVE == 16
    assert n * 1800 // NATIVE == 24
    assert n * 3600 // NATIVE == 48


def test_session_03_str_end_match_sf_usb_window():
    """Session 03 width 1728 px; STR/END shifted by dummy clocks for display framing."""
    g = compute_geometry(1200, model=MODEL_8200I_SE, area=(0.0, 0.0, 1.0, 0.5))
    assert g.pixel_startx == SESSION_03_STR + FRAME_SHIFT
    assert g.pixel_endx == SESSION_03_END + FRAME_SHIFT
    assert g.pixels == SESSION_03_PX_1200
    assert g.usb_end_drop == 16
    assert g.pixel_endx - g.pixel_startx == g.optical_pixels
    assert g.line_bytes == g.pixels * 3 * 2


def test_session_04_str_end_match_cropped_usb_window():
    """Session 04 STR 578 / 2478 px crop; USB span matches SF, drop 24 at 1800."""
    origin = 120
    tl_mm = (SESSION_04_STR - origin) * MM_PER_INCH / NATIVE
    x1 = (tl_mm - MODEL_8200I_SE.x_offset_ta_mm) / MODEL_8200I_SE.x_size_ta_mm
    width_mm = (SESSION_04_END - SESSION_04_STR) * MM_PER_INCH / NATIVE
    x2 = x1 + width_mm / MODEL_8200I_SE.x_size_ta_mm
    g = compute_geometry(1800, model=MODEL_8200I_SE, area=(x1, 0.1, x2, 0.5))
    assert g.pixel_startx == SESSION_04_STR + FRAME_SHIFT
    assert g.pixel_endx == SESSION_04_END + FRAME_SHIFT
    assert g.pixels == SESSION_04_PX_1800
    assert g.usb_end_drop == 24
    assert g.pixels % 2 == 0
    assert g.optical_pixels % 4 == 0
    assert g.line_bytes == g.pixels * 3 * 2


def test_same_crop_str_matches_at_1800_and_3600():
    """Same crop: STR is DPI-independent; even-width snap may move END by <8 native."""
    area = (0.15, 0.1, 0.72, 0.8)
    g1800, _ = crop_scan_geometry(MODEL_8200I_SE, 1800, area)
    g3600, _ = crop_scan_geometry(MODEL_8200I_SE, 3600, area)
    assert g1800.pixel_startx == g3600.pixel_startx
    assert abs(g1800.pixel_endx - g3600.pixel_endx) < 8
    assert g3600.usb_end_drop == g1800.usb_end_drop * 2


def test_full_window_str_is_dpi_independent():
    starts = []
    for dpi in (1200, 1800, 3600, 7200):
        g, _ = bringup_scan_geometry(MODEL_8200I_SE, dpi, profile="preview_safe")
        starts.append(g.pixel_startx)
        assert g.pixels % 2 == 0
        assert g.pixel_endx - g.pixel_startx == g.optical_pixels
        assert g.line_bytes == g.pixels * 3 * (g.depth // 8)
        assert g.usb_end_drop == 96 * dpi // NATIVE
    assert len(set(starts)) == 1
    assert starts[0] == SESSION_03_STR + FRAME_SHIFT


def test_calib_geometry_does_not_drop_usb_end():
    from pyopticfilm.scan.geometry import compute_calib_geometry

    g = compute_calib_geometry(1800, model=MODEL_8200I_SE)
    assert g.disable_buffer_full_move
    assert g.usb_end_drop == 0
    assert g.pixel_startx == 120  # origin only; no dummy framing shift
