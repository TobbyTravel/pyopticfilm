# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive OpticFilm 8200i (GL845) init + scan configure over a fake USB transport."""

from __future__ import annotations

from pyopticfilm.device.model_8200i import MODEL_8200I
from pyopticfilm.device.select import create_asic
from pyopticfilm.scan.geometry import ScanGeometry, compute_geometry
from pyopticfilm.scan.session import ScanSession
from pyopticfilm.usb.protocol import GenesysUsbProtocol
from scanners.fake_usb import FakeUsbTransport


def run_8200i_setup(
    usb: FakeUsbTransport | None = None,
    *,
    dpi: int = 1800,
) -> tuple[FakeUsbTransport, ScanGeometry]:
    """ASIC ``init`` + ``ScanSession._configure`` (no home, lamp, or bulk image)."""
    transport = usb if usb is not None else FakeUsbTransport()
    proto = GenesysUsbProtocol(transport)
    asic = create_asic(proto, MODEL_8200I)
    asic.init()
    geometry = compute_geometry(dpi, model=MODEL_8200I)
    ScanSession(asic, MODEL_8200I)._configure(geometry)
    return transport, geometry
