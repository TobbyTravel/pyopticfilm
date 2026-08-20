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


def test_merge_snr_prefers_short_when_long_clipped():
    short = np.full((4, 4, 3), 50000, dtype=np.uint16)
    long = np.full((4, 4, 3), 65000, dtype=np.uint16)
    result = merge_exposures_result(
        short, long, method="snr", exposure_short=14000, exposure_long=42000
    )
    assert np.allclose(result.rgb, short, atol=1)
    assert result.fusion_stats is not None
    assert result.fusion_stats.mean_long_weight < result.fusion_stats.mean_short_weight
    assert result.fusion_stats.zero_weight_pixels == 0
    assert result.fusion_stats.mean_residual_confidence is not None


def test_merge_snr_midtones_favor_long():
    """After normalization, long has lower variance → higher IVW weight."""
    short = np.full((8, 8, 3), 8000, dtype=np.uint16)
    # Underlying X≈8000 on short scale → long raw ≈ 24000 at 3×
    long = np.full((8, 8, 3), 24000, dtype=np.uint16)
    result = merge_exposures_result(
        short, long, method="snr", exposure_short=14000, exposure_long=42000
    )
    assert result.fusion_stats is not None
    assert result.fusion_stats.mean_long_weight > result.fusion_stats.mean_short_weight
    assert result.fusion_stats.zero_weight_pixels == 0
    # Fused near the common radiometric level
    assert abs(float(result.rgb.mean()) - 8000.0) < 50.0


def test_merge_snr_both_zero_stays_black():
    short = np.full((4, 4, 3), 50, dtype=np.uint16)
    long = np.full((4, 4, 3), 65000, dtype=np.uint16)
    result = merge_exposures_result(short, long, method="snr")
    assert np.all(result.rgb == 0)
    assert result.fusion_stats is not None
    assert result.fusion_stats.zero_weight_fraction == 1.0


def test_merge_snr_scale_mismatch_does_not_black_out():
    """USB ratio ≠ effective gain: image-fit ratio; residual must not zero the frame."""
    short = np.full((32, 32, 3), 8000, dtype=np.uint16)
    # True 3× would be 24000; 30000 is a systematic scale error → fitted r=3.75.
    long = np.full((32, 32, 3), 30000, dtype=np.uint16)
    result = merge_exposures_result(
        short, long, method="snr", exposure_short=14000, exposure_long=42000
    )
    assert result.fusion_stats is not None
    assert result.fusion_stats.zero_weight_fraction == 0.0
    assert result.fusion_stats.exposure_ratio_used is not None
    assert abs(result.fusion_stats.exposure_ratio_used - 3.75) < 0.05
    assert abs(float(result.rgb.mean()) - 8000.0) < 50.0
    assert result.fusion_stats.mean_long_weight > result.fusion_stats.mean_short_weight


def test_merge_snr_uses_long_in_dense_film():
    """Dense (dark) short + brighter long → image-fit ratio, long dominates IVW."""
    short = np.full((32, 32, 3), 1500, dtype=np.uint16)
    long = np.full((32, 32, 3), 9000, dtype=np.uint16)  # effective r≈6
    result = merge_exposures_result(
        short, long, method="snr", exposure_short=14000, exposure_long=42000
    )
    assert result.fusion_stats is not None
    assert result.fusion_stats.exposure_ratio_used is not None
    assert abs(result.fusion_stats.exposure_ratio_used - 6.0) < 0.1
    assert result.fusion_stats.mean_long_weight > result.fusion_stats.mean_short_weight
    assert abs(float(result.rgb.mean()) - 1500.0) < 80.0


def test_merge_snr_differs_from_short_when_long_adds_signal():
    """Half-frame: short clipped-low in one region that long recovers."""
    short = np.full((32, 32, 3), 8000, dtype=np.uint16)
    long = np.full((32, 32, 3), 24000, dtype=np.uint16)
    # Dense corner: short near floor, long has recoverable signal at 3×.
    short[:16, :16, :] = 400
    long[:16, :16, :] = 6000  # → ~2000 on short scale after r=3
    result = merge_exposures_result(
        short, long, method="snr", exposure_short=14000, exposure_long=42000, align_shift=(0, 0)
    )
    # Dense corner should be brighter than short's 400 (long contributes).
    assert float(result.rgb[:16, :16].mean()) > 800.0
    assert not np.allclose(result.rgb, short, atol=50)


def test_merge_snr_reduces_noise_vs_short_only():
    """Synthetic PG noise: fused frame closer to truth than noisy short alone."""
    rng = np.random.default_rng(42)
    truth = np.full((64, 64, 3), 6000.0)
    r = 3.0
    short = np.clip(truth + rng.normal(0, 80, truth.shape), 0, 65535).astype(np.uint16)
    long_raw = np.clip(truth * r + rng.normal(0, 80, truth.shape), 0, 65535).astype(np.uint16)
    fused = merge_exposures(
        short,
        long_raw,
        method="snr",
        exposure_short=14000,
        exposure_long=42000,
        align_shift=(0, 0),
    )
    err_short = float(np.mean((short.astype(np.float64) - truth) ** 2))
    err_fused = float(np.mean((fused.astype(np.float64) - truth) ** 2))
    assert err_fused < err_short * 0.85
