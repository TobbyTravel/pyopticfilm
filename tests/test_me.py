# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-exposure merge and alignment tests."""

from __future__ import annotations

import numpy as np

from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.pass_align import align_pass_to_reference
from pyopticfilm.scan.exposure_merge import merge_exposures, merge_exposures_result


def test_model_me_exposure_constants():
    assert MODEL_8200I_SE.exposure_short == 14000
    assert MODEL_8200I_SE.exposure_long == 42000
    assert MODEL_8200I_SE.multi_exposure_factor == 3
    assert MODEL_8200I_SE.channel_exposure_for(1800, exposure=42000) == 42000 // 4


def test_model_pixel_clock_long_at_1800():
    assert MODEL_8200I_SE.pixel_clock_for_image(1800, long_exposure=False) == 0x02
    assert MODEL_8200I_SE.pixel_clock_for_image(1800, long_exposure=True) == 0x01


def test_align_pass_zero_shift_is_identity():
    arr = np.arange(64 * 64 * 3, dtype=np.uint16).reshape(64, 64, 3)
    aligned, shift = align_pass_to_reference(arr, arr, shift=(0, 0))
    assert shift == (0, 0)
    assert np.array_equal(aligned, arr)


def test_merge_linear_prefers_short_when_long_saturated():
    short = np.full((4, 4, 3), 50000, dtype=np.uint16)
    long = np.full((4, 4, 3), 65000, dtype=np.uint16)
    out = merge_exposures(short, long, method="linear", exposure_short=14000, exposure_long=42000)
    assert out.shape == short.shape
    assert np.all(out == short)


def test_merge_linear_uses_long_in_shadows():
    short = np.full((4, 4, 3), 1000, dtype=np.uint16)
    long = np.full((4, 4, 3), 9000, dtype=np.uint16)
    out = merge_exposures(short, long, method="linear", exposure_short=14000, exposure_long=42000)
    # long scaled to short: 9000 * (14000/42000) ≈ 3000 > 1000
    assert np.all(out > short)
    assert np.all(out < 5000)


def test_merge_fusion_returns_uint16():
    short = np.random.default_rng(0).integers(1000, 20000, (8, 8, 3), dtype=np.uint16)
    long = np.random.default_rng(1).integers(2000, 50000, (8, 8, 3), dtype=np.uint16)
    out = merge_exposures(short, long, method="fusion")
    assert out.dtype == np.uint16
    assert out.shape == short.shape
    assert int(out.mean()) > 100


def test_merge_fusion_prefers_short_in_highlights():
    short = np.full((4, 4, 3), 50000, dtype=np.uint16)
    long = np.full((4, 4, 3), 65000, dtype=np.uint16)
    result = merge_exposures_result(short, long, method="fusion")
    assert np.allclose(result.rgb, short)
    assert result.fusion_stats is not None
    assert result.fusion_stats.mean_short_weight == 1.0
    assert result.fusion_stats.mean_long_weight == 0.0
    assert result.fusion_stats.zero_weight_pixels == 0


def test_merge_fusion_prefers_long_in_shadows():
    short = np.full((4, 4, 3), 1000, dtype=np.uint16)
    long = np.full((4, 4, 3), 9000, dtype=np.uint16)
    result = merge_exposures_result(
        short, long, method="fusion", exposure_short=14000, exposure_long=42000
    )
    # scaled long ≈ 3000; short has partial noise weight, long full → blend > short
    assert np.all(result.rgb > short)
    assert result.fusion_stats is not None
    assert result.fusion_stats.mean_long_weight == 1.0
    assert result.fusion_stats.mean_short_weight > 0.0
    assert result.fusion_stats.zero_weight_pixels == 0


def test_merge_fusion_both_zero_stays_black():
    """Misaligned edge: short in noise and long clipped → no fill, stays ~black."""
    short = np.full((4, 4, 3), 400, dtype=np.uint16)
    long = np.full((4, 4, 3), 65000, dtype=np.uint16)
    result = merge_exposures_result(short, long, method="fusion")
    assert np.all(result.rgb == 0)
    assert result.fusion_stats is not None
    assert result.fusion_stats.zero_weight_fraction == 1.0


def test_merge_fusion_stats_typical_midtones():
    short = np.full((8, 8, 3), 8000, dtype=np.uint16)
    long = np.full((8, 8, 3), 20000, dtype=np.uint16)
    result = merge_exposures_result(short, long, method="fusion")
    assert result.fusion_stats is not None
    assert result.fusion_stats.mean_short_weight == 1.0
    assert result.fusion_stats.mean_long_weight == 1.0
    assert result.fusion_stats.zero_weight_pixels == 0
    # Equal weights → mean of short and scaled long
    scale = 14000 / 42000
    expected = (8000 + 20000 * scale) / 2
    assert np.allclose(result.rgb.astype(np.float64), expected, atol=1.0)
