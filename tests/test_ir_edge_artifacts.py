# SPDX-License-Identifier: GPL-3.0-or-later
"""Edge padding trim, IR flatten hardening, and pass-align border fill."""

from __future__ import annotations

import numpy as np

from pyopticfilm.pass_align import _warp_shift
from pyopticfilm.scan.calib_gl128 import (
    IR_SIDECAR_TARGET_LEVEL,
    flatten_ir_image_columns,
)
from pyopticfilm.scan.geometry import compute_geometry
from pyopticfilm.scan.pipeline import (
    apply_edge_trim,
    trim_invalid_edge_columns,
    trim_to_optical_span,
)
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE


def test_trim_invalid_edge_columns_drops_hot_left_padding():
    h, w = 64, 128
    rgb = np.full((h, w, 3), 20_000, dtype=np.uint16)
    rgb[:, 0:2, :] = 65_000
    out, (left, right) = trim_invalid_edge_columns(rgb)
    assert left == 2
    assert right == 0
    assert out.shape == (h, w - 2, 3)
    assert int(out[:, 0, 0].mean()) < 30_000


def test_trim_invalid_edge_columns_drops_dark_flat_padding():
    h, w = 64, 128
    rgb = np.full((h, w, 3), 18_000, dtype=np.uint16)
    rgb[:, 0, :] = 0
    out, (left, right) = trim_invalid_edge_columns(rgb)
    assert left == 1
    assert right == 0
    assert out.shape[1] == w - 1


def test_trim_invalid_edge_columns_leaves_clean_plane():
    h, w = 64, 128
    # Mild L/R gradient — not anomalous padding.
    x = np.linspace(15_000, 25_000, w, dtype=np.float32)
    rgb = np.broadcast_to(x[None, :, None], (h, w, 3)).astype(np.uint16).copy()
    out, trim = trim_invalid_edge_columns(rgb)
    assert trim == (0, 0)
    assert out.shape == rgb.shape


def test_trim_invalid_edge_columns_leaves_structured_bright_edge():
    """Specular film at the crop edge must not be trimmed as padding."""
    h, w = 64, 128
    rgb = np.full((h, w, 3), 20_000, dtype=np.uint16)
    # Bright but structured left column (not flat padding).
    rgb[:, 0, :] = 62_000
    rgb[10:20, 0, :] = 8_000
    rgb[40:50, 0, :] = 30_000
    out, trim = trim_invalid_edge_columns(rgb)
    assert trim == (0, 0)
    assert out.shape == rgb.shape


def test_apply_edge_trim_and_optical_span():
    rgb = np.zeros((8, 20, 3), dtype=np.uint16)
    cropped = apply_edge_trim(rgb, 2, 1)
    assert cropped.shape == (8, 17, 3)
    geo = compute_geometry(1800, model=MODEL_8200I_SE, area=(0.2, 0.2, 0.6, 0.5))
    wide = np.zeros((8, geo.pixels + 5, 3), dtype=np.uint16)
    trimmed = trim_to_optical_span(wide, geo)
    assert trimmed.shape[1] == geo.pixels


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
    # Shift left → right border would replicate column -1 without fill fix.
    out = _warp_shift(plane, dx=-2.0, dy=0.0)
    assert float(out[:, -1].mean()) < 30_000
    assert float(out[:, -2].mean()) < 30_000
