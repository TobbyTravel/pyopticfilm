# SPDX-License-Identifier: GPL-3.0-or-later
"""Image view with a normalized rubber-band crop."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QEvent, QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget


def rgb16_to_qimage(rgb: np.ndarray) -> QImage:
    """Convert HxWx3 uint16 to an 8-bit RGB QImage (owned copy)."""
    arr = np.asarray(rgb)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"expected HxWx3, got {arr.shape}")
    u8 = np.ascontiguousarray((arr.astype(np.uint32) >> 8).clip(0, 255).astype(np.uint8))
    h, w, _ = u8.shape
    image = QImage(u8.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return image.copy()


def gray16_to_qimage(gray: np.ndarray) -> QImage:
    """Convert HxW uint16 to an 8-bit grayscale QImage (owned copy)."""
    arr = np.asarray(gray)
    if arr.ndim != 2:
        raise ValueError(f"expected HxW, got {arr.shape}")
    u8 = np.ascontiguousarray((arr.astype(np.uint32) >> 8).clip(0, 255).astype(np.uint8))
    h, w = u8.shape
    image = QImage(u8.data, w, h, w, QImage.Format.Format_Grayscale8)
    return image.copy()


class CropImageView(QWidget):
    """Scales a scan preview and lets the user drag a crop rectangle (0..1)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._label.setMinimumSize(240, 180)
        self._label.setStyleSheet("background: #1a1a1a; color: #888;")
        self._label.setText("No image")
        self._pixmap = QPixmap()
        self._crop: tuple[float, float, float, float] | None = None
        self._drag_origin: QPoint | None = None
        self._drag_rect: QRect | None = None
        self._label.installEventFilter(self)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self._label)

    @property
    def crop_norm(self) -> tuple[float, float, float, float] | None:
        return self._crop

    def clear_crop(self) -> None:
        self._crop = None
        self._refresh()

    def set_rgb(self, rgb: np.ndarray | None) -> None:
        if rgb is None:
            self._pixmap = QPixmap()
            self._label.setPixmap(QPixmap())
            self._label.setText("No image")
            return
        self._label.setText("")
        self._pixmap = QPixmap.fromImage(rgb16_to_qimage(rgb))
        self._refresh()

    def set_gray(self, gray: np.ndarray | None) -> None:
        if gray is None:
            self.set_rgb(None)
            return
        self._label.setText("")
        self._pixmap = QPixmap.fromImage(gray16_to_qimage(gray))
        self._refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def eventFilter(self, watched, event) -> bool:
        if watched is not self._label or self._pixmap.isNull():
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position().toPoint()
            self._drag_rect = QRect(self._drag_origin, self._drag_origin)
            return True
        if event.type() == QEvent.Type.MouseMove and self._drag_origin is not None:
            self._drag_rect = QRect(self._drag_origin, event.position().toPoint()).normalized()
            self._refresh()
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self._drag_origin is not None:
            rect = QRect(self._drag_origin, event.position().toPoint()).normalized()
            self._drag_origin = None
            self._drag_rect = None
            self._crop = self._rect_to_norm(rect)
            self._refresh()
            return True
        return super().eventFilter(watched, event)

    def _image_rect(self) -> QRect:
        if self._pixmap.isNull():
            return QRect()
        lw, lh = self._label.width(), self._label.height()
        scaled = self._pixmap.scaled(
            lw,
            lh,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (lw - scaled.width()) // 2
        y = (lh - scaled.height()) // 2
        return QRect(x, y, scaled.width(), scaled.height())

    def _rect_to_norm(self, rect: QRect) -> tuple[float, float, float, float] | None:
        ir = self._image_rect()
        if ir.width() < 8 or ir.height() < 8:
            return None
        inter = rect.intersected(ir)
        if inter.width() < 4 or inter.height() < 4:
            return None
        x1 = (inter.left() - ir.left()) / ir.width()
        y1 = (inter.top() - ir.top()) / ir.height()
        x2 = (inter.right() - ir.left()) / ir.width()
        y2 = (inter.bottom() - ir.top()) / ir.height()
        return (
            max(0.0, min(1.0, x1)),
            max(0.0, min(1.0, y1)),
            max(0.0, min(1.0, x2)),
            max(0.0, min(1.0, y2)),
        )

    def _refresh(self) -> None:
        if self._pixmap.isNull():
            return
        ir = self._image_rect()
        if ir.isEmpty():
            return
        scaled = self._pixmap.scaled(
            ir.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        canvas = QPixmap(self._label.size())
        canvas.fill(QColor("#1a1a1a"))
        painter = QPainter(canvas)
        painter.drawPixmap(ir.topLeft(), scaled)
        crop_rect = self._drag_rect
        if crop_rect is None and self._crop is not None:
            x1, y1, x2, y2 = self._crop
            crop_rect = QRect(
                ir.left() + int(x1 * ir.width()),
                ir.top() + int(y1 * ir.height()),
                max(1, int((x2 - x1) * ir.width())),
                max(1, int((y2 - y1) * ir.height())),
            )
        if crop_rect is not None:
            painter.setPen(QPen(QColor("#4fc3f7"), 2))
            painter.setBrush(QColor(79, 195, 247, 40))
            painter.drawRect(crop_rect.intersected(ir))
        painter.end()
        self._label.setPixmap(canvas)
