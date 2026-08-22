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
_REFINE_ROI_SIDE = 2048
_REFINE_MAX_RESIDUAL = 2.0

Shift2D = tuple[float, float]

_cv2_warned = False


def opencv_align_available() -> bool:
    """Return True when sub-pixel pass registration is available."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def warn_if_align_unavailable(context: str = "multi-pass") -> bool:
    """Log once at WARNING when OpenCV is missing; return False."""
    global _cv2_warned
    if opencv_align_available():
        return True
    if not _cv2_warned:
        logger.warning(
            "pass_align: OpenCV unavailable — %s registration disabled (zero shift). "
            "Install opencv-python-headless (lab group) for ME/IR alignment.",
            context,
        )
        _cv2_warned = True
    return False


def _luminance_plane(image: np.ndarray, *, probe_w: int = _ALIGN_PROBE_WIDTH) -> np.ndarray:
    """Downsampled luma for coarse phase correlation (INTER_AREA when OpenCV present)."""
    arr = np.asarray(image)
    h, w = arr.shape[:2]
    scale = max(1.0, w / probe_w)
    if scale <= 1.0:
        if arr.ndim == 3:
            return arr.astype(np.float32).mean(axis=2)
        return arr.astype(np.float32)
    sz = (probe_w, max(1, round(h / scale)))
    try:
        import cv2
    except ImportError:
        sy = max(1, round(h / scale))
        sx = max(1, round(w / scale))
        arr = arr[::sy, ::sx]
        if arr.ndim == 3:
            return arr.astype(np.float32).mean(axis=2)
        return arr.astype(np.float32)
    if arr.ndim == 3:
        small = cv2.resize(arr, sz, interpolation=cv2.INTER_AREA)
        return small.astype(np.float32).mean(axis=2)
    return cv2.resize(arr.astype(np.float32), sz, interpolation=cv2.INTER_AREA)


def _roi_luminance(image: np.ndarray, y0: int, x0: int, rh: int, rw: int) -> np.ndarray:
    roi = np.asarray(image)[y0 : y0 + rh, x0 : x0 + rw]
    if roi.ndim == 3:
        return roi.astype(np.float32).mean(axis=2)
    return roi.astype(np.float32)


def _phase_correlate_shift(ref: np.ndarray, mov: np.ndarray, *, scale: float) -> Shift2D:
    try:
        import cv2
    except ImportError:
        return (0.0, 0.0)
    if ref.size == 0 or mov.size == 0 or ref.shape != mov.shape:
        return (0.0, 0.0)
    h, w = ref.shape[:2]
    win = cv2.createHanningWindow((w, h), cv2.CV_32F)
    (dx, dy), _resp = cv2.phaseCorrelate(
        np.ascontiguousarray(mov), np.ascontiguousarray(ref), win
    )
    return float(dx * scale), float(dy * scale)


def _refine_pass_shift(
    reference: np.ndarray,
    moving: np.ndarray,
    coarse: Shift2D,
) -> Shift2D:
    """Second phaseCorrelate on a central ROI after coarse alignment."""
    if not opencv_align_available():
        return coarse
    h, w = reference.shape[:2]
    if h * w < 512 * 512:
        return coarse
    dx0, dy0 = coarse
    if abs(dx0) < 1e-9 and abs(dy0) < 1e-9:
        warped = moving
    else:
        warped = _warp_shift(moving, dx0, dy0)
    rh = min(_REFINE_ROI_SIDE, h)
    rw = min(_REFINE_ROI_SIDE, w)
    y0 = max(0, (h - rh) // 2)
    x0 = max(0, (w - rw) // 2)
    ref_lum = _roi_luminance(reference, y0, x0, rh, rw)
    mov_lum = _roi_luminance(warped, y0, x0, rh, rw)
    ddx, ddy = _phase_correlate_shift(ref_lum, mov_lum, scale=1.0)
    if max(abs(ddx), abs(ddy)) > _REFINE_MAX_RESIDUAL:
        return coarse
    return dx0 + ddx, dy0 + ddy


def estimate_pass_shift(reference: np.ndarray, moving: np.ndarray) -> Shift2D:
    """Estimate ``(dx, dy)`` (sub-pixel when OpenCV is available) for ``moving`` → ``reference``."""
    if not opencv_align_available():
        warn_if_align_unavailable("pass")
        return (0.0, 0.0)
    ref = np.asarray(reference)
    mov = np.asarray(moving)
    if ref.shape[:2] != mov.shape[:2]:
        return (0.0, 0.0)
    full_w = ref.shape[1]
    ref_lum = _luminance_plane(ref)
    mov_lum = _luminance_plane(mov)
    scale = max(1.0, full_w / _ALIGN_PROBE_WIDTH)
    dx, dy = _phase_correlate_shift(ref_lum, mov_lum, scale=scale)
    if max(abs(dx), abs(dy)) > max(16.0, _ALIGN_MAX_SHIFT_FRAC * full_w):
        logger.warning(
            "pass_align: shift (%.2f, %.2f) exceeds guard — using unaligned",
            dx,
            dy,
        )
        return (0.0, 0.0)
    dx, dy = _refine_pass_shift(ref, mov, (dx, dy))
    if max(abs(dx), abs(dy)) > max(16.0, _ALIGN_MAX_SHIFT_FRAC * full_w):
        logger.warning(
            "pass_align: refined shift (%.2f, %.2f) exceeds guard — using unaligned",
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
