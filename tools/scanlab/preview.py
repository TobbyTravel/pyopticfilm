# SPDX-License-Identifier: GPL-3.0-or-later
"""Numpy-only Scan Lab preview helpers (no Qt / no display libs)."""

from __future__ import annotations

import numpy as np

#: Longest edge of the QPixmap we keep. 3600/7200 captures are 5k–10k px wide;
#: a full-res RGB32 pixmap on Windows often fails or scales into noise.
MAX_DISPLAY_EDGE = 4096


def downsample_for_display(
    arr: np.ndarray, *, max_edge: int = MAX_DISPLAY_EDGE
) -> np.ndarray:
    """Integer-stride subsample so the preview fits a Qt-safe pixmap size."""
    if arr.ndim < 2:
        return arr
    height, width = arr.shape[:2]
    longest = max(height, width)
    if longest <= max_edge:
        return arr
    step = int(np.ceil(longest / max_edge))
    return np.ascontiguousarray(arr[::step, ::step])


def auto_level_u8(arr: np.ndarray) -> np.ndarray:
    """1–99% stretch on a subsampled copy (full-res float64 OOMs at 7200)."""
    u8 = np.empty(arr.shape[:2] + (arr.shape[2],), dtype=np.uint8)
    probe = arr[::8, ::8] if min(arr.shape[:2]) >= 32 else arr
    for c in range(arr.shape[2]):
        lo, hi = np.percentile(probe[:, :, c].astype(np.float32), (1.0, 99.0))
        plane = arr[:, :, c].astype(np.float32)
        if hi <= lo:
            u8[:, :, c] = 0
        else:
            scaled = (plane - lo) * (255.0 / (hi - lo))
            u8[:, :, c] = np.clip(scaled, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(u8)
