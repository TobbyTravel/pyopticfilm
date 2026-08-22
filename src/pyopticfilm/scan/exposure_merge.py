# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-exposure SNR / IVW merge in linear film-negative scanner space."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyopticfilm.pass_align import align_pass_to_reference

_FULL_SCALE = 65535.0

# Soft confidence (fraction of full scale). Clip starts earlier than a hard
# 16-bit rail so CCD knee / pre-sat nonlinearity does not bleed into IVW.
_SNR_FLOOR = 0.002 * _FULL_SCALE
_SNR_CLIP_START = 0.80 * _FULL_SCALE
_SNR_CLIP_END = 0.95 * _FULL_SCALE
# Provisional Poisson-Gaussian DN² model (override via alpha/beta kwargs or
# :func:`estimate_pg_noise_params` from flat fields).
_SNR_ALPHA = 1.0
_SNR_BETA = 4096.0  # ~64 DN read noise
_SNR_Z_LO = 3.0
_SNR_Z_HI = 5.0


@dataclass(frozen=True)
class FusionStats:
    """Mean short/long IVW weights and related diagnostics."""

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


def estimate_pg_noise_params(
    flats: list[np.ndarray],
    *,
    patch: int = 32,
) -> tuple[float, float]:
    """Estimate Poisson–Gaussian ``α``, ``β`` from flat (or near-flat) frames.

    For each frame, tiles of ``patch×patch`` yield (mean, variance) pairs; a
    robust line fit gives ``var ≈ α·mean + β``. Returns ``(_SNR_ALPHA, _SNR_BETA)``
    if too few samples.
    """
    means: list[float] = []
    vars_: list[float] = []
    for flat in flats:
        arr = np.asarray(flat, dtype=np.float64)
        if arr.ndim == 3:
            arr = arr.mean(axis=2)
        h, w = arr.shape[:2]
        if h < patch or w < patch:
            continue
        for y in range(0, h - patch + 1, patch):
            for x in range(0, w - patch + 1, patch):
                tile = arr[y : y + patch, x : x + patch]
                means.append(float(tile.mean()))
                vars_.append(float(tile.var()))
    if len(means) < 8:
        return _SNR_ALPHA, _SNR_BETA
    m = np.asarray(means, dtype=np.float64)
    v = np.asarray(vars_, dtype=np.float64)
    # Drop empty / saturated tiles.
    ok = (m > 50.0) & (m < 0.9 * _FULL_SCALE) & np.isfinite(v)
    if int(np.count_nonzero(ok)) < 8:
        return _SNR_ALPHA, _SNR_BETA
    m, v = m[ok], v[ok]
    # Least squares: [mean, 1] @ [α, β] = var
    a = np.column_stack([m, np.ones_like(m)])
    coef, _, _, _ = np.linalg.lstsq(a, v, rcond=None)
    alpha = float(max(coef[0], 1e-6))
    beta = float(max(coef[1], 1.0))
    return alpha, beta


def merge_exposures(
    short: np.ndarray,
    long: np.ndarray,
    *,
    exposure_short: int = 14000,
    exposure_long: int = 42000,
    align_shift: tuple[float, float] | tuple[int, int] | None = None,
    alpha: float = _SNR_ALPHA,
    beta: float = _SNR_BETA,
) -> np.ndarray:
    """Fuse short and long RGB negatives into one uint16 frame (SNR / IVW)."""
    return merge_exposures_result(
        short,
        long,
        exposure_short=exposure_short,
        exposure_long=exposure_long,
        align_shift=align_shift,
        alpha=alpha,
        beta=beta,
    ).rgb


