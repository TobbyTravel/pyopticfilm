# SPDX-License-Identifier: GPL-3.0-or-later
"""OpticFilm 8100 V2 model tables (GL128).

The 8100 V2 (``07b3:1824``) is a hardware sibling of the OpticFilm 8200i SE
(``07b3:1825``): same **GL128** ASIC, same USB vendor-request protocol, and —
per the user confirming the hardware matches apart from the absence of an
infrared channel — same register tables, geometry, and motor constants.

This model was **not** derived from independent USB captures; it is a
confidence-alias that reuses every register table from the 8200i SE.  Only
the identity and IR capability differ:

* ``usb_product_id = 0x1824``
* ``name = "plustek-opticfilm-8100-v2"``
* ``model = "OpticFilm 8100 (V2)"``
* ``supports_infrared = False`` – no IR LED / no iSRD
* ``multi_exposure_factor = 1`` – single-pass colour only

All ``scan`` / ``calibrate`` / ``home`` / ``park`` safety gates are inherited
from the SE and remain in force: FEEDL clamps, ``_park_ok`` origin tracking,
stationary-shading motor disarming, and so on.

Bring-up status: **not yet hardware-validated**.  The first live test is
performed on Tobby's unit; see ``docs/scanner-validation.md`` for the
expected sequence (open → read_status → lamp → home → preview scan).
"""

from __future__ import annotations

from dataclasses import dataclass

from pyopticfilm.device.model_8200i_se import Model8200iSE


@dataclass(frozen=True)
class Model8100V2(Model8200iSE):
    """OpticFilm 8100 V2 — GL128 sibling of the 8200i SE without IR / iSRD."""

    name: str = "plustek-opticfilm-8100-v2"
    vendor: str = "PLUSTEK"
    model: str = "OpticFilm 8100 (V2)"
    asic: str = "GL128"
    usb_vendor_id: int = 0x07B3
    usb_product_id: int = 0x1824

    #: Hardware-validated only on the 8200i SE; the 8100 V2 is a
    #: capture-alias awaiting its own first live scan.  Kept ``True`` so
    #: the existing GL128 motor-gate escape hatch
    #: (``Scanner._ensure_scan_ready``) permits motor moves; bring-up
    #: safety is enforced by the inherited FEEDL clamps, not by this flag.
    scan_ready: bool = True

    #: No infrared channel / iSRD on this variant: ``supports_infrared`` is
    #: False, and :meth:`Gl128ScanSession.run` refuses IR passes for this model.
    #: Multi-exposure (short+long colour bracket) remains available — the
    #: GL128 ASIC supports it; we simply have no IR plane to merge against.
    supports_infrared: bool = False


MODEL_8100_V2 = Model8100V2()
