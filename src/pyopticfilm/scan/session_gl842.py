# SPDX-License-Identifier: GPL-3.0-or-later
"""GL842 scan session (SANE ``CommandSetGl842`` / OpticFilm 7200).

CCD OpticFilm path: ``MAXWD = line_bytes`` (not CIS ``* channels``).
``LPERIOD`` follows the GL843-style ``exposure / tgtime`` write.
"""

from __future__ import annotations

from pyopticfilm.device.model_7200 import MODEL_7200
from pyopticfilm.device.protocol import AsicDriver, FilmModel
from pyopticfilm.scan.calibrate import Calibrator
from pyopticfilm.scan.session import ScanSession


class Gl842ScanSession(ScanSession):
    """SANE GL842 OpticFilm 7200 scan configure/acquire."""

    _lperiod_divide_tgtime: bool = True

    def __init__(
        self,
        asic: AsicDriver,
        model: FilmModel = MODEL_7200,
        calibrator: Calibrator | None = None,
    ) -> None:
        super().__init__(asic, model, calibrator)
