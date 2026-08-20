# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-exposure fusion in linear film-negative scanner space."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from pyopticfilm.pass_align import align_pass_to_reference

MergeMethod = Literal["linear", "fusion", "snr"]

_FULL_SCALE = 65535.0

# Heuristic fusion (legacy soft weights)
_DEFAULT_CLIP_START = 52428  # ~80% of 16-bit
_DEFAULT_CLIP_END = 62259  # ~95%
_DEFAULT_NOISE_FLOOR = 512

# Research-style SNR / IVW soft confidence (fraction of full scale)
_SNR_FLOOR = 0.002 * _FULL_SCALE
_SNR_CLIP_START = 0.92 * _FULL_SCALE
_SNR_CLIP_END = 0.98 * _FULL_SCALE
# Provisional Poisson-Gaussian DN² model (tunable; calibrate later from PTC)
_SNR_ALPHA = 1.0
_SNR_BETA = 4096.0  # ~64 DN read noise
_SNR_Z_LO = 3.0
_SNR_Z_HI = 5.0


@dataclass(frozen=True)
class FusionStats:
    """Mean short/long weights over HxW (luminance plane for fusion; mean of w for SNR)."""

    mean_short_weight: float
    mean_long_weight: float
    zero_weight_pixels: int
    total_pixels: int
    mean_residual_confidence: float | None = None
    exposure_ratio_used: float | None = None

    @property
    def zero_weight_fraction(self) -> float:
        return self.zero_weight_pixels / self.total_pixels if self.total_pixels else 0.0


@dataclass(frozen=True)
class MergeResult:
    rgb: np.ndarray
    fusion_stats: FusionStats | None = None


def merge_exposures(
    short: np.ndarray,
    long: np.ndarray,
    *,
    method: MergeMethod = "linear",
    exposure_short: int = 14000,
    exposure_long: int = 42000,
    align_shift: tuple[int, int] | None = None,
    clip_start: int = _DEFAULT_CLIP_START,
    clip_end: int = _DEFAULT_CLIP_END,
    noise_floor: int = _DEFAULT_NOISE_FLOOR,
    alpha: float = _SNR_ALPHA,
    beta: float = _SNR_BETA,
) -> np.ndarray:
    """Fuse short and long RGB negatives into one uint16 frame."""
    return merge_exposures_result(
        short,
        long,
        method=method,
        exposure_short=exposure_short,
        exposure_long=exposure_long,
        align_shift=align_shift,
        clip_start=clip_start,
        clip_end=clip_end,
        noise_floor=noise_floor,
        alpha=alpha,
        beta=beta,
    ).rgb


def merge_exposures_result(
    short: np.ndarray,
    long: np.ndarray,
    *,
    method: MergeMethod = "linear",
    exposure_short: int = 14000,
    exposure_long: int = 42000,
    align_shift: tuple[int, int] | None = None,
    clip_start: int = _DEFAULT_CLIP_START,
    clip_end: int = _DEFAULT_CLIP_END,
    noise_floor: int = _DEFAULT_NOISE_FLOOR,
    alpha: float = _SNR_ALPHA,
    beta: float = _SNR_BETA,
) -> MergeResult:
    """Like :func:`merge_exposures` but returns weight stats when applicable."""
    a = np.asarray(short, dtype=np.float64)
    b_raw = np.asarray(long, dtype=np.float64)
    if a.shape != b_raw.shape or a.ndim != 3 or a.shape[2] != 3:
        raise ValueError(f"expected matching HxWx3 arrays, got {a.shape} and {b_raw.shape}")
    if exposure_long <= 0 or exposure_short <= 0:
        raise ValueError("exposure values must be positive")

    b, _ = align_pass_to_reference(a.astype(np.uint16), b_raw.astype(np.uint16), shift=align_shift)
    b = b.astype(np.float64)
    ratio = exposure_long / float(exposure_short)
    scale = 1.0 / ratio
    bn = b * scale

    if method == "linear":
        rgb = _merge_linear(a, bn, b, clip_end=clip_end).astype(np.uint16)
        return MergeResult(rgb=rgb, fusion_stats=None)
    if method == "fusion":
        rgb, stats = _merge_fusion(
            a,
            bn,
            b,
            clip_start=clip_start,
            clip_end=clip_end,
            noise_floor=noise_floor,
        )
        return MergeResult(rgb=rgb.astype(np.uint16), fusion_stats=stats)
    if method == "snr":
        rgb, stats = _merge_snr(
            a,
            b,
            usb_ratio=ratio,
            alpha=alpha,
            beta=beta,
            floor=_SNR_FLOOR,
            clip_start=_SNR_CLIP_START,
            clip_end=_SNR_CLIP_END,
        )
        return MergeResult(rgb=rgb.astype(np.uint16), fusion_stats=stats)
    raise ValueError(f"unsupported merge method {method!r}")


