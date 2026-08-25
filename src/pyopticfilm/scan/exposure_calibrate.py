# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure multi-exposure (ME) exposure calibration for GL128 scanners.

Selects the two ``REG_EXPOSURE`` values GL128 multi-exposure scanning
measures on the currently loaded film:

* ``short`` — the lowest candidate whose linear image still spans a
  meaningful density range (see :func:`pick_short`);
* ``long`` — the highest candidate whose highlights stay below the clip
  threshold (see :func:`pick_high`).

Everything here is hardware-independent: it operates on linear uint16
``HxWx3`` planes (no film-base makeup, no display scaling) and on the
per-step statistics those planes produce. Acquisition lives in
:class:`~pyopticfilm.scanner.Scanner` / the GL128 session layer.

The candidate ladders, calibration geometry, and gate thresholds come from
the model (see :class:`~pyopticfilm.device.model_8200i_se.Model8200iSE`
``me_calib_*`` / ``exposure_*_candidates`` fields) so future scanner models
can override them.

Measured on an OpticFilm 8100 V2 (GL128) at 1200 dpi over a central 20 %
crop (area ``(0.4, 0.4, 0.6, 0.6)``), 16 k→100 k steps:

======  ========  =====  ============
exp     clip >=92  p05    p90/p05
======  ========  =====  ============
7000    0.000 %   1563   2.33
14000   0.000 %   1765   3.16
...     ...       ...    ...
42000   0.000 %   5071   3.34
58000   0.036 %   6931   3.38
64000   0.489 %   7685   3.39
70000   1.788 %   8425   3.40
======  ========  =====  ============

which yields ``short=14000`` (7 k's density range is not yet independent
signal) and ``long=64000`` (last candidate at or below the 0.5 % clip
gate). See ``docs/scanner-validation.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

#: Fraction of full scale a channel must not exceed (92 % — the CCD knee
#: starts well before the 16-bit rail).
CLIP_LEVEL_FRACTION = 0.92
_FULL_SCALE = 65535.0
#: Default maximum fraction of pixels (any channel) at/above the clip level
#: for an exposure to count as clip-clean.
DEFAULT_CLIP_THRESHOLD = 0.005
#: Minimum ``p90/p05`` luma spread for a short-exposure candidate to carry
#: independent density information (~one optical density).
DEFAULT_SHORT_MIN_CONTRAST = 3.0


@dataclass(frozen=True)
class CalibStepStats:
    """Per-step statistics of one linear ME calibration scan."""

    exposure: int
    mean_dn: float
    p05: float
    p90: float
    p99: float
    clip_92_max: float
    noise_ratio: float
    usable: bool
    reason: str


@dataclass(frozen=True)
class ExposurePlan:
    """Chosen ME exposures plus the diagnostic steps that produced them.

    ``exposures`` is intentionally a sequence rather than two internal
    variables: this PR fills it with exactly the two-value
    ``(short, long)`` pair, and a future N-pass PR should extend the
    sequence instead of redesigning the merge.
    """

    short: int
    long: int
    exposures: tuple[int, ...]
    steps: tuple[CalibStepStats, ...]
    source: str
    area: tuple[float, float, float, float]
    resolution: int


def step_stats(
    exposure: int,
    linear_rgb: np.ndarray,
    *,
    clip_threshold: float = DEFAULT_CLIP_THRESHOLD,
) -> CalibStepStats:
    """Compute per-step statistics on a **linear** ``HxWx3`` uint16 plane.

    Uses raw linear data on purpose: the display / film-base-normalized
    result is not comparable across exposures (it is stretched per plane),
    so exposure decisions must be made before any makeup.
    """
    if linear_rgb.ndim != 3 or linear_rgb.shape[2] != 3:
        raise ValueError(f"expected HxWx3 linear plane, got shape {linear_rgb.shape}")
    a = linear_rgb.astype(np.float64)
    lum = a.mean(axis=2)

    p05 = float(np.percentile(lum, 5))
    p90 = float(np.percentile(lum, 90))
    p99 = float(np.percentile(lum, 99))
    clip_level = CLIP_LEVEL_FRACTION * _FULL_SCALE
    clip_92_max = max(float(np.mean(a[:, :, c] >= clip_level)) for c in range(3))

    # Local SNR of the film-base region (darkest 5 % luma): its coefficient
    # of variation. A high ratio means the base sits near the read-noise
    # floor, so little independent information rides on the dense parts.
    base = lum[lum <= p05]
    if base.size:
        base_mean = max(float(base.mean()), 1e-9)
        noise_ratio = float(base.std()) / base_mean
    else:  # pragma: no cover - p05 always defines a non-empty mask
        noise_ratio = 0.0

    usable = clip_92_max <= clip_threshold
    reason = (
        f"clip {100.0 * clip_92_max:.3f}% <= {100.0 * clip_threshold:.3f}% threshold"
        if usable
        else f"clip {100.0 * clip_92_max:.3f}% > {100.0 * clip_threshold:.3f}% threshold"
    )
    return CalibStepStats(
        exposure=int(exposure),
        mean_dn=float(a.mean()),
        p05=p05,
        p90=p90,
        p99=p99,
        clip_92_max=clip_92_max,
        noise_ratio=noise_ratio,
        usable=usable,
        reason=reason,
    )


