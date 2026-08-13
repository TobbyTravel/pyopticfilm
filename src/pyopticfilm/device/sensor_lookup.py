# SPDX-License-Identifier: GPL-3.0-or-later
"""DPI / method keyed sensor lookups (SANE ``tables_sensor.cpp`` shape).

Models may expose optional maps:

- ``lperiod_by_dpi``: ``{(method, dpi): lperiod}`` or ``{dpi: lperiod}``
- ``dummy_pixel_by_dpi``: ``{dpi: dummy}``
- ``sensor_regs_by_dpi``: ``{dpi: {reg: value}}``
- ``frontend_regs_by_dpi``: ``{(method, dpi): {fe_reg: value}}`` or ``{dpi: …}``
- ``dummy_pixel``: default dummy when no per-dpi map

Fallbacks use the flat ``exposure_lperiod`` / ``sensor_custom_regs`` /
``frontend_regs`` fields already on every ``FilmModel``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pyopticfilm.device.protocol import ScanMethod


def _lookup_method_dpi(
    table: Mapping[Any, Any] | None,
    *,
    method: str,
    resolution: int,
) -> Any | None:
    if not table:
        return None
    for key in ((method, resolution), resolution, (method,)):
        if key in table:
            return table[key]
    return None


def exposure_lperiod_for(
    model: Any,
    resolution: int,
    *,
    method: ScanMethod | str = "transparency",
) -> int:
    """Return ``LPERIOD`` exposure for ``resolution`` / ``method``."""
    hit = _lookup_method_dpi(
        getattr(model, "lperiod_by_dpi", None),
        method=str(method),
        resolution=int(resolution),
    )
    if hit is not None:
        return int(hit)
    return int(getattr(model, "exposure_lperiod", 14000))


def dummy_pixel_for(model: Any, resolution: int) -> int:
    """Return shading/scan DUMMY register value."""
    by_dpi = getattr(model, "dummy_pixel_by_dpi", None)
    if isinstance(by_dpi, Mapping) and int(resolution) in by_dpi:
        return int(by_dpi[int(resolution)])
    return int(getattr(model, "dummy_pixel", 20))


def sensor_regs_for(model: Any, resolution: int) -> Mapping[int, int]:
    """Return sensor custom register overlay for ``resolution``."""
    by_dpi = getattr(model, "sensor_regs_by_dpi", None)
    if isinstance(by_dpi, Mapping) and int(resolution) in by_dpi:
        return dict(by_dpi[int(resolution)])
    return dict(getattr(model, "sensor_custom_regs", {}) or {})


def frontend_regs_for(
    model: Any,
    resolution: int,
    *,
    method: ScanMethod | str = "transparency",
) -> Mapping[int, int]:
    """Return AFE frontend register map (base + optional dpi overlay)."""
    base = dict(getattr(model, "frontend_regs", {}) or {})
    hit = _lookup_method_dpi(
        getattr(model, "frontend_regs_by_dpi", None),
        method=str(method),
        resolution=int(resolution),
    )
    if hit is not None:
        base.update(dict(hit))
    return base


def maxwd_register_value(model: Any, *, line_bytes: int, channels: int = 3) -> int:
    """Compute ASIC ``MAXWD`` from SANE per-chip quirks.

    - GL845/GL846: ``(line_bytes * channels) >> 2`` (4-word units; genesys bug preserved)
    - GL843: ``line_bytes >> 1`` for OpticFilm CCD (optical≈full → SANE ``>> 1``)
    - GL842 CCD: ``line_bytes``
    """
    asic = str(getattr(model, "asic", "GL845"))
    lb = int(line_bytes)
    ch = int(channels)
    override = getattr(model, "maxwd_for_line_bytes", None)
    if callable(override):
        return int(override(lb, channels=ch))
    if asic == "GL843":
        return lb >> 1
    if asic == "GL842":
        return lb
    # GL845 / GL846 / default
    return (lb * ch) >> 2