def merge_exposures_result(
    short: np.ndarray,
    long: np.ndarray,
    *,
    exposure_short: int = 14000,
    exposure_long: int = 42000,
    align_shift: tuple[float, float] | tuple[int, int] | None = None,
    alpha: float = _SNR_ALPHA,
    beta: float = _SNR_BETA,
) -> MergeResult:
    """Like :func:`merge_exposures` but returns weight stats."""
    a = np.asarray(short, dtype=np.float32)
    b_raw = np.asarray(long, dtype=np.float32)
    if a.shape != b_raw.shape or a.ndim != 3 or a.shape[2] != 3:
        raise ValueError(f"expected matching HxWx3 arrays, got {a.shape} and {b_raw.shape}")
    if exposure_long <= 0 or exposure_short <= 0:
        raise ValueError("exposure values must be positive")

    b, _ = align_pass_to_reference(a.astype(np.uint16), b_raw.astype(np.uint16), shift=align_shift)
    b = b.astype(np.float32)
    ratio = exposure_long / float(exposure_short)

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


def _smoothstep01(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _smooth_confidence(
    raw: np.ndarray, *, floor: float, clip_start: float, clip_end: float
) -> np.ndarray:
    """Noise-floor ramp × soft saturation ramp on raw (unnormalized) DN.

    ``raw`` may be HxW or HxWxC — confidence is computed element-wise.
    """
    floor_w = np.clip((raw - floor) / max(floor, 1e-12), 0.0, 1.0)
    t = (raw - clip_start) / max(clip_end - clip_start, 1e-12)
    clip_w = 1.0 - _smoothstep01(t)
    return floor_w * clip_w


def _residual_confidence(z: np.ndarray, *, z_lo: float = _SNR_Z_LO, z_hi: float = _SNR_Z_HI) -> np.ndarray:
    """1 for |z|<=z_lo, smooth decay to 0 by z_hi."""
    az = np.abs(z)
    conf = np.ones_like(az, dtype=np.float32)
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

    Per-channel soft confidence avoids luma-only highlight mistakes (one channel
    clipped while mean RGB still looks safe). Residual gating stays on luminance.
    """
    r = max(_estimate_exposure_ratio(a, b_raw, usb_ratio=usb_ratio), 1e-12)
    xa = a
    xb = b_raw / r

    ca = _smooth_confidence(a, floor=floor, clip_start=clip_start, clip_end=clip_end)
    cb = _smooth_confidence(b_raw, floor=floor, clip_start=clip_start, clip_end=clip_end)

    va = alpha * np.maximum(xa, 0.0) + beta
    vb = (alpha * np.maximum(b_raw, 0.0) + beta) / (r * r)

    lum_xa = xa.mean(axis=2)
    lum_xb = xb.mean(axis=2)
    lum_b_raw = b_raw.mean(axis=2)
    va_lum = alpha * np.maximum(lum_xa, 0.0) + beta
    vb_lum = (alpha * np.maximum(lum_b_raw, 0.0) + beta) / (r * r)
    z = (lum_xa - lum_xb) / np.sqrt(np.maximum(va_lum + vb_lum, 1e-12))
    z_local = z - float(np.median(z))
    c_res = _residual_confidence(z_local)
    gate = np.minimum(ca, cb).mean(axis=2)
    c_res_eff = 1.0 - gate * (1.0 - c_res)

    wa = ca / np.maximum(va, 1e-12)
    wb = cb / np.maximum(vb, 1e-12)
    denom = wa + wb
    both_zero = (ca + cb) <= 1e-6
    ivw = (wa * xa + wb * xb) / np.maximum(denom, 1e-12)

    prefer = np.where(wa >= wb, xa, xb)
    out = c_res_eff[..., np.newaxis] * ivw + (1.0 - c_res_eff[..., np.newaxis]) * prefer
    out = np.where(both_zero, 0.0, out)

    both_zero_pix = np.all(both_zero, axis=2)
    stats = FusionStats(
        mean_short_weight=float(wa.mean()),
        mean_long_weight=float(wb.mean()),
        zero_weight_pixels=int(np.count_nonzero(both_zero_pix)),
        total_pixels=int(both_zero_pix.size),
        mean_residual_confidence=float(c_res_eff.mean()),
        exposure_ratio_used=float(r),
    )
    return np.clip(out, 0, 65535), stats
