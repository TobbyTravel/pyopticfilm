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
    scan_ready = pyqtSignal(object, object)
    failed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    request_prescan = pyqtSignal(object)
    request_scan = pyqtSignal(object, int, bool, object)

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

    def cancel(self) -> None:
        self._cancel.set()

    def run_prescan(self, target: LabTarget) -> None:
        self._run(target, kind="prescan", dpi=prescan_resolution(target.model), ir=False, crop=None)

    def run_scan(
        self,
        target: LabTarget,
        dpi: int,
        ir_pass: bool,
        crop_norm: tuple[float, float, float, float] | None,
    ) -> None:
        # PyQt queued signals pass every argument positionally (not as keywords).
        self._run(target, kind="scan", dpi=dpi, ir=ir_pass, crop=crop_norm)

    def _run(
        self,
        target: LabTarget,
        *,
        kind: str,
        dpi: int,
        ir: bool,
        crop: tuple[float, float, float, float] | None,
    ) -> None:
        self.busy_changed.emit(True)
        self._cancel.clear()
        self.progress.emit(0.0)
        try:
            scanner = self.ensure_open(target)
            scan_kw = lab_scan_kwargs(target.model, dpi=dpi, kind=kind, crop_norm=crop)
            apply_calib = not target.mock
            if kind == "prescan":
                self._usb_divider(f"PRESCAN {dpi} dpi")
                image = scanner.scan(
                    mode="color",
                    progress=self._progress,
                    cancel=self._cancel,
                    apply_calib=apply_calib,
                    **scan_kw,
                )
                self.prescan_ready.emit(image)
            else:
                self._usb_divider(f"SCAN {dpi} dpi")
                colour = scanner.scan(
                    mode="color",
                    progress=self._progress,
                    cancel=self._cancel,
                    apply_calib=apply_calib,
                    **scan_kw,
                )
                ir_image: ScanImage | None = None
                if ir and getattr(target.model, "supports_infrared", False):
                    self.progress.emit(0.0)
                    self._usb_divider(f"IR {dpi} dpi")
                    ir_image = scanner.scan(
                        mode="infrared",
                        progress=self._progress,
                        cancel=self._cancel,
                        apply_calib=False,
                        **scan_kw,
                    )
                self.scan_ready.emit(colour, ir_image)
        except ScanCancelled:
            self.busy_changed.emit(False)
            self.failed.emit("Scan cancelled")
        except Exception as exc:  # noqa: BLE001
            self.busy_changed.emit(False)
            self.failed.emit(str(exc))
        finally:
            self.busy_changed.emit(False)
            self.progress.emit(0.0)
