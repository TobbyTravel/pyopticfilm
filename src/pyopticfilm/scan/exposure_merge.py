# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-exposure fusion in linear film-negative scanner space."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from pyopticfilm.pass_align import align_pass_to_reference

MergeMethod = Literal["linear", "fusion"]


@dataclass(frozen=True)
class FusionStats:
    """Mean short/long reliability weights (0..1) over HxW luminance pixels."""

    mean_short_weight: float
    mean_long_weight: float
    zero_weight_pixels: int
    total_pixels: int

    @property
    def zero_weight_fraction(self) -> float:
        return self.zero_weight_pixels / self.total_pixels if self.total_pixels else 0.0


@dataclass(frozen=True)
class MergeResult:
    rgb: np.ndarray
    fusion_stats: FusionStats | None = None


_DEFAULT_CLIP_START = 52428  # ~80% of 16-bit
_DEFAULT_CLIP_END = 62259  # ~95%
_DEFAULT_NOISE_FLOOR = 512


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
) -> MergeResult:
    """Like :func:`merge_exposures` but returns fusion weight stats when applicable."""
    a = np.asarray(short, dtype=np.float64)
    b_raw = np.asarray(long, dtype=np.float64)
    if a.shape != b_raw.shape or a.ndim != 3 or a.shape[2] != 3:
        raise ValueError(f"expected matching HxWx3 arrays, got {a.shape} and {b_raw.shape}")
    if exposure_long <= 0 or exposure_short <= 0:
        raise ValueError("exposure values must be positive")

    b, _ = align_pass_to_reference(a.astype(np.uint16), b_raw.astype(np.uint16), shift=align_shift)
    b = b.astype(np.float64)
    scale = exposure_short / float(exposure_long)
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
    # Short: trust where there is signal above the noise floor.
    wa = _weight_above_noise(lum_a, floor=noise_floor, full=noise_floor * 4)
    # Long: trust where the raw long pass is not approaching clip.
    wb = _weight_below_clip(lum_b, start=clip_start, end=clip_end)
    denom = wa + wb
    both_zero = denom <= 1e-6
    # No fallback fill: zero-weight pixels stay near-black so merge failures are visible.
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
