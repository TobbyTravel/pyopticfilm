# SPDX-License-Identifier: GPL-3.0-or-later
"""IR flatten hardening and pass-align border fill."""

from __future__ import annotations

import numpy as np

from pyopticfilm.pass_align import _warp_shift
from pyopticfilm.scan.calib_gl128 import (
    IR_SIDECAR_TARGET_LEVEL,
    flatten_ir_image_columns,
)


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