def _stats_by_exposure(steps: Sequence[CalibStepStats]) -> dict[int, CalibStepStats]:
    return {int(s.exposure): s for s in steps}


def pick_short(
    steps: Sequence[CalibStepStats],
    *,
    candidates: Sequence[int],
    min_contrast: float = DEFAULT_SHORT_MIN_CONTRAST,
) -> tuple[int | None, list[str]]:
    """Pick the lowest short-exposure candidate that is usable.

    Usability is a contrast criterion on the linear image, not a
    ``p05 > 3*noise`` rule: on dense film the base sits far above the read
    noise even at 7 k, but the whole frame still spans less than ~one
    optical density (``p90/p05 < 3``) and the short pass carries no
    information the long pass does not. Returning the **lowest** passing
    candidate keeps the short pass maximally underexposed, which is what
    the IVW merge needs for dense/shadow regions.

    Returns ``(exposure, reasons)``; ``exposure`` is ``None`` when no
    candidate is usable (the caller falls back to the model default).
    """
    by_exp = _stats_by_exposure(steps)
    reasons: list[str] = []
    for exposure in sorted(int(e) for e in candidates):
        stats = by_exp.get(exposure)
        if stats is None:
            reasons.append(f"{exposure}: no measurement taken")
            continue
        if stats.p05 <= 0.0:
            reasons.append(f"{exposure}: p05={stats.p05:.0f} — no measurable film base")
            continue
        contrast = stats.p90 / stats.p05
        if contrast < min_contrast:
            reasons.append(
                f"{exposure}: p90/p05={contrast:.2f} < {min_contrast:.2f} "
                f"(density range not independent of base/noise)"
            )
            continue
        reasons.append(f"{exposure}: selected (p90/p05={contrast:.2f} >= {min_contrast:.2f})")
        return exposure, reasons
    return None, reasons


def pick_high(
    steps: Sequence[CalibStepStats],
    *,
    candidates: Sequence[int],
    clip_threshold: float = DEFAULT_CLIP_THRESHOLD,
) -> int:
    """Pick the highest long-exposure candidate that stays clip-clean.

    Candidates are examined in descending order; the first one whose
    measured ``clip_92_max <= clip_threshold`` wins. If every measured
    candidate clips (or was not measured), the safest minimum candidate
    that *was* measured is returned — it capture-validated that the
    exposure completes, and it clips the least.
    """
    by_exp = _stats_by_exposure(steps)
    for exposure in sorted((int(e) for e in candidates), reverse=True):
        stats = by_exp.get(exposure)
        if stats is None:
            continue
        if stats.clip_92_max <= clip_threshold:
            return exposure
    measured = [by_exp[e] for e in sorted({int(x) for x in candidates}) if e in by_exp]
    if measured:
        return min(measured, key=lambda s: s.exposure).exposure
    return min(int(e) for e in candidates)


def format_step_table(steps: Sequence[CalibStepStats]) -> str:
    """Human-readable per-step table for logs / NegPy diagnostics."""
    header = (
        f"{'exp':>7}  {'mean':>8}  {'p05':>7}  {'p90':>8}  {'p99':>8}  "
        f"{'clip92max':>10}  {'n':>5}  {'usable':>6}  reason"
    )
    lines = [header]
    for s in steps:
        lines.append(
            f"{s.exposure:>7}  {s.mean_dn:>8.0f}  {s.p05:>7.0f}  {s.p90:>8.0f}  {s.p99:>8.0f}  "
            f"{100.0 * s.clip_92_max:>9.3f}%  {s.noise_ratio:>5.2f}  "
            f"{'yes' if s.usable else 'no':>6}  {s.reason}"
        )
    return "\n".join(lines)
