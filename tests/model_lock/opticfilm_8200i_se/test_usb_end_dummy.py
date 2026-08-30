# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen 8200i SE USB ENDPIXEL dummy trim — do not retarget to match new code."""

from __future__ import annotations

import numpy as np

from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.scan.geometry import compute_geometry
from pyopticfilm.scan.pipeline import (
    ImagePipeline,
    count_usb_end_dummy_columns,
    trim_to_optical_span,
)


def test_trim_to_optical_span_drops_usb_wider_than_pixels():
    """Pcap 7200 URB dummy past STR/END — not live-path heuristic trim."""
    geo = compute_geometry(1800, model=MODEL_8200I_SE, area=(0.2, 0.2, 0.6, 0.5))
    wide = np.zeros((8, geo.pixels + 5, 3), dtype=np.uint16)
    trimmed = trim_to_optical_span(wide, geo)
    assert trimmed.shape[1] == geo.pixels


def _assemble_passthrough(monkeypatch, geo, rgb):
    pipe = ImagePipeline(MODEL_8200I_SE)
    monkeypatch.setattr(pipe, "decode_rgb", lambda *_a, **_k: rgb.copy())
    monkeypatch.setattr(pipe, "reduce_y_oversample", lambda arr, _g: arr)
    monkeypatch.setattr(pipe, "apply_line_shifts", lambda arr, _g: arr)
    monkeypatch.setattr(pipe, "apply_y_stagger", lambda arr, _g: arr)
    monkeypatch.setattr(pipe, "apply_host_downsample", lambda arr, _g: arr)
    return pipe.assemble(b"", geo, dark=None, white=None, expose_base=False)


def test_count_usb_end_dummy_drops_true_dummy_not_cap():
    rgb = np.full((8, 80, 3), 18_000, dtype=np.uint16)
    rgb[:, -8:, :] = 200
    assert count_usb_end_dummy_columns(rgb, max_drop=24) == 8


def test_count_usb_end_dummy_zero_when_no_dummy():
    rgb = np.full((8, 80, 3), 18_000, dtype=np.uint16)
    assert count_usb_end_dummy_columns(rgb, max_drop=24) == 0


def test_assemble_drops_fixed_usb_end_dummy(monkeypatch):
    """Full dummy suffix still drops usb_end_drop when every END column is dummy."""
    geo = compute_geometry(1800, model=MODEL_8200I_SE, area=(0.15, 0.2, 0.7, 0.6))
    assert geo.usb_end_drop == 24
    h = max(4, geo.lines)
    rgb = np.full((h, geo.pixels, 3), 18_000, dtype=np.uint16)
    rgb[:, -24:, :] = 200  # USB ENDPIXEL dummy (image left after mirror)
    out = _assemble_passthrough(monkeypatch, geo, rgb)
    assert out.shape[1] == geo.pixels - 24
    assert int(out[:, 0, 0].mean()) > 10_000


def test_assemble_drops_partial_usb_end_dummy(monkeypatch):
    """8 true dummy columns drop 8, not the 24-column cap."""
    geo = compute_geometry(1800, model=MODEL_8200I_SE, area=(0.15, 0.2, 0.7, 0.6))
    assert geo.usb_end_drop == 24
    h = max(4, geo.lines)
    rgb = np.full((h, geo.pixels, 3), 18_000, dtype=np.uint16)
    rgb[:, -8:, :] = 200
    out = _assemble_passthrough(monkeypatch, geo, rgb)
    assert out.shape[1] == geo.pixels - 8
    assert int(out[:, 0, 0].mean()) > 10_000


def test_assemble_keeps_left_frame_when_no_dummy(monkeypatch):
    geo = compute_geometry(1800, model=MODEL_8200I_SE, area=(0.15, 0.2, 0.7, 0.6))
    assert geo.usb_end_drop == 24
    h = max(4, geo.lines)
    rgb = np.full((h, geo.pixels, 3), 18_000, dtype=np.uint16)
    out = _assemble_passthrough(monkeypatch, geo, rgb)
    assert out.shape[1] == geo.pixels
    assert int(out[:, 0, 0].mean()) > 10_000
