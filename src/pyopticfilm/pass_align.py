# SPDX-License-Identifier: GPL-3.0-or-later
"""Whole-pixel registration for multi-pass Plustek USB scans.

The carriage re-homes (AGOHOME) between colour, IR, and ME passes, so secondary
frames can land a few pixels off the reference. Uses phase correlation when
OpenCV is available; otherwise returns zero shift.
"""

from __future__ import annotations

import numpy as np

from pyopticfilm.logging import get_logger

logger = get_logger(__name__)

_ALIGN_PROBE_WIDTH = 1024
_ALIGN_MAX_SHIFT_FRAC = 0.02


def _luminance_plane(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 3:
        return arr.mean(axis=2)
    return arr


def estimate_pass_shift(reference: np.ndarray, moving: np.ndarray) -> tuple[int, int]:
    """Estimate whole-pixel ``(dx, dy)`` to align ``moving`` onto ``reference``."""
    ref = _luminance_plane(reference)
    mov = _luminance_plane(moving)
    if ref.size == 0 or mov.size == 0 or ref.shape != mov.shape:
        return (0, 0)
    h, w = ref.shape[:2]
    scale = max(1.0, w / _ALIGN_PROBE_WIDTH)
    if scale > 1.0:
        sz = (_ALIGN_PROBE_WIDTH, max(1, round(h / scale)))
        try:
            import cv2
        except ImportError:
            logger.debug("pass_align: OpenCV unavailable — skipping shift estimate")
            return (0, 0)
        r = cv2.resize(ref, sz, interpolation=cv2.INTER_AREA)
        m = cv2.resize(mov, sz, interpolation=cv2.INTER_AREA)
    else:
        r, m = ref, mov
        try:
            import cv2
        except ImportError:
            logger.debug("pass_align: OpenCV unavailable — skipping shift estimate")
            return (0, 0)
    win = cv2.createHanningWindow((r.shape[1], r.shape[0]), cv2.CV_32F)
    (dx, dy), _resp = cv2.phaseCorrelate(
        np.ascontiguousarray(r), np.ascontiguousarray(m), win
    )
    dx, dy = dx * scale, dy * scale
    if max(abs(dx), abs(dy)) > max(16.0, _ALIGN_MAX_SHIFT_FRAC * w):
        logger.warning(
            "pass_align: shift (%.1f, %.1f) exceeds guard — using unaligned",
            dx,
            dy,
        )
        return (0, 0)
    return (round(dx), round(dy))


def align_pass_to_reference(
    reference: np.ndarray,
    moving: np.ndarray,
    shift: tuple[int, int] | None = None,
) -> tuple[np.ndarray, tuple[int, int]]:
    """Align ``moving`` onto ``reference``; return aligned array and shift used."""
    ref = np.asarray(reference)
    mov = np.asarray(moving)
    if mov.size == 0 or ref.shape[:2] != mov.shape[:2]:
        return mov, (0, 0)
    if shift is None:
        shift = estimate_pass_shift(ref, mov)
    dx, dy = shift
    if dx == 0 and dy == 0:
        return mov, (0, 0)
    h, w = ref.shape[:2]
    x_idx = np.clip(np.arange(w) + dx, 0, w - 1)
    y_idx = np.clip(np.arange(h) + dy, 0, h - 1)
    if mov.ndim == 3:
        return mov[y_idx][:, x_idx, :], (dx, dy)
    return mov[y_idx][:, x_idx], (dx, dy)


def align_ir_to_rgb(rgb: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Backward-compatible IR→RGB alignment."""
    aligned, _ = align_pass_to_reference(rgb, ir)
    return aligned
