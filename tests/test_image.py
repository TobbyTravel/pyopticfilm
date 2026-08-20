# SPDX-License-Identifier: GPL-3.0-or-later
"""ScanImage TIFF helper tests."""

import numpy as np
import pytest

from pyopticfilm.exceptions import PlustekError
from pyopticfilm.image import ScanImage


def test_save_tiff_requires_optional_dep_or_writes(tmp_path):
    rgb = np.zeros((4, 4, 3), dtype=np.uint16)
    image = ScanImage(rgb=rgb, dpi=3600)
    out = tmp_path / "frame.tif"
    try:
        import tifffile  # noqa: F401
    except ImportError:
        with pytest.raises(PlustekError, match="tiff"):
            image.save_tiff(out)
        return

    path = image.save_tiff(out)
    assert path.exists()
    assert path.suffix == ".tif"


def test_save_tiff_rejects_wrong_shape():
    image = ScanImage(rgb=np.zeros((4, 4), dtype=np.uint16), dpi=900)
    with pytest.raises(PlustekError, match="HxWx3"):
        image.save_tiff("x.tif")


def test_save_rgb16_tiff_roundtrip(tmp_path):
    try:
        import tifffile  # noqa: F401
    except ImportError:
        pytest.skip("tifffile not installed")

    from pyopticfilm.image import load_rgb16_tiff, save_rgb16_tiff

    rgb = np.arange(12, dtype=np.uint16).reshape(2, 2, 3)
    path = save_rgb16_tiff(rgb, tmp_path / "out.tif", dpi=1800)
    back, dpi = load_rgb16_tiff(path)
    assert back.shape == rgb.shape
    assert np.array_equal(back, rgb)
    assert dpi == 1800
