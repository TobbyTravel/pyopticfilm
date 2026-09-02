# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive GL128 OpticFilm init + scan configure over a fake USB transport."""

from __future__ import annotations

from pyopticfilm.device.protocol import FilmModel
from pyopticfilm.device.select import create_asic
from pyopticfilm.scan.geometry import ScanGeometry, compute_geometry
from pyopticfilm.scan.session_gl128 import Gl128ScanSession
from pyopticfilm.usb.fake import MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol


def run_gl128_setup(
    model: FilmModel,
    usb: MockScannerTransport | None = None,
    *,
    dpi: int = 1800,
    area: tuple[float, float, float, float] | None = (0.0, 0.0, 1.0, 0.12),
) -> tuple[MockScannerTransport, ScanGeometry]:
    """ASIC ``init`` + ``Gl128ScanSession._configure`` (motors gated; no bulk image).

    Default area is a short top crop so full-frame LINCNT gates do not fire and
    the register program stays comparable across SE (feed2 13704) and V2
    (feed2 13128) at 7200 dpi.
    """
    transport = usb if usb is not None else MockScannerTransport()
    proto = GenesysUsbProtocol(transport)
    asic = create_asic(proto, model)
    asic._motor_moves_enabled = False
    asic.init()
    geometry = compute_geometry(dpi, model=model, area=area)
    Gl128ScanSession(asic, model)._configure(geometry)
    return transport, geometry
