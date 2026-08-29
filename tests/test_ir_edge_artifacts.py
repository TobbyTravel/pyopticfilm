# SPDX-License-Identifier: GPL-3.0-or-later
"""IR flatten hardening, pass-align border fill, and live-path width lock."""

from __future__ import annotations

import numpy as np

from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.pass_align import _warp_shift
from pyopticfilm.scan.calib_gl128 import (
    IR_SIDECAR_TARGET_LEVEL,
    flatten_ir_image_columns,
)
from pyopticfilm.scan.geometry import compute_geometry
from pyopticfilm.scan.pipeline import ImagePipeline, trim_to_optical_span


def test_trim_to_optical_span_drops_usb_wider_than_pixels():
    """Pcap 7200 URB dummy past STR/END — not live-path heuristic trim."""
    geo = compute_geometry(1800, model=MODEL_8200I_SE, area=(0.2, 0.2, 0.6, 0.5))
    wide = np.zeros((8, geo.pixels + 5, 3), dtype=np.uint16)
    trimmed = trim_to_optical_span(wide, geo)
    assert trimmed.shape[1] == geo.pixels


def test_assemble_drops_fixed_usb_end_dummy(monkeypatch):
    """Fixed ENDPIXEL dummy suffix — not heuristic dark-column trim."""
    geo = compute_geometry(1800, model=MODEL_8200I_SE, area=(0.15, 0.2, 0.7, 0.6))
    assert geo.usb_end_drop == 24
    h = max(4, geo.lines)
    rgb = np.full((h, geo.pixels, 3), 18_000, dtype=np.uint16)
    rgb[:, -24:, :] = 200  # USB ENDPIXEL dummy (image left after mirror)
    pipe = ImagePipeline(MODEL_8200I_SE)
    monkeypatch.setattr(pipe, "decode_rgb", lambda *_a, **_k: rgb.copy())
    monkeypatch.setattr(pipe, "reduce_y_oversample", lambda arr, _g: arr)
    monkeypatch.setattr(pipe, "apply_line_shifts", lambda arr, _g: arr)
    monkeypatch.setattr(pipe, "apply_y_stagger", lambda arr, _g: arr)
    monkeypatch.setattr(pipe, "apply_host_downsample", lambda arr, _g: arr)
    out = pipe.assemble(b"", geo, dark=None, white=None, expose_base=False)
    assert out.shape[1] == geo.pixels - 24
    assert int(out[:, 0, 0].mean()) > 10_000


def test_flatten_ir_image_columns_caps_zero_edge_gain():
    h, w = 80, 200
    plane = np.full((h, w), 14_000, dtype=np.uint16)
    plane[:, 0:2] = 0  # garbage padding columns
    out = flatten_ir_image_columns(plane, smooth_half=0)
    # Must not produce a wide blown band on the left.
    left_band = out[:, : max(8, w // 10)]
    interior = out[:, w // 4 : 3 * w // 4]
    assert float(left_band.mean()) <= float(interior.mean()) * 1.25
    assert float(out.max()) <= 65535


def test_flatten_ir_image_columns_clean_plane_near_target():
    h, w = 80, 200
    plane = np.full((h, w), 14_000, dtype=np.uint16)
    # Mild falloff so flatten has work to do.
    fall = np.linspace(0.85, 1.0, w, dtype=np.float32)
    plane = np.clip(plane.astype(np.float32) * fall[None, :], 0, 65535).astype(np.uint16)
    out = flatten_ir_image_columns(plane)
    assert abs(float(np.median(out)) - IR_SIDECAR_TARGET_LEVEL) < 8_000


def test_warp_shift_does_not_widen_hot_edge_column():
    h, w = 40, 60
    plane = np.full((h, w), 10_000, dtype=np.uint16)
    plane[:, 0] = 60_000  # hot padding column
    warped = _warp_shift(plane, dx=3.0, dy=0.0)
    # Left strip must not be a copy of the 60k edge (would widen the band).
    assert float(warped[:, 0].mean()) < 20_000
    assert float(warped[:, 1].mean()) < 20_000
    assert float(warped[:, 2].mean()) < 20_000


def test_fill_shift_border_right_edge():
    plane = np.arange(20, dtype=np.uint16).reshape(1, 20)
    plane = np.broadcast_to(plane, (8, 20)).copy()
    plane[:, -1] = 65_000
    # Shift left → right border would replicate column -1 without fill.
    out = _warp_shift(plane, dx=-2.0, dy=0.0)
    assert float(out[:, -1].mean()) < 30_000
    assert float(out[:, -2].mean()) < 30_000
