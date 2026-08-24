# SPDX-License-Identifier: GPL-3.0-or-later

"""OpticFilm 8100 V2 model definition.

The 8100 V2 (``07b3:1824``) uses the GL128 ASIC and shares the
register tables, geometry, and motor constants of the 8200i SE.

Unlike the 8200i SE, the 8100 V2 has no infrared channel or iSRD support.
Multi-exposure colour scanning remains supported.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyopticfilm.device.model_8200i_se import Model8200iSE


@dataclass(frozen=True)
class Model8100V2(Model8200iSE):
    """OpticFilm 8100 V2 — GL128 sibling of the 8200i SE without IR."""

    name: str = "plustek-opticfilm-8100-v2"
    vendor: str = "PLUSTEK"
    model: str = "OpticFilm 8100 (V2)"
    asic: str = "GL128"
    usb_vendor_id: int = 0x07B3
    usb_product_id: int = 0x1824

    scan_ready: bool = True
    supports_infrared: bool = False


MODEL_8100_V2 = Model8100V2()
