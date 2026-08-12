# SPDX-License-Identifier: GPL-3.0-or-later
"""Plustek USB driver — raw access for OpticFilm (Genesys) scanners."""

from __future__ import annotations

from pyopticfilm._version import __version__
from pyopticfilm.device.model_8200i import MODEL_8200I, Model8200i
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE, Model8200iSE
from pyopticfilm.device.select import KNOWN_MODELS
from pyopticfilm.exceptions import (
    AsicError,
    CalibrationError,
    DeviceNotFoundError,
    DriverBindingError,
    MotorTimeoutError,
    PlustekError,
    ScanCancelled,
    ScanError,
    UnsupportedDeviceError,
    UsbError,
)
from pyopticfilm.image import ScanImage
from pyopticfilm.scanner import Scanner

__all__ = [
    "KNOWN_MODELS",
    "MODEL_8200I",
    "MODEL_8200I_SE",
    "AsicError",
    "CalibrationError",
    "DeviceNotFoundError",
    "DriverBindingError",
    "Model8200i",
    "Model8200iSE",
    "MotorTimeoutError",
    "PlustekError",
    "ScanCancelled",
    "ScanError",
    "ScanImage",
    "Scanner",
    "UnsupportedDeviceError",
    "UsbError",
    "__version__",
]
