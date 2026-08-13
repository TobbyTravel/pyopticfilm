# SPDX-License-Identifier: GPL-3.0-or-later
"""GL843 scan session (SANE ``CommandSetGl843`` / OpticFilm film path).

Shares motor slope / feed / bulk acquire with :class:`ScanSession` (GL845).
Differences that matter for OpticFilm:

* ``MAXWD`` is in 2-word units → ``line_bytes >> 1`` when optical≈full
* ``LPERIOD`` is written as ``exposure / tgtime``
"""

from __future__ import annotations

from pyopticfilm.device.model_7200i import MODEL_7200I
from pyopticfilm.device.protocol import AsicDriver, FilmModel
from pyopticfilm.scan.calibrate import Calibrator
from pyopticfilm.scan.session import ScanSession


class Gl843ScanSession(ScanSession):
    """SANE GL843 OpticFilm scan configure/acquire."""

    _lperiod_divide_tgtime: bool = True

    def __init__(
        self,
        asic: AsicDriver,
        model: FilmModel = MODEL_7200I,
        calibrator: Calibrator | None = None,
    ) -> None:
        super().__init__(asic, model, calibrator)
