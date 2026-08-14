# SPDX-License-Identifier: GPL-3.0-or-later
"""PyQt6 main window for the pyopticfilm scan lab."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pyopticfilm.image import ScanImage
from tools.scanlab.backend import (
    LabTarget,
    device_banner,
    list_lab_targets,
    usb_log_section_key,
    with_mock_mode,
)
from tools.scanlab.widgets import CropImageView
from tools.scanlab.worker import ScanWorker


class ScanLabWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("pyopticfilm Scan Lab")
        self.resize(1100, 720)

        self._targets: list[LabTarget] = []
        self._usb_sections: dict[str, int] = {}
        self._thread = QThread(self)
        self._worker = ScanWorker()
        self._worker.moveToThread(self._thread)
        self._thread.start()

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)

        controls = QWidget()
        form = QVBoxLayout(controls)
        controls.setFixedWidth(280)

        form.addWidget(QLabel("Device"))
        self.device = QComboBox()
        form.addWidget(self.device)

        self.run_mock = QCheckBox("Run against MOCK")
        self.run_mock.setChecked(True)
        self.run_mock.toggled.connect(self._refresh_banner)
        form.addWidget(self.run_mock)

        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self.reload_devices)
        form.addWidget(refresh)

        form.addWidget(QLabel("PPI"))
        self.ppi = QComboBox()
        form.addWidget(self.ppi)

        self.ir_pass = QCheckBox("IR pass (second scan)")
        form.addWidget(self.ir_pass)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("color: #c9a227; font-weight: 600;")
        form.addWidget(self.banner)

        self.btn_prescan = QPushButton("Prescan")
        self.btn_scan = QPushButton("Scan")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_prescan.clicked.connect(self._on_prescan)
        self.btn_scan.clicked.connect(self._on_scan)
        self.btn_cancel.clicked.connect(self._worker.cancel)
        form.addWidget(self.btn_prescan)
        form.addWidget(self.btn_scan)
        form.addWidget(self.btn_cancel)
        form.addStretch(1)

        tabs = QTabWidget()
        self.prescan_view = CropImageView()
        self.scan_view = CropImageView()
        self.ir_view = CropImageView()
        self.usb_log = QPlainTextEdit()
        self.usb_log.setReadOnly(True)
        self.usb_log.setPlaceholderText("USB transactions appear here…")
        jump_row = QHBoxLayout()
        jump_row.addWidget(QLabel("Jump"))
        self.btn_jump_prescan = QPushButton("Prescan")
        self.btn_jump_scan = QPushButton("Scan")
        self.btn_jump_ir = QPushButton("IR")
        self.btn_jump_prescan.clicked.connect(lambda: self._jump_usb_section("PRESCAN"))
        self.btn_jump_scan.clicked.connect(lambda: self._jump_usb_section("SCAN"))
        self.btn_jump_ir.clicked.connect(lambda: self._jump_usb_section("IR"))
        jump_row.addWidget(self.btn_jump_prescan)
        jump_row.addWidget(self.btn_jump_scan)
        jump_row.addWidget(self.btn_jump_ir)
        jump_row.addStretch(1)
        clear_log = QPushButton("Clear USB log")
        clear_log.clicked.connect(self._clear_usb_log)
        jump_row.addWidget(clear_log)
        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.addLayout(jump_row)
        log_layout.addWidget(self.usb_log)
        self._update_usb_jump_buttons()

        tabs.addTab(self.prescan_view, "Prescan")
        tabs.addTab(self.scan_view, "Scan")
        tabs.addTab(self.ir_view, "IR")
        tabs.addTab(log_page, "USB log")
        self.tabs = tabs

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(controls)
        splitter.addWidget(tabs)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.statusBar().addPermanentWidget(self.progress, 1)

        self.device.currentIndexChanged.connect(self._on_device_changed)

        self._worker.progress.connect(self._on_progress)
        self._worker.usb_line.connect(self._append_usb)
        self._worker.banner.connect(self.banner.setText)
        self._worker.prescan_ready.connect(self._on_prescan_ready)
        self._worker.scan_ready.connect(self._on_scan_ready)
        self._worker.failed.connect(self._on_failed)
        self._worker.busy_changed.connect(self._on_busy)

        self.reload_devices()

    def closeEvent(self, event) -> None:
        self._worker.cancel()
        self._worker.close_scanner()
        self._thread.quit()
        self._thread.wait(3000)
        super().closeEvent(event)

    def reload_devices(self) -> None:
        self._targets = list_lab_targets()
        self.device.blockSignals(True)
        self.device.clear()
        for target in self._targets:
            self.device.addItem(target.label)
        self.device.blockSignals(False)
        if self._targets:
            self._on_device_changed(0)

    def _current_target(self) -> LabTarget | None:
        idx = self.device.currentIndex()
        if idx < 0 or idx >= len(self._targets):
            return None
        return self._targets[idx]

    def _on_device_changed(self, _index: int) -> None:
        target = self._current_target()
        if target is None:
            return
        self.ppi.blockSignals(True)
        self.ppi.clear()
        for dpi in target.model.resolutions_dpi:
            self.ppi.addItem(str(dpi), dpi)
        preferred = 1800 if 1800 in target.model.resolutions_dpi else target.model.resolutions_dpi[0]
        self.ppi.setCurrentIndex(list(target.model.resolutions_dpi).index(preferred))
        self.ppi.blockSignals(False)
        self.ir_pass.setEnabled(bool(getattr(target.model, "supports_infrared", False)))
        if not self.ir_pass.isEnabled():
            self.ir_pass.setChecked(False)
        self._refresh_banner()
        self.prescan_view.clear_crop()

    def _refresh_banner(self) -> None:
        target = self._current_target()
        if target is None:
            self.banner.setText("")
            return
        mock = self.run_mock.isChecked()
        if not mock and not target.device_id:
            self.banner.setText(
                f"No scanner connected for {target.model.model}. "
                "Plug it in or keep MOCK enabled."
            )
            return
        self.banner.setText(device_banner(with_mock_mode(target, mock)))

    def _resolved_target(self, *, warn_if_missing: bool = True) -> LabTarget | None:
        target = self._current_target()
        if target is None:
            return None
        mock = self.run_mock.isChecked()
        if not mock and not target.device_id:
            if warn_if_missing:
                QMessageBox.warning(
                    self,
                    "Scan lab",
                    "No matching scanner is connected. Plug in the device or keep MOCK enabled.",
                )
            return None
        return with_mock_mode(target, mock)

    def _on_prescan(self) -> None:
        target = self._resolved_target()
        if target is None:
            return
        self._clear_usb_log()
        self._worker.request_prescan.emit(target)

    def _on_scan(self) -> None:
        target = self._resolved_target()
        if target is None:
            return
        dpi = int(self.ppi.currentData())
        self._worker.request_scan.emit(
            target,
            dpi,
            self.ir_pass.isChecked(),
            self.prescan_view.crop_norm,
        )

    def _on_progress(self, value: float) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, value)) * 1000))

    def _append_usb(self, line: str) -> None:
        self.usb_log.appendPlainText(line)
        key = usb_log_section_key(line)
        if key is None:
            return
        self._usb_sections[key] = self.usb_log.document().lastBlock().position()
        self._update_usb_jump_buttons()

    def _clear_usb_log(self) -> None:
        self.usb_log.clear()
        self._clear_usb_sections()

    def _clear_usb_sections(self) -> None:
        self._usb_sections.clear()
        self._update_usb_jump_buttons()

    def _update_usb_jump_buttons(self) -> None:
        self.btn_jump_prescan.setEnabled("PRESCAN" in self._usb_sections)
        self.btn_jump_scan.setEnabled("SCAN" in self._usb_sections)
        self.btn_jump_ir.setEnabled("IR" in self._usb_sections)

    def _jump_usb_section(self, key: str) -> None:
        pos = self._usb_sections.get(key)
        if pos is None:
            return
        cursor = self.usb_log.textCursor()
        cursor.setPosition(pos)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        self.usb_log.setTextCursor(cursor)
        self.usb_log.ensureCursorVisible()
        bar = self.usb_log.verticalScrollBar()
        bar.setValue(bar.value() + self.usb_log.cursorRect().top())
        self.tabs.setCurrentWidget(self.usb_log.parentWidget())

    def _on_prescan_ready(self, image: ScanImage) -> None:
        self.prescan_view.set_rgb(image.rgb)
        self.tabs.setCurrentWidget(self.prescan_view)
        self.statusBar().showMessage(
            f"Prescan {image.rgb.shape[1]}×{image.rgb.shape[0]} @ {image.dpi} dpi — drag a crop"
        )

    def _on_scan_ready(self, colour: ScanImage, ir: ScanImage | None) -> None:
        self.scan_view.set_rgb(colour.rgb)
        self.tabs.setCurrentWidget(self.scan_view)
        if ir is not None:
            plane = ir.ir if ir.ir is not None else ir.rgb[:, :, 1]
            self.ir_view.set_gray(plane)
        else:
            self.ir_view.set_rgb(None)
        msg = f"Scan {colour.rgb.shape[1]}×{colour.rgb.shape[0]} @ {colour.dpi} dpi"
        if ir is not None:
            msg += f"; IR {ir.rgb.shape[1]}×{ir.rgb.shape[0]}"
        self.statusBar().showMessage(msg)

    def _on_failed(self, message: str) -> None:
        self.statusBar().showMessage(message)
        if message != "Scan cancelled":
            QMessageBox.warning(self, "Scan lab", message)

    def _on_busy(self, busy: bool) -> None:
        self.btn_prescan.setEnabled(not busy)
        self.btn_scan.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.device.setEnabled(not busy)
        self.ppi.setEnabled(not busy)
        self.run_mock.setEnabled(not busy)


def run() -> int:
    import sys

    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    win = ScanLabWindow()
    win.show()
    return app.exec()
