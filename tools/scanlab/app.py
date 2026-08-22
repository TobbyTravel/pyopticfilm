# SPDX-License-Identifier: GPL-3.0-or-later
"""PyQt6 main window for the pyopticfilm scan lab."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
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
    format_crop_status,
    lab_crop_scan_meta,
    lab_scan_needs_motor_warning,
    list_lab_targets,
    nonse_safe_y_fraction,
    usb_log_section_key,
    with_hw_override,
    with_mock_mode,
    with_motor_acoustic,
    with_usb_planar,
)
from tools.scanlab.capture_pcap import (
    CaptureAnalysis,
    analyze_usbpcap,
    decode_all_capture_passes,
    format_capture_usb_log_lines,
    model_for_capture_decode,
    motor_register_diff,
)
from tools.scanlab.widgets import ImageTabPage
from tools.scanlab.worker import ScanWorker


class ScanLabWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("pyopticfilm Scan Lab")
        self.resize(1100, 720)

        self._targets: list[LabTarget] = []
        self._usb_sections: dict[str, int] = {}
        self._capture: CaptureAnalysis | None = None
        self._last_scan: ScanImage | None = None
        self._last_prescan_dpi: int | None = None
        self._pending_crop_meta: dict | None = None
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

        self.override_hw_gate = QCheckBox("Override safety HW gate")
        self.override_hw_gate.setChecked(False)
        self.override_hw_gate.toggled.connect(self._on_override_hw_gate)
        form.addWidget(self.override_hw_gate)

        self.apply_calib = QCheckBox("Apply calib")
        self.apply_calib.setChecked(True)
        self.apply_calib.setToolTip(
            "ASIC shading before colour scans. First prescan/scan at each PPI "
            "measures once; results are cached in ~/.cache/pyopticfilm/calib_v2.json."
        )
        form.addWidget(self.apply_calib)

        self.btn_clear_calib = QPushButton("Clear calib cache")
        self.btn_clear_calib.clicked.connect(self._on_clear_calib_cache)
        form.addWidget(self.btn_clear_calib)

        self.usb_planar = QCheckBox("USB planar RGB")
        self.usb_planar.setChecked(False)
        self.usb_planar.toggled.connect(self._on_usb_planar)
        form.addWidget(self.usb_planar)

        self.quiet_usb_pace = QCheckBox("Adaptive quiet drain")
        self.quiet_usb_pace.setChecked(True)
        self.quiet_usb_pace.setToolTip(
            "Rate-limit host bulk reads to the ASIC line rate (no fixed pause "
            "before each chunk). Keeps motor creep continuous at 7200 dpi; "
            "uncheck for fastest drain (louder)."
        )
        form.addWidget(self.quiet_usb_pace)

        self.slow_image_slope = QCheckBox("Slow image slope")
        self.slow_image_slope.setChecked(False)
        self.slow_image_slope.setToolTip(
            "Upload the shading/slow motor ramp for the image pass (acoustic probe). "
            "Feeds still use the fast ramp."
        )
        form.addWidget(self.slow_image_slope)

        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self.reload_devices)
        form.addWidget(refresh)

        form.addWidget(QLabel("PPI"))
        self.ppi = QComboBox()
        form.addWidget(self.ppi)

        self.ir_pass = QCheckBox("IR pass (second scan)")
        form.addWidget(self.ir_pass)

        self.me_pass = QCheckBox("Multi-exposure (ME)")
        form.addWidget(self.me_pass)

        self.me_pass.toggled.connect(self._on_me_pass_toggled)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("color: #c9a227; font-weight: 600;")
        form.addWidget(self.banner)

        self.btn_prescan = QPushButton("Prescan")
        self.btn_scan = QPushButton("Scan")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_open_capture = QPushButton("Open capture…")
        self.btn_cancel.setEnabled(False)
        self.btn_prescan.clicked.connect(self._on_prescan)
        self.btn_scan.clicked.connect(self._on_scan)
        self.btn_cancel.clicked.connect(self._worker.cancel)
        self.btn_open_capture.clicked.connect(self._on_open_capture)
        form.addWidget(self.btn_prescan)
        form.addWidget(self.btn_scan)
        form.addWidget(self.btn_cancel)
        form.addWidget(self.btn_open_capture)
        form.addStretch(1)

        tabs = QTabWidget()
        self.prescan_view = ImageTabPage(default_stem="prescan", allow_crop=True)
        self.scan_view = ImageTabPage(default_stem="color_short", allow_load=True)
        self.me_long_view = ImageTabPage(default_stem="color_long", allow_load=True)
        self.merged_view = ImageTabPage(default_stem="merged")
        self.ir_view = ImageTabPage(default_stem="ir")
        self.capture_diff = QPlainTextEdit()
        self.capture_diff.setReadOnly(True)
        self.capture_diff.setPlaceholderText(
            "Open a USBPcap .pcap / .pcapng to decode the image bulk and compare "
            "FEEDL / LINCNT / DPISET to Lab geometry…"
        )
        self.usb_log = QPlainTextEdit()
        self.usb_log.setReadOnly(True)
        self.usb_log.setPlaceholderText("USB transactions appear here…")
        jump_row = QHBoxLayout()
        jump_row.addWidget(QLabel("Jump"))
        self.btn_jump_prescan = QPushButton("Prescan")
        self.btn_jump_scan = QPushButton("Scan")
        self.btn_jump_ir = QPushButton("IR")
        self.btn_jump_capture = QPushButton("Capture")
        self.btn_jump_prescan.clicked.connect(lambda: self._jump_usb_section("PRESCAN"))
        self.btn_jump_scan.clicked.connect(lambda: self._jump_usb_section("SCAN"))
        self.btn_jump_ir.clicked.connect(lambda: self._jump_usb_section("IR"))
        self.btn_jump_capture.clicked.connect(lambda: self._jump_usb_section("CAPTURE"))
        jump_row.addWidget(self.btn_jump_prescan)
        jump_row.addWidget(self.btn_jump_scan)
        jump_row.addWidget(self.btn_jump_ir)
        jump_row.addWidget(self.btn_jump_capture)
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
        tabs.addTab(self.scan_view, "Color short")
        tabs.addTab(self.me_long_view, "Color long")
        tabs.addTab(self.merged_view, "Merged")
        tabs.addTab(self.ir_view, "IR")
        tabs.addTab(self.capture_diff, "Capture")
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
        self.ppi.currentIndexChanged.connect(self._on_ppi_changed)

        self._worker.progress.connect(self._on_progress)
        self._worker.usb_line.connect(self._append_usb)
        self._worker.banner.connect(self.banner.setText)
        self._worker.prescan_ready.connect(self._on_prescan_ready)
        self._worker.scan_ready.connect(self._on_scan_ready)
        self._worker.failed.connect(self._on_failed)
        self._worker.busy_changed.connect(self._on_busy)
        self._worker.calib_cleared.connect(self._on_calib_cleared)

        self.scan_view.load_clicked.connect(lambda: self._load_me_plane("short"))
        self.me_long_view.load_clicked.connect(lambda: self._load_me_plane("long"))

        self.reload_devices()
        self._update_me_tabs_visible()

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
        is_gl128 = getattr(target.model, "asic", "") == "GL128"
        self.me_pass.setEnabled(is_gl128)
        if not is_gl128:
            self.me_pass.setChecked(False)
        self._update_me_tabs_visible()
        self._refresh_banner()
        self.prescan_view.clear_crop()
        if self._capture is not None:
            self._decode_loaded_capture()

    def _on_ppi_changed(self, _index: int) -> None:
        if self._capture is not None:
            self._decode_loaded_capture()

    def _refresh_merged_preview(self) -> None:
        image = self._last_scan
        if image is None or image.rgb_short is None or image.rgb_long is None:
            self.merged_view.set_caption("")
            return
        try:
            from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
            from pyopticfilm.scan.exposure_merge import merge_exposures_result
            from pyopticfilm.scan.pipeline import ImagePipeline

            result = merge_exposures_result(
                image.rgb_short,
                image.rgb_long,
                exposure_short=image.exposure_short or 14000,
                exposure_long=image.exposure_long or 42000,
                align_shift=image.align_shift_long,
            )
            # Same deliverable path as a live ME scan: short-scale IVW then makeup.
            # Without makeup, Merged matches Color short brightness (by design of
            # short-scale fusion) and looks "unused."
            pipe = ImagePipeline(MODEL_8200I_SE)
            rgb = pipe.expose_film_base(
                result.rgb, source="me preview", preserve_headroom=True
            )
            rgb = pipe.clamp_border_highlights(rgb)
            self.merged_view.set_rgb(rgb, dpi=image.dpi, auto_level=False)
            if result.fusion_stats is not None:
                self.merged_view.set_caption(self._format_fusion_caption(result.fusion_stats))
            else:
                self.merged_view.set_caption("Merge: SNR / IVW (short scale + makeup)")
        except Exception:  # noqa: BLE001
            self.merged_view.set_rgb(None)
            self.merged_view.set_caption("")

    @staticmethod
    def _format_fusion_caption(stats) -> str:
        # Absolute IVW weights are tiny (÷ variance); ratio matters.
        ws = float(stats.mean_short_weight)
        wl = float(stats.mean_long_weight)
        ratio_w = wl / max(ws, 1e-30)
        msg = (
            f"SNR / IVW — short-scale output; "
            f"w_long/w_short={ratio_w:.2f} "
            f"(short {ws:.4g}, long {wl:.4g})"
        )
        if stats.zero_weight_fraction > 0:
            msg += f"; {stats.zero_weight_fraction:.2%} both-zero (black)"
        mean_res = getattr(stats, "mean_residual_confidence", None)
        if mean_res is not None:
            msg += f"; residual conf {mean_res:.2f}"
        ratio = getattr(stats, "exposure_ratio_used", None)
        if ratio is not None:
            msg += f"; r={ratio:.3f}"
        return msg

    def _format_fusion_caption_from_image(self, image: ScanImage) -> str:
        ws = float(image.merge_fusion_mean_short_weight or 0.0)
        wl = float(image.merge_fusion_mean_long_weight or 0.0)
        ratio_w = wl / max(ws, 1e-30)
        msg = (
            f"SNR / IVW — short-scale + makeup; "
            f"w_long/w_short={ratio_w:.2f} "
            f"(short {ws:.4g}, long {wl:.4g})"
        )
        zf = image.merge_fusion_zero_weight_fraction or 0.0
        if zf > 0:
            msg += f"; {zf:.2%} both-zero (black)"
        return msg

    def _fusion_stats_message(self, image: ScanImage) -> str:
        if image.merge_method != "snr":
            return ""
        if image.merge_fusion_mean_short_weight is None:
            return ""
        msg = (
            f"; SNR/IVW w_short={image.merge_fusion_mean_short_weight:.4g} "
            f"w_long={image.merge_fusion_mean_long_weight:.4g}"
        )
        zf = image.merge_fusion_zero_weight_fraction
        if zf:
            msg += f" both-zero={zf:.2%}"
        return msg

    def _on_me_pass_toggled(self, checked: bool) -> None:
        self._update_me_tabs_visible()

    def _default_dpi(self) -> int:
        data = self.ppi.currentData()
        if data is not None:
            return int(data)
        return 1800

    def _default_exposure_pair(self) -> tuple[int, int]:
        target = self._current_target()
        if target is not None:
            model = target.model
            short = int(getattr(model, "exposure_short", 14000))
            long = int(getattr(model, "exposure_long", short * 3))
            return short, long
        return 14000, 42000

    def _me_planes_ready(self) -> bool:
        scan = self._last_scan
        return (
            scan is not None
            and scan.rgb_short is not None
            and scan.rgb_long is not None
        )

    def _load_me_plane(self, which: str) -> None:
        from pyopticfilm.exceptions import PlustekError
        from pyopticfilm.image import load_rgb16_tiff

        title = "Load color short TIFF" if which == "short" else "Load color long TIFF"
        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            "",
            "TIFF (*.tif *.tiff);;All files (*.*)",
        )
        if not path:
            return
        try:
            rgb, dpi = load_rgb16_tiff(path, default_dpi=self._default_dpi())
        except (PlustekError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Load TIFF", str(exc))
            return

        exp_short, exp_long = self._default_exposure_pair()
        prev = self._last_scan
        short = prev.rgb_short if prev is not None and prev.rgb_short is not None else None
        long = prev.rgb_long if prev is not None else None

        if which == "short":
            if long is not None and long.shape != rgb.shape:
                QMessageBox.warning(
                    self,
                    "Load TIFF",
                    f"Shape mismatch: short {rgb.shape[:2]} vs long {long.shape[:2]}",
                )
                return
            short = rgb
            # Linear 8-bit preview (same as live ME short/long) so exposure
            # ratios stay visible — auto_level would stretch each plane alone.
            self.scan_view.set_rgb(short, dpi=dpi, auto_level=False)
        else:
            if short is not None and short.shape != rgb.shape:
                QMessageBox.warning(
                    self,
                    "Load TIFF",
                    f"Shape mismatch: long {rgb.shape[:2]} vs short {short.shape[:2]}",
                )
                return
            long = rgb
            self.me_long_view.set_rgb(long, dpi=dpi, auto_level=False)

        if short is None and long is None:
            return
        primary = short if short is not None else long
        self._last_scan = ScanImage(
            rgb=primary,
            dpi=dpi,
            rgb_short=short,
            rgb_long=long,
            exposure_short=exp_short,
            exposure_long=exp_long,
            merge_method=None,
            align_shift_long=None,
        )
        self._update_me_tabs_visible()
        if self._me_planes_ready():
            self._refresh_merged_preview()
            self.tabs.setCurrentWidget(self.merged_view)
        # Status mean is on the raw uint16 (not the 8-bit preview).
        mean_y = float(rgb.astype("float64").mean() / 65535.0)
        self.statusBar().showMessage(
            f"Loaded {Path(path).name} — {rgb.shape[1]}×{rgb.shape[0]} @ {dpi} dpi "
            f"(mean Y={mean_y:.3f}, linear preview)"
        )

    def _update_me_tabs_visible(self) -> None:
        has_me_result = (
            self._last_scan is not None and self._last_scan.rgb_long is not None
        )
        me = (self.me_pass.isEnabled() and self.me_pass.isChecked()) or has_me_result
        idx_long = self.tabs.indexOf(self.me_long_view)
        idx_merged = self.tabs.indexOf(self.merged_view)
        if idx_long >= 0:
            self.tabs.setTabVisible(idx_long, me)
        if idx_merged >= 0:
            self.tabs.setTabVisible(idx_merged, me)
        scan_label = "Color short" if me else "Scan"
        idx_scan = self.tabs.indexOf(self.scan_view)
        if idx_scan >= 0:
            self.tabs.setTabText(idx_scan, scan_label)

    def _on_override_hw_gate(self, checked: bool) -> None:
        if checked:
            reply = QMessageBox.warning(
                self,
                "Override safety HW gate",
                "This unlocks unverified scan/home/park pipelines against real "
                "hardware. Motors and the lamp can move on models that are not "
                "hardware-validated (scan_ready stays False).\n\n"
                "Keep a hand near the power switch. Continue only if you intend "
                "to run bring-up on a connected OpticFilm.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Ok:
                self.override_hw_gate.blockSignals(True)
                self.override_hw_gate.setChecked(False)
                self.override_hw_gate.blockSignals(False)
                return
            # Extra calib motor moves are risky on first bring-up.
            self.apply_calib.setChecked(False)
        else:
            self._worker.close_scanner()
        self._refresh_banner()

    def _on_usb_planar(self, _checked: bool) -> None:
        self._worker.close_scanner()
        self._refresh_banner()
        if self._capture is not None:
            self._decode_loaded_capture()

    def _on_open_capture(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open USB capture",
            "",
            "USBPcap (*.pcapng *.pcap);;All files (*.*)",
        )
        if not path:
            return
        try:
            self._capture = analyze_usbpcap(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Scan lab", f"Failed to parse capture:\n{exc}")
            return
        self._populate_usb_log_from_capture(self._capture)
        self._decode_loaded_capture()

    def _populate_usb_log_from_capture(self, analysis: CaptureAnalysis) -> None:
        """Replace the USB log with a collapsed transcript of the capture."""
        self._clear_usb_log()
        log_lines = format_capture_usb_log_lines(analysis)
        # One setPlainText is much faster than thousands of appendPlainText calls.
        self.usb_log.setPlainText("\n".join(log_lines))
        self._usb_sections.clear()
        # Re-scan for section dividers (CAPTURE / any others).
        doc = self.usb_log.document()
        block = doc.begin()
        while block.isValid():
            key = usb_log_section_key(block.text())
            if key is not None:
                self._usb_sections[key] = block.position()
            block = block.next()
        self._update_usb_jump_buttons()

    def _decode_loaded_capture(self) -> None:
        analysis = self._capture
        target = self._current_target()
        if analysis is None or target is None:
            return
        dpi = int(self.ppi.currentData() or target.model.resolutions_dpi[0])
        planar = self.usb_planar.isChecked()
        decode_model = model_for_capture_decode(analysis, target.model)
        lines = [
            f"Capture: {analysis.path.name}",
            f"Lab target: {target.model.model} ({target.model.asic})",
            f"Decode model: {decode_model.model} ({decode_model.asic})",
            (
                f"Packets: {len(analysis.packets)}  bulk INs: {len(analysis.bulk_ins)}  "
                f"register writes: {len(analysis.register_writes)}"
            ),
            "",
            *motor_register_diff(
                decode_model,
                analysis,
                dpi=dpi,
                crop_norm=self.prescan_view.crop_norm,
            ),
            "",
        ]
        decoded_ok = False
        decoded = None
        try:
            # Decode uses capture DPISET for width; Lab PPI is only for the diff above.
            decoded = decode_all_capture_passes(
                decode_model,
                analysis,
                planar=planar,
            )
            if decoded.prescan is not None:
                rgb, geo = decoded.prescan
                self.prescan_view.set_rgb(rgb, dpi=geo.resolution, auto_level=True)
            else:
                self.prescan_view.set_rgb(None)
            if decoded.color is not None:
                rgb, geo = decoded.color
                self.scan_view.set_rgb(rgb, dpi=geo.resolution, auto_level=True)
            else:
                self.scan_view.set_rgb(None)
            if decoded.color_me is not None:
                rgb_me, geo = decoded.color_me
                self.me_long_view.set_rgb(rgb_me, dpi=geo.resolution, auto_level=True)
                self.me_pass.setChecked(True)
                self._update_me_tabs_visible()
            else:
                self.me_long_view.set_rgb(None)
            if decoded.color is not None and decoded.color_me is not None:
                try:
                    from pyopticfilm.scan.exposure_merge import merge_exposures_result

                    short_rgb, geo = decoded.color
                    long_rgb, _ = decoded.color_me
                    merged = merge_exposures_result(short_rgb, long_rgb)
                    from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
                    from pyopticfilm.scan.pipeline import ImagePipeline

                    pipe = ImagePipeline(MODEL_8200I_SE)
                    rgb = pipe.expose_film_base(
                        merged.rgb, source="capture me preview", preserve_headroom=True
                    )
                    rgb = pipe.clamp_border_highlights(rgb)
                    self.merged_view.set_rgb(rgb, dpi=geo.resolution, auto_level=False)
                    if merged.fusion_stats is not None:
                        self.merged_view.set_caption(
                            self._format_fusion_caption(merged.fusion_stats)
                        )
                    self._update_me_tabs_visible()
                    lines.append("Merged tab: capture preview (SNR / IVW)")
                except Exception as merge_exc:  # noqa: BLE001
                    lines.append(f"Merged preview failed: {merge_exc}")
                    self.merged_view.set_rgb(None)
            else:
                self.merged_view.set_rgb(None)
            if decoded.ir is not None:
                ir_rgb, geo = decoded.ir
                plane = ir_rgb[:, :, 1]
                self.ir_view.set_gray(plane, dpi=geo.resolution)
            else:
                self.ir_view.set_rgb(None)

            lines.extend(decoded.log_lines)
            if decoded.color is not None:
                rgb, geo = decoded.color
                lines.append(
                    f"Scan tab: {rgb.shape[1]}×{rgb.shape[0]} @ {geo.resolution} dpi "
                    f"ld_shift=({geo.shift_r},{geo.shift_g},{geo.shift_b})"
                )
            if decoded.prescan is not None:
                rgb, geo = decoded.prescan
                lines.append(
                    f"Prescan tab: {rgb.shape[1]}×{rgb.shape[0]} @ {geo.resolution} dpi"
                )
            if decoded.ir is not None:
                rgb, geo = decoded.ir
                lines.append(
                    f"IR tab: {rgb.shape[1]}×{rgb.shape[0]} @ {geo.resolution} dpi"
                )
            if decoded.color_me is not None:
                rgb, geo = decoded.color_me
                lines.append(
                    f"Color long tab: {rgb.shape[1]}×{rgb.shape[0]} @ {geo.resolution} dpi"
                )
            lines.append(
                "Decode ignores the Lab PPI spinner (uses capture DPISET). "
                "Toggle USB planar RGB to re-decode without reopening the file."
            )
            if decode_model.asic != target.model.asic:
                lines.append(
                    f"Note: capture looks like {decode_model.asic}; "
                    f"used {decode_model.model} tables instead of {target.model.model}."
                )
            decoded_ok = decoded.prescan is not None or decoded.color is not None
            if decoded.color is not None:
                rgb, _ = decoded.color
                self.statusBar().showMessage(
                    f"Capture decode scan {rgb.shape[1]}×{rgb.shape[0]} planar={planar}"
                )
            elif decoded.prescan is not None:
                rgb, _ = decoded.prescan
                self.statusBar().showMessage(
                    f"Capture decode prescan {rgb.shape[1]}×{rgb.shape[0]} planar={planar}"
                )
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Decode failed: {exc}")
            lines.append("Register diff above may still help with FEEDL/LINCNT.")
            self.statusBar().showMessage(f"Capture decode failed: {exc}")
            QMessageBox.warning(self, "Scan lab", f"Could not decode image bulk:\n{exc}")
        self.capture_diff.setPlainText("\n".join(lines))
        if decoded_ok:
            if decoded.color is not None:
                self.tabs.setCurrentWidget(self.scan_view)
            elif decoded.prescan is not None:
                self.tabs.setCurrentWidget(self.prescan_view)
        else:
            self.tabs.setCurrentWidget(self.capture_diff)

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
        resolved = with_motor_acoustic(
            with_usb_planar(
                with_hw_override(
                    with_mock_mode(target, mock),
                    self.override_hw_gate.isChecked(),
                ),
                self.usb_planar.isChecked(),
            ),
            quiet_usb_pace=self.quiet_usb_pace.isChecked(),
            slow_image_slope=self.slow_image_slope.isChecked(),
        )
        self.banner.setText(device_banner(resolved))

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
        return with_motor_acoustic(
            with_usb_planar(
                with_hw_override(
                    with_mock_mode(target, mock),
                    self.override_hw_gate.isChecked(),
                ),
                self.usb_planar.isChecked(),
            ),
            quiet_usb_pace=self.quiet_usb_pace.isChecked(),
            slow_image_slope=self.slow_image_slope.isChecked(),
        )

    def _clear_scan_tabs(self) -> None:
        """Drop prior prescan/scan results so a new Prescan starts a fresh session."""
        self._last_scan = None
        self._last_prescan_dpi = None
        self.prescan_view.set_rgb(None)
        self.prescan_view.clear_crop()
        self.prescan_view.set_caption("")
        self.scan_view.set_rgb(None)
        self.scan_view.set_caption("")
        self.me_long_view.set_rgb(None)
        self.me_long_view.set_caption("")
        self.merged_view.set_rgb(None)
        self.merged_view.set_caption("")
        self.ir_view.set_rgb(None)
        self.ir_view.set_caption("")
        self._update_me_tabs_visible()
        self.tabs.setCurrentWidget(self.prescan_view)

    def _on_prescan(self) -> None:
        target = self._resolved_target()
        if target is None:
            return
        self._clear_usb_log()
        self._clear_scan_tabs()
        self.statusBar().showMessage("Prescanning…")
        self._worker.request_prescan.emit(target, self.apply_calib.isChecked())

    def _on_scan(self) -> None:
        target = self._resolved_target()
        if target is None:
            return
        dpi = int(self.ppi.currentData())
        crop = self.prescan_view.crop_norm
        if (
            self.override_hw_gate.isChecked()
            and not target.mock
            and lab_scan_needs_motor_warning(target.model, dpi=dpi, crop_norm=crop)
        ):
            frac = nonse_safe_y_fraction(target.model)
            reply = QMessageBox.warning(
                self,
                "High-PPI Scan",
                f"{dpi} dpi without a short crop can grind the motor on "
                f"unverified models.\n\n"
                f"Scan Lab will clamp travel to about {frac:.0%} of the TA "
                f"height (~{frac * float(getattr(target.model, 'y_size_ta_mm', 25)):.1f} mm). "
                "Prefer a small rubber-band crop and lower PPI for bring-up.\n\n"
                "Continue with the clamped short window?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Ok:
                return
        self._pending_crop_meta = None
        if crop is not None:
            self._pending_crop_meta = lab_crop_scan_meta(
                target.model, dpi=dpi, crop_norm=crop
            )
        self._worker.request_scan.emit(
            target,
            dpi,
            self.ir_pass.isChecked(),
            self.me_pass.isChecked(),
            crop,
            self.apply_calib.isChecked(),
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
        self.btn_jump_capture.setEnabled("CAPTURE" in self._usb_sections)

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
        self._last_prescan_dpi = image.dpi
        self.prescan_view.set_rgb(image.rgb, dpi=image.dpi)
        self.tabs.setCurrentWidget(self.prescan_view)
        self.statusBar().showMessage(
            f"Prescan {image.rgb.shape[1]}×{image.rgb.shape[0]} @ {image.dpi} dpi — drag a crop"
        )

    def _on_scan_ready(self, image: ScanImage) -> None:
        self._last_scan = image
        short = image.rgb_short if image.rgb_short is not None else image.rgb
        self.scan_view.set_rgb(short, dpi=image.dpi)
        if image.rgb_long is not None:
            self.me_long_view.set_rgb(image.rgb_long, dpi=image.dpi)
        else:
            self.me_long_view.set_rgb(None)
        if image.merge_method == "snr":
            self.merged_view.set_rgb(image.rgb, dpi=image.dpi, auto_level=False)
            if image.merge_fusion_mean_short_weight is not None:
                self.merged_view.set_caption(
                    self._format_fusion_caption_from_image(image)
                )
            else:
                self.merged_view.set_caption("Merge: SNR / IVW")
        elif image.rgb_long is not None:
            self._refresh_merged_preview()
        else:
            self.merged_view.set_rgb(None)
            self.merged_view.set_caption("")
        if image.ir is not None:
            self.ir_view.set_gray(image.ir, dpi=image.dpi)
        else:
            self.ir_view.set_rgb(None)
        self._update_me_tabs_visible()
        if image.rgb_long is not None:
            self.tabs.setCurrentWidget(self.merged_view)
        else:
            self.tabs.setCurrentWidget(self.scan_view)
        msg = f"Scan {short.shape[1]}×{short.shape[0]} @ {image.dpi} dpi"
        if image.rgb_long is not None:
            msg += "; ME long"
        if image.merge_method == "snr":
            msg += "; merged (SNR/IVW)"
            msg += self._fusion_stats_message(image)
        if image.ir is not None:
            msg += f"; IR {image.ir.shape[1]}×{image.ir.shape[0]}"
        crop_note = format_crop_status(self._pending_crop_meta)
        if crop_note:
            msg += f"; {crop_note}"
        self.statusBar().showMessage(msg)

    def _on_clear_calib_cache(self) -> None:
        reply = QMessageBox.question(
            self,
            "Clear calib cache",
            "Delete all cached ASIC shading entries on disk?\n\n"
            "The next prescan or scan will re-measure shading at home.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._worker.clear_calib_cache()

    def _on_calib_cleared(self, path: str) -> None:
        if path:
            self.statusBar().showMessage(f"Cleared calibration cache ({path})")
        else:
            self.statusBar().showMessage("No open scanner — calib cache unchanged")

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
        self.ir_pass.setEnabled(not busy and bool(
            getattr(self._current_target().model, "supports_infrared", False)
            if self._current_target()
            else False
        ))
        is_gl128 = (
            self._current_target() is not None
            and getattr(self._current_target().model, "asic", "") == "GL128"
        )
        self.me_pass.setEnabled(not busy and is_gl128)
        self.run_mock.setEnabled(not busy)
        self.override_hw_gate.setEnabled(not busy)
        self.apply_calib.setEnabled(not busy)
        self.btn_clear_calib.setEnabled(not busy)
        self.usb_planar.setEnabled(not busy)
        self.quiet_usb_pace.setEnabled(not busy)
        self.slow_image_slope.setEnabled(not busy)
        self.btn_open_capture.setEnabled(not busy)


def run() -> int:
    import sys

    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    win = ScanLabWindow()
    win.show()
    return app.exec()
