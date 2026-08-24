# SPDX-License-Identifier: GPL-3.0-or-later
"""Pass registration for multi-pass Plustek USB scans.

The carriage re-homes (AGOHOME) between colour, IR, and ME passes, so secondary
frames can land a few pixels off the reference. Registration realigns them.

The estimator here is deliberately robust to the two things that break a naive
approach on film scanner passes:

* **Exposure difference** — the short ME pass is underexposed and the long one
  well-exposed, so the two frames differ strongly in brightness. Registering
  raw luminance confuses the shift search (it sees "different brightness"
  instead of "the same scene, shifted").
* **Low texture** — a plain crop leaves little for a phase-correlation to lock
  onto, which can latch onto a spurious multi-pixel shift.

We therefore register on a high-frequency (edge/grain) map (image minus a heavy
blur, contrast-normalised) with OpenCV ECC registration
(:func:`cv2.findTransformECC`, ``MOTION_TRANSLATION``). ECC minimises an
illumination-robust similarity and its internal Gaussian pyramid supplies the
coarse-to-fine sub-pixel refinement. A physical-magnitude guard rejects any
corrected shift beyond the real re-home drift; when OpenCV is unavailable we
return zero shift (no warp) rather than error.

See https://github.com/jboneng/pyopticfilm/issues/14.
"""

from __future__ import annotations

import numpy as np

from pyopticfilm.logging import get_logger

logger = get_logger(__name__)

# Physical bound on the feed-axis (AGOHOME re-home) drift we are willing to
# correct, in full-resolution pixels. Real re-home drift is a few pixels at
# most; anything larger on a low-texture crop is a spurious spike.
_ROBUST_MAX_SHIFT_PX = 8.0
# Gaussian blur kernel for the high-frequency map.
_HF_BLUR = 15
# High-frequency contrast: normalise so short/long exposures share a scale.
_HF_PERCENTILE = 95

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


def _luma(rgb: np.ndarray) -> np.ndarray:
    arr = np.asarray(rgb)
    if arr.ndim == 3 and arr.shape[2] in (3, 4):
        return arr[..., :3].astype(np.float32).mean(axis=2)
    return arr.astype(np.float32) if arr.ndim == 2 else arr[..., 0].astype(np.float32)


def _highfreq(rgb: np.ndarray) -> np.ndarray:
    """Exposure-invariant edge/grain map: image minus a heavy blur, normalised.

    The short (underexposed) and long (well-exposed) passes look like the same
    edges here regardless of absolute brightness, so registration locks onto
    structure instead of being confused by the exposure ratio.
    """
    import cv2

    lum = _luma(rgb)
    k = _HF_BLUR if _HF_BLUR % 2 == 1 else _HF_BLUR + 1
    hf = lum - cv2.GaussianBlur(lum, (k, k), 0)
    p = np.percentile(np.abs(hf), _HF_PERCENTILE) + 1e-6
    return np.clip(hf / p, -1.0, 1.0).astype(np.float32)


def estimate_pass_shift(reference, moving):
    """Estimate ``(dx, dy)`` aligning ``moving`` → ``reference`` (full-res px).

    Registers on the exposure-invariant high-frequency map via OpenCV ECC with
    an internal coarse-to-fine pyramid, then applies the physical-magnitude
    guard. Returns ``(0, 0)`` (no warp) when OpenCV is unavailable, the inputs
    mismatch, ECC fails, or the corrected shift exceeds ``_ROBUST_MAX_SHIFT_PX``
    — a bogus estimate can never move the image, only leave it unaligned.
    """
    try:
        import cv2
    except ImportError:
        warn_if_align_unavailable("pass")
        return (0.0, 0.0)

    ref = np.asarray(reference)
    mov = np.asarray(moving)
    if ref.shape[:2] != mov.shape[:2] or ref.size == 0:
        return (0.0, 0.0)

    ref_map = _highfreq(ref)
    mov_map = _highfreq(mov)

    warp = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    try:
        # findTransformECC(template, input, ...): returns the warp that maps
        # `input` onto `template`. We want moving -> reference, so template=ref,
        # input=mov.
        _ok, warp = cv2.findTransformECC(
            np.ascontiguousarray(ref_map),
            np.ascontiguousarray(mov_map),
            warp,
            cv2.MOTION_TRANSLATION,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-4),
        )
    except cv2.error:
        return (0.0, 0.0)

    dx = float(warp[0, 2])
    dy = float(warp[1, 2])
    if max(abs(dx), abs(dy)) > _ROBUST_MAX_SHIFT_PX:
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
    # OpenCV: +x is right, +y is down — same convention as our index math.
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
    reference,
    moving,
    shift: Shift2D | None = None,
):
    """Align ``moving`` onto ``reference``; return ``(aligned, shift)``."""
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