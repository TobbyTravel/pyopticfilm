# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen 8100 V2 USB ENDPIXEL dummy trim — do not retarget to match new code."""

from __future__ import annotations

import numpy as np

from pyopticfilm.device.model_8100_v2 import MODEL_8100_V2
from pyopticfilm.scan.geometry import compute_geometry
from pyopticfilm.scan.pipeline import ImagePipeline, trim_to_optical_span


def test_trim_to_optical_span_drops_usb_wider_than_pixels():
    geo = compute_geometry(1800, model=MODEL_8100_V2, area=(0.2, 0.2, 0.6, 0.5))
    wide = np.zeros((8, geo.pixels + 5, 3), dtype=np.uint16)
    trimmed = trim_to_optical_span(wide, geo)
    assert trimmed.shape[1] == geo.pixels


def _assemble_passthrough(monkeypatch, geo, rgb):
    pipe = ImagePipeline(MODEL_8100_V2)
    monkeypatch.setattr(pipe, "decode_rgb", lambda *_a, **_k: rgb.copy())
    monkeypatch.setattr(pipe, "reduce_y_oversample", lambda arr, _g: arr)
    monkeypatch.setattr(pipe, "apply_line_shifts", lambda arr, _g: arr)
    monkeypatch.setattr(pipe, "apply_y_stagger", lambda arr, _g: arr)
    monkeypatch.setattr(pipe, "apply_host_downsample", lambda arr, _g: arr)
    return pipe.assemble(b"", geo, dark=None, white=None, expose_base=False)


def test_assemble_drops_fixed_usb_end_dummy(monkeypatch):
    geo = compute_geometry(1800, model=MODEL_8100_V2, area=(0.15, 0.2, 0.7, 0.6))
    assert geo.usb_end_drop == 24
    h = max(4, geo.lines)
    rgb = np.full((h, geo.pixels, 3), 18_000, dtype=np.uint16)
    rgb[:, -24:, :] = 200
    out = _assemble_passthrough(monkeypatch, geo, rgb)
    assert out.shape[1] == geo.pixels - 24
    assert int(out[:, 0, 0].mean()) > 10_000
