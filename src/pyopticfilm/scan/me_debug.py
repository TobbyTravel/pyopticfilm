# SPDX-License-Identifier: GPL-3.0-or-later
"""Lab-only multi-exposure bracket diagnostics (not part of ScanImage)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pyopticfilm.scan.exposure_merge import FusionStats


@dataclass(frozen=True)
class MeScanDebug:
    """Bracket planes and IVW stats from a GL128 ME scan.

    Exposed via :attr:`~pyopticfilm.scanner.Scanner.last_me_debug` for Scan Lab
    and audit tooling. Integrators should use :class:`~pyopticfilm.image.ScanImage`
    ``rgb`` only.
    """

    rgb_short: np.ndarray
    rgb_long: np.ndarray
    exposure_short: int
    exposure_long: int
    fusion_stats: FusionStats | None = None
    align_shift_long: tuple[float, float] | None = None
    align_shift_ir: tuple[float, float] | None = None


@dataclass(frozen=True)
class NPassDebug:
    """Diagnostics for a GL128 multi-pass (N≥3) colour scan.

    Set on :attr:`~pyopticfilm.scanner.Scanner.last_me_debug` when the scan
    used more than the classic 2-pass ME bracket. ``planes`` are the raw
    per-pass colour frames (uint16) in acquisition order (short passes first,
    then long); ``exposures`` is the matching list. Fused output is what
    :class:`~pyopticfilm.image.ScanImage` ``rgb`` already holds, so this
    structure is lab / audit only and not part of the NegPy-facing ``rgb``
    contract.

    Field parity with :class:`MeScanDebug`: ``rgb_short`` / ``rgb_long`` and
    ``exposure_short`` / ``exposure_long`` point to the first pass of each
    class so existing consumers that only read those two fields keep working.
    """

    planes: tuple[np.ndarray, ...]
    exposures: tuple[int, ...]
    align_shifts: tuple[tuple[float, float], ...] | None = None  # per pass (ref-relative), None if align disabled
    align_shift_ir: tuple[float, float] | None = None
    mean_gate: float | None = None
    per_pass_mean_weight: tuple[float, ...] | None = None
    zero_weight_fraction: float | None = None

    @property
    def rgb_short(self) -> np.ndarray:
        return self.planes[0]

    @property
    def rgb_long(self) -> np.ndarray:
        return self.planes[-1]

    @property
    def exposure_short(self) -> int:
        return int(self.exposures[0])

    @property
    def exposure_long(self) -> int:
        return int(self.exposures[-1])
