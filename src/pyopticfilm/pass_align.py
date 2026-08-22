# SPDX-License-Identifier: GPL-3.0-or-later
"""Pass registration for multi-pass Plustek USB scans.

The carriage re-homes (AGOHOME) between colour, IR, and ME passes, so secondary
frames can land a few pixels off the reference. Uses phase correlation when
OpenCV is available (sub-pixel shift + warp); otherwise returns zero shift.
"""

from __future__ import annotations

import numpy as np

from pyopticfilm.logging import get_logger

logger = get_logger(__name__)

_ALIGN_PROBE_WIDTH = 1024
_ALIGN_MAX_SHIFT_FRAC = 0.02

Shift2D = tuple[float, float]


def _luminance_plane(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 3:
        return arr.mean(axis=2)
    return arr


def estimate_pass_shift(reference: np.ndarray, moving: np.ndarray) -> Shift2D:
    """Estimate ``(dx, dy)`` (sub-pixel when OpenCV is available) for ``moving`` → ``reference``."""
    ref = _luminance_plane(reference)
    mov = _luminance_plane(moving)
    if ref.size == 0 or mov.size == 0 or ref.shape != mov.shape:
        return (0.0, 0.0)
    h, w = ref.shape[:2]
    scale = max(1.0, w / _ALIGN_PROBE_WIDTH)
    try:
        import cv2
    except ImportError:
        logger.debug("pass_align: OpenCV unavailable — skipping shift estimate")
        return (0.0, 0.0)
    if scale > 1.0:
        sz = (_ALIGN_PROBE_WIDTH, max(1, round(h / scale)))
        r = cv2.resize(ref, sz, interpolation=cv2.INTER_AREA)
        m = cv2.resize(mov, sz, interpolation=cv2.INTER_AREA)
    else:
        r, m = ref, mov
    win = cv2.createHanningWindow((r.shape[1], r.shape[0]), cv2.CV_32F)
    (dx, dy), _resp = cv2.phaseCorrelate(
        np.ascontiguousarray(r), np.ascontiguousarray(m), win
    )
    dx, dy = float(dx * scale), float(dy * scale)
    if max(abs(dx), abs(dy)) > max(16.0, _ALIGN_MAX_SHIFT_FRAC * w):
        logger.warning(
            "pass_align: shift (%.2f, %.2f) exceeds guard — using unaligned",
            dx,
            dy,
        )
        return (0.0, 0.0)
    return (dx, dy)


def _warp_shift(mov: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Translate ``mov`` by ``(dx, dy)`` with sub-pixel resampling when possible."""
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return mov
    h, w = mov.shape[:2]
    # Integer path: cheap index gather (exact, no OpenCV).
    if abs(dx - round(dx)) < 1e-6 and abs(dy - round(dy)) < 1e-6:
        idx = round(dx)
        idy = round(dy)
        x_idx = np.clip(np.arange(w) + idx, 0, w - 1)
        y_idx = np.clip(np.arange(h) + idy, 0, h - 1)
        if mov.ndim == 3:
            return mov[y_idx][:, x_idx, :]
        return mov[y_idx][:, x_idx]
    try:
        import cv2
    except ImportError:
        idx = round(dx)
        idy = round(dy)
        x_idx = np.clip(np.arange(w) + idx, 0, w - 1)
        y_idx = np.clip(np.arange(h) + idy, 0, h - 1)
        if mov.ndim == 3:
            return mov[y_idx][:, x_idx, :]
        return mov[y_idx][:, x_idx]
    # OpenCV: +x is right, +y is down — same as phaseCorrelate / our index convention.
    matrix = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
    if mov.ndim == 3:
        planes = [
            cv2.warpAffine(
                mov[:, :, c],
                matrix,
                (w, h),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
            for c in range(mov.shape[2])
        ]
        out = np.stack(planes, axis=2)
    else:
        out = cv2.warpAffine(
            mov,
            matrix,
            (w, h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
    if np.issubdtype(mov.dtype, np.integer):
        return np.clip(np.rint(out), 0, np.iinfo(mov.dtype).max).astype(mov.dtype)
    return out.astype(mov.dtype, copy=False)


def align_pass_to_reference(
    reference: np.ndarray,
    moving: np.ndarray,
    shift: tuple[float, float] | tuple[int, int] | None = None,
) -> tuple[np.ndarray, Shift2D]:
    """Align ``moving`` onto ``reference``; return aligned array and shift used."""
    ref = np.asarray(reference)
    mov = np.asarray(moving)
    if mov.size == 0 or ref.shape[:2] != mov.shape[:2]:
        return mov, (0.0, 0.0)
    if shift is None:
        shift = estimate_pass_shift(ref, mov)
    dx, dy = float(shift[0]), float(shift[1])
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return mov, (0.0, 0.0)
    return _warp_shift(mov, dx, dy), (dx, dy)


def align_ir_to_rgb(rgb: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Backward-compatible IR→RGB alignment."""
    aligned, _ = align_pass_to_reference(rgb, ir)
    return aligned