def _merge_linear(
    a: np.ndarray, bn: np.ndarray, b_raw: np.ndarray, *, clip_end: float
) -> np.ndarray:
    """Prefer scaled long where the raw long pass is not clipped; else short."""
    use_long = b_raw < clip_end
    out = np.where(use_long, bn, a)
    return np.clip(out, 0, 65535)


def _weight_above_noise(value: np.ndarray, *, floor: float, full: float) -> np.ndarray:
    """0 at/below noise floor, ramp to 1 by ``full`` (short-pass SNR)."""
    span = max(1.0, full - floor)
    return np.clip((value - floor) / span, 0.0, 1.0)


def _weight_below_clip(value: np.ndarray, *, start: float, end: float) -> np.ndarray:
    """1 below clip start, ramp to 0 by ``end`` (long-pass highlight trust)."""
    span = max(1.0, end - start)
    return np.clip((end - value) / span, 0.0, 1.0)


def _merge_fusion(
    a: np.ndarray,
    bn: np.ndarray,
    b_raw: np.ndarray,
    *,
    clip_start: float,
    clip_end: float,
    noise_floor: float,
) -> tuple[np.ndarray, FusionStats]:
    # Shared luminance weights — per-channel weights cause colour fringing.
    lum_a = a.mean(axis=2)
    lum_b = b_raw.mean(axis=2)
    wa = _weight_above_noise(lum_a, floor=noise_floor, full=noise_floor * 4)
    wb = _weight_below_clip(lum_b, start=clip_start, end=clip_end)
    denom = wa + wb
    both_zero = denom <= 1e-6
    out = (wa[..., np.newaxis] * a + wb[..., np.newaxis] * bn) / np.maximum(
        denom[..., np.newaxis], 1e-6
    )
    stats = FusionStats(
        mean_short_weight=float(wa.mean()),
        mean_long_weight=float(wb.mean()),
        zero_weight_pixels=int(np.count_nonzero(both_zero)),
        total_pixels=int(both_zero.size),
    )
    return np.clip(out, 0, 65535), stats


