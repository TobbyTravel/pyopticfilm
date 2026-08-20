# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan Lab preview conversion (no GUI window)."""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from tools.scanlab.preview import MAX_DISPLAY_EDGE, downsample_for_display


def _pyqt6_gui_importable() -> bool:
    """True when PyQt6 is installed *and* its native libs load (needs libEGL on Linux CI)."""
    if importlib.util.find_spec("PyQt6") is None:
        return False
    try:
        from PyQt6.QtGui import QImage  # noqa: F401
    except ImportError:
        return False
    return True


def test_downsample_for_display_caps_long_edge():
    arr = np.arange(80 * 200 * 3, dtype=np.uint16).reshape(80, 200, 3)
    out = downsample_for_display(arr, max_edge=50)
    assert max(out.shape[:2]) <= 50
    assert out.shape[2] == 3
    assert downsample_for_display(arr, max_edge=400) is arr


@pytest.mark.skipif(
    not _pyqt6_gui_importable(),
    reason="PyQt6 GUI libs unavailable (install lab extra + libEGL on Linux)",
)
def test_rgb16_to_qimage_odd_width_and_downsample():
    from tools.scanlab.widgets import rgb16_to_qimage

    rgb = np.full((32, 17, 3), 0x4000, dtype=np.uint16)
    rgb[:, 8, :] = 0xC000
    image = rgb16_to_qimage(rgb, auto_level=True)
    assert not image.isNull()
    assert image.width() == 17
    assert image.height() == 32

    wide = np.zeros((80, 5000, 3), dtype=np.uint16)
    wide[:, ::17] = 0x8000
    preview = rgb16_to_qimage(wide, auto_level=True)
    assert not preview.isNull()
    assert max(preview.width(), preview.height()) <= MAX_DISPLAY_EDGE
