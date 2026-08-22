# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan buffer helpers and optional TIFF export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pyopticfilm.exceptions import PlustekError


@dataclass
class ScanImage:
    """Host-side scan result.

    ``rgb`` is HxWx3 uint16 linear data (colour or infrared-illuminated CCD
    frame). For ``mode="infrared"``, ``ir`` is also filled with an HxW uint16
    plane (green CCD channel, host-flattened on GL128) for dust/iSRD use.

    All arrays are **linear film-negative measurements** (bright sensor values
    correspond to dense emulsion). Positive conversion is the host app's job.

    Multi-exposure (8200i SE): the SNR/IVW-merged deliverable with film-base
    makeup is in ``rgb``. Bracket planes and fusion stats are **not** on
    ``ScanImage`` — use :attr:`~pyopticfilm.scanner.Scanner.last_me_debug`
    for Scan Lab / bring-up inspection.
    """

    rgb: np.ndarray
    dpi: int
    device_model: str = "PLUSTEK OpticFilm 8200i"
    ir: np.ndarray | None = None

    def save_tiff(self, path: str | Path) -> Path:
        """Write a 16-bit RGB TIFF (the scanned frame only; no ``*_IR`` sidecar)."""
        return save_rgb16_tiff(self.rgb, path, dpi=self.dpi)


def save_rgb16_tiff(rgb: np.ndarray, path: str | Path, *, dpi: int) -> Path:
    """Write HxWx3 uint16 linear RGB to a compressed TIFF with resolution tags."""
    arr = np.asarray(rgb)
    if arr.dtype != np.uint16:
        raise PlustekError(f"rgb must be uint16, got {arr.dtype}")
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise PlustekError(f"rgb must be HxWx3, got shape {arr.shape}")

    try:
        import tifffile
    except ImportError as exc:
        raise PlustekError("TIFF export requires tifffile") from exc

    out = Path(path)
    if out.suffix.lower() not in {".tif", ".tiff"}:
        out = out.with_suffix(".tif")

    tifffile.imwrite(
        out,
        arr,
        photometric="rgb",
        compression="zlib",
        predictor=True,
        resolution=(dpi, dpi),
        resolutionunit="inch",
    )
    return out


def save_gray16_tiff(gray: np.ndarray, path: str | Path, *, dpi: int) -> Path:
    """Write HxW uint16 grayscale to a compressed TIFF with resolution tags."""
    arr = np.asarray(gray)
    if arr.dtype != np.uint16:
        raise PlustekError(f"gray must be uint16, got {arr.dtype}")
    if arr.ndim != 2:
        raise PlustekError(f"gray must be HxW, got shape {arr.shape}")

    try:
        import tifffile
    except ImportError as exc:
        raise PlustekError("TIFF export requires tifffile") from exc

    out = Path(path)
    if out.suffix.lower() not in {".tif", ".tiff"}:
        out = out.with_suffix(".tif")

    tifffile.imwrite(
        out,
        arr,
        photometric="minisblack",
        compression="zlib",
        predictor=True,
        resolution=(dpi, dpi),
        resolutionunit="inch",
    )
    return out


def load_rgb16_tiff(path: str | Path, *, default_dpi: int = 1800) -> tuple[np.ndarray, int]:
    """Read a 16-bit RGB TIFF written by :func:`save_rgb16_tiff` or Scan Lab."""
    try:
        import tifffile
    except ImportError as exc:
        raise PlustekError("TIFF import requires tifffile") from exc

    src = Path(path)
    with tifffile.TiffFile(src) as tif:
        arr = np.asarray(tif.asarray())
        dpi = _tiff_dpi_from_page(tif.pages[0]) if tif.pages else None

    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise PlustekError(f"expected HxWx3 RGB TIFF, got shape {arr.shape}")
    if arr.dtype != np.uint16:
        if np.issubdtype(arr.dtype, np.floating):
            arr = np.clip(arr, 0, 65535).astype(np.uint16)
        elif arr.dtype == np.uint8:
            arr = (arr.astype(np.uint16) << 8) | arr.astype(np.uint16)
        else:
            arr = arr.astype(np.uint16)

    return np.ascontiguousarray(arr), int(dpi if dpi else default_dpi)


def _tiff_dpi_from_page(page) -> int | None:
    """Best-effort DPI from TIFF resolution tags."""
    try:
        xres = page.tags.get("XResolution")
        if xres is None:
            return None
        value = xres.value
        if isinstance(value, tuple) and len(value) == 2 and value[1]:
            x = float(value[0]) / float(value[1])
        else:
            x = float(value)
        unit = page.tags.get("ResolutionUnit")
        if unit is not None and int(unit.value) == 3:
            x *= 2.54
        if x <= 0:
            return None
        return round(x)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
