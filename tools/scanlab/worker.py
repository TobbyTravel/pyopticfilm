# SPDX-License-Identifier: GPL-3.0-or-later
"""Background scan worker (never run USB I/O on the GUI thread)."""

from __future__ import annotations

import threading
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from pyopticfilm.exceptions import ScanCancelled
from pyopticfilm.image import ScanImage
from pyopticfilm.usb.trace import UsbTransaction
from tools.scanlab.backend import (
    LabTarget,
    apply_lab_motor_acoustic,
    device_banner,
    lab_scan_kwargs,
    open_lab_scanner,
    prescan_resolution,
    usb_log_divider,
    usb_log_line,
)


class ScanWorker(QObject):
    progress = pyqtSignal(float)
    usb_line = pyqtSignal(str)
    banner = pyqtSignal(str)
    prescan_ready = pyqtSignal(object)
    scan_ready = pyqtSignal(object)
    me_debug_ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    calib_cleared = pyqtSignal(str)
    #: target, apply_calib
    request_prescan = pyqtSignal(object, bool)
    #: target, dpi, ir_pass, me_pass, crop_norm, apply_calib, me_exposure_mode
    request_scan = pyqtSignal(object, int, bool, bool, object, bool, str)

    def __init__(self) -> None:
        super().__init__()
        self._target: LabTarget | None = None
        self._scanner: Any = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.request_prescan.connect(self.run_prescan)
        self.request_scan.connect(self.run_scan)

    def _on_usb(self, txn: UsbTransaction) -> None:
        self.usb_line.emit(usb_log_line(txn))

    def _progress(self, value: float) -> None:
        self.progress.emit(float(value))

    def _usb_divider(self, title: str) -> None:
        self.usb_line.emit("")
        self.usb_line.emit(usb_log_divider(title))

    def close_scanner(self) -> None:
        with self._lock:
            scanner = self._scanner
            self._scanner = None
            self._target = None
        if scanner is not None:
            try:
                scanner.close()
            except Exception:  # noqa: BLE001, S110
                pass

    def ensure_open(self, target: LabTarget) -> Any:
        with self._lock:
            if self._scanner is not None and self._target == target:
                return self._scanner
        self.close_scanner()
        scanner, _rec = open_lab_scanner(target, on_usb=self._on_usb)
        with self._lock:
            self._target = target
            self._scanner = scanner
        self.banner.emit(device_banner(target))
        return scanner

    def clear_calib_cache(self) -> None:
        """Drop on-disk calib entries and close the scanner session."""
        with self._lock:
            scanner = self._scanner
        if scanner is None:
            self.calib_cleared.emit("")
            return
        calibrator = getattr(scanner, "_calibrator", None)
        path = ""
        if calibrator is not None:
            path = str(getattr(calibrator, "cache_path", None) or "")
            calibrator.clear()
        asic = getattr(scanner, "_asic", None)
        if asic is not None:
            asic.asic_shading_ready = False
        self.close_scanner()
        self.calib_cleared.emit(path)

    def cancel(self) -> None:
        self._cancel.set()

    def run_prescan(self, target: LabTarget, apply_calib: bool = False) -> None:
        self._run(
            target,
            kind="prescan",
            dpi=prescan_resolution(target.model),
            ir=False,
            me=False,
            crop=None,
            apply_calib=bool(apply_calib),
        )

    def run_scan(
        self,
        target: LabTarget,
        dpi: int,
        ir_pass: bool,
        me_pass: bool,
        crop_norm: tuple[float, float, float, float] | None,
        apply_calib: bool = False,
        me_exposure_mode: str = "adaptive",
    ) -> None:
        self._run(
            target,
            kind="scan",
            dpi=dpi,
            ir=ir_pass,
            me=me_pass,
            crop=crop_norm,
            apply_calib=bool(apply_calib),
            me_exposure_mode=str(me_exposure_mode or "adaptive"),
        )

    def _run(
        self,
        target: LabTarget,
        *,
        kind: str,
        dpi: int,
        ir: bool,
        me: bool,
        crop: tuple[float, float, float, float] | None,
        apply_calib: bool,
        me_exposure_mode: str = "adaptive",
    ) -> None:
        self.busy_changed.emit(True)
        self._cancel.clear()
        self.progress.emit(0.0)
        try:
            scanner = self.ensure_open(target)
            apply_lab_motor_acoustic(scanner, target)
            scan_kw = lab_scan_kwargs(target.model, dpi=dpi, kind=kind, crop_norm=crop)
            if kind == "prescan":
                self._usb_divider(f"PRESCAN {dpi} dpi")
                image = scanner.scan(
                    mode="color",
                    progress=self._progress,
                    cancel=self._cancel,
                    apply_calib=apply_calib,
                    multi_exposure=me,
                    **scan_kw,
                )
                self.prescan_ready.emit(image)
            else:
                self._usb_divider(f"SCAN {dpi} dpi")
                if me:
                    self._usb_divider(
                        f"ME multi-pass ({me_exposure_mode})"
                    )
                if ir:
                    self._usb_divider("IR pass")
                image: ScanImage = scanner.scan(
                    mode="color",
                    progress=self._progress,
                    cancel=self._cancel,
                    apply_calib=apply_calib,
                    multi_exposure=me,
                    infrared=ir,
                    me_exposure_mode=me_exposure_mode,
                    **scan_kw,
                )
                self.me_debug_ready.emit(getattr(scanner, "last_me_debug", None))
                self.scan_ready.emit(image)
        except ScanCancelled:
            self.busy_changed.emit(False)
            self.failed.emit("Scan cancelled")
        except Exception as exc:  # noqa: BLE001
            self.busy_changed.emit(False)
            self.failed.emit(str(exc))
        finally:
            self.busy_changed.emit(False)
            self.progress.emit(0.0)
