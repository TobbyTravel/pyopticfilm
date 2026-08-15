# SPDX-License-Identifier: GPL-3.0-or-later
"""GL843 swap_16bit_data decode (SANE sensor flag for 7200i)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from pyopticfilm.device.model_7200i import MODEL_7200I
from pyopticfilm.device.model_8200i import MODEL_8200I
from pyopticfilm.scan.geometry import compute_geometry
from pyopticfilm.scan.pipeline import ImagePipeline


def test_7200i_decodes_with_16bit_byteswap():
    assert MODEL_7200I.swap_16bit_data is True
    geo = compute_geometry(900, model=MODEL_7200I, area=(0.0, 0.0, 0.1, 0.05))
    geo = replace(geo, lines=1, optical_line_count=1)
    # One pixel RGB LE words 0x1234 / 0x5678 / 0x9ABC — after swap become BE-as-LE.
    pixel = np.array([0x1234, 0x5678, 0x9ABC], dtype="<u2")
    raw = np.tile(pixel, geo.pixels).astype("<u2").tobytes()
    # Truncate/pad to exact size
    need = geo.total_bytes
    raw = (raw + b"\x00" * need)[:need]
    out = ImagePipeline(MODEL_7200I).decode_rgb(raw, geometry=geo, planar=False)
    assert int(out[0, 0, 0]) == 0x3412
    assert int(out[0, 0, 1]) == 0x7856
    assert int(out[0, 0, 2]) == 0xBC9A


def test_8200i_does_not_byteswap():
    assert getattr(MODEL_8200I, "swap_16bit_data", False) is False
    geo = compute_geometry(900, model=MODEL_8200I, area=(0.0, 0.0, 0.1, 0.05))
    geo = replace(geo, lines=1, optical_line_count=1)
    pixel = np.array([0x1234, 0x5678, 0x9ABC], dtype="<u2")
    raw = (np.tile(pixel, geo.pixels).astype("<u2").tobytes() + b"\x00" * geo.total_bytes)[
        : geo.total_bytes
    ]
    out = ImagePipeline(MODEL_8200I).decode_rgb(raw, geometry=geo, planar=False)
    assert int(out[0, 0, 0]) == 0x1234
