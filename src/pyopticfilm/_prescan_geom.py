# SPDX-License-Identifier: GPL-3.0-or-later
"""Prescan crop geometry helpers (mirrored X for OpticFilm SE)."""

from __future__ import annotations

ScanArea = tuple[float, float, float, float]


def clamp_scan_area(area: ScanArea) -> ScanArea:
    """Clamp a normalized TA rect to ``0..1`` with a tiny positive size."""
    x1, y1, x2, y2 = (float(v) for v in area)
    x1 = max(0.0, min(1.0, x1))
    y1 = max(0.0, min(1.0, y1))
    x2 = max(0.0, min(1.0, x2))
    y2 = max(0.0, min(1.0, y2))
    if x2 <= x1:
        x2 = min(1.0, x1 + 1e-3)
    if y2 <= y1:
        y2 = min(1.0, y1 + 1e-3)
    return (x1, y1, x2, y2)


def crop_to_scan_window(crop: ScanArea, *, mirror_x: bool) -> ScanArea:
    """Map Prescan widget coords ↔ TA ``area`` (self-inverse when ``mirror_x`` is fixed)."""
    area = clamp_scan_area(crop)
    if mirror_x:
        x1, y1, x2, y2 = area
        return (1.0 - x2, y1, 1.0 - x1, y2)
    return area