def _smoothstep01(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _smooth_confidence(
    raw: np.ndarray, *, floor: float, clip_start: float, clip_end: float
) -> np.ndarray:
    """Noise-floor ramp × soft saturation ramp on raw (unnormalized) DN."""
    floor_w = np.clip((raw - floor) / max(floor, 1e-12), 0.0, 1.0)
    t = (raw - clip_start) / max(clip_end - clip_start, 1e-12)
    clip_w = 1.0 - _smoothstep01(t)
    return floor_w * clip_w


def _residual_confidence(z: np.ndarray, *, z_lo: float = _SNR_Z_LO, z_hi: float = _SNR_Z_HI) -> np.ndarray:
    """1 for |z|<=z_lo, smooth decay to 0 by z_hi."""
    az = np.abs(z)
    conf = np.ones_like(az, dtype=np.float64)
    mid = (az > z_lo) & (az < z_hi)
    conf[az >= z_hi] = 0.0
    conf[mid] = (z_hi - az[mid]) / max(z_hi - z_lo, 1e-12)
    return conf


def _estimate_exposure_ratio(
    a: np.ndarray,
    b_raw: np.ndarray,
    *,
    usb_ratio: float,
    full_scale: float = _FULL_SCALE,
) -> float:
    """Robust median long/short ratio on mid-tone pixels; fall back to USB ratio."""
    lo = 0.01 * full_scale
    hi = 0.85 * full_scale
    valid = (a > lo) & (a < hi) & (b_raw > lo) & (b_raw < hi)
    if int(np.count_nonzero(valid)) >= 1000:
        ratios = b_raw[valid] / np.maximum(a[valid], 1e-12)
        return float(np.median(ratios))
    la = a.mean(axis=2)
    lb = b_raw.mean(axis=2)
    valid2 = (la > lo) & (la < hi) & (lb > lo) & (lb < hi)
    if int(np.count_nonzero(valid2)) < 100:
        return float(usb_ratio)
    ratios = lb[valid2] / np.maximum(la[valid2], 1e-12)
    return float(np.median(ratios))


def _merge_snr(
    a: np.ndarray,
    b_raw: np.ndarray,
    *,
    usb_ratio: float,
    alpha: float,
    beta: float,
    floor: float,
    clip_start: float,
    clip_end: float,
) -> tuple[np.ndarray, FusionStats]:
    """Clipping-aware inverse-variance fusion in short-exposure radiometric scale.

    Fits an effective exposure ratio from mid-tone pixels (USB ratio is only a
    fallback), then combines with soft clip/floor confidence and IVW weights.
    Residual rejection is local (global median removed) so a slight scale error
    cannot collapse the merge onto the short pass.
    """
    r = max(_estimate_exposure_ratio(a, b_raw, usb_ratio=usb_ratio), 1e-12)
    xa = a
    xb = b_raw / r

    lum_a_raw = a.mean(axis=2)
    lum_b_raw = b_raw.mean(axis=2)
    ca = _smooth_confidence(lum_a_raw, floor=floor, clip_start=clip_start, clip_end=clip_end)
    cb = _smooth_confidence(lum_b_raw, floor=floor, clip_start=clip_start, clip_end=clip_end)

    va = alpha * np.maximum(xa, 0.0) + beta
    vb = (alpha * np.maximum(b_raw, 0.0) + beta) / (r * r)

    lum_xa = xa.mean(axis=2)
    lum_xb = xb.mean(axis=2)
    va_lum = alpha * np.maximum(lum_xa, 0.0) + beta
    vb_lum = (alpha * np.maximum(lum_b_raw, 0.0) + beta) / (r * r)
    z = (lum_xa - lum_xb) / np.sqrt(np.maximum(va_lum + vb_lum, 1e-12))
    z_local = z - float(np.median(z))
    c_res = _residual_confidence(z_local)
    gate = np.minimum(ca, cb)
    c_res_eff = 1.0 - gate * (1.0 - c_res)

    wa = ca[..., np.newaxis] / np.maximum(va, 1e-12)
    wb = cb[..., np.newaxis] / np.maximum(vb, 1e-12)
    denom = wa + wb
    both_zero = (ca + cb) <= 1e-6
    ivw = (wa * xa + wb * xb) / np.maximum(denom, 1e-12)

    # On local disagreement, keep the higher IVW-confidence pass (not short-on-tie).
    w_a_lum = ca / np.maximum(va_lum, 1e-12)
    w_b_lum = cb / np.maximum(vb_lum, 1e-12)
    prefer = np.where(w_a_lum[..., np.newaxis] >= w_b_lum[..., np.newaxis], xa, xb)
    out = c_res_eff[..., np.newaxis] * ivw + (1.0 - c_res_eff[..., np.newaxis]) * prefer
    out = np.where(both_zero[..., np.newaxis], 0.0, out)

    stats = FusionStats(
        mean_short_weight=float(wa.mean()),
        mean_long_weight=float(wb.mean()),
        zero_weight_pixels=int(np.count_nonzero(both_zero)),
        total_pixels=int(both_zero.size),
        mean_residual_confidence=float(c_res_eff.mean()),
        exposure_ratio_used=float(r),
    )
    return np.clip(out, 0, 65535), stats
