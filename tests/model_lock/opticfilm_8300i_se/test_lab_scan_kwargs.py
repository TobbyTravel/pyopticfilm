# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen 8300i SE Scan Lab scan-window kwargs — do not retarget to match new code."""

from __future__ import annotations

from pyopticfilm.device.model_8300i_se import MODEL_8300I_SE
from tools.scanlab.backend import lab_scan_kwargs


def test_lab_prescan_8300i_1200_stays_in_window():
    kwargs = lab_scan_kwargs(MODEL_8300I_SE, dpi=1200, kind="prescan", crop_norm=None)
    geometry = kwargs["geometry"]
    feed2 = MODEL_8300I_SE.feed_to_scan_steps_for_area(geometry.area)
    assert feed2 == MODEL_8300I_SE.feed_to_scan_top_steps == 13128
    assert geometry.lincnt_register <= MODEL_8300I_SE.max_lincnt_for(feed2, 1200)


def test_lab_scan_8300i_full_frame_uses_window_top_feed2():
    kwargs = lab_scan_kwargs(MODEL_8300I_SE, dpi=1800, kind="scan", crop_norm=None)
    geometry = kwargs["geometry"]
    feed2 = MODEL_8300I_SE.feed_to_scan_steps_for_area(geometry.area)
    assert feed2 == 13128
    assert geometry.lincnt_register <= MODEL_8300I_SE.max_lincnt_for(feed2, 1800)


def test_lab_scan_8300i_inset_crop_is_not_full_window():
    full = lab_scan_kwargs(MODEL_8300I_SE, dpi=1800, kind="scan", crop_norm=None)
    cropped = lab_scan_kwargs(
        MODEL_8300I_SE,
        dpi=1800,
        kind="scan",
        crop_norm=(0.1, 0.1, 0.75, 0.85),
    )
    full_geo = full["geometry"]
    crop_geo = cropped["geometry"]
    assert crop_geo.pixels < full_geo.pixels
    assert crop_geo.pixel_startx != full_geo.pixel_startx
    assert crop_geo.lincnt_register < full_geo.lincnt_register
