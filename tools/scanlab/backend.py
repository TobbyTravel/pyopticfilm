# SPDX-License-Identifier: GPL-3.0-or-later
"""Open real SE or mock scanners with a recording USB wrapper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from pyopticfilm.device.protocol import FilmModel
from pyopticfilm.device.select import KNOWN_MODELS, model_for_device, model_is_scan_ready
from pyopticfilm.exceptions import DeviceNotFoundError
from pyopticfilm.scan.bringup import (
    PRESCAN_DPI,
    bringup_scan_geometry,
    crop_scan_geometry,
    image_crop_to_scan_area,
    is_opticfilm_8200i_se,
)
from pyopticfilm.scanner import Scanner
from pyopticfilm.usb.device import UsbDeviceInfo, list_devices
from pyopticfilm.usb.fake import MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol
from pyopticfilm.usb.trace import RecordingTransport, UsbTransaction, format_usb_log_line


@dataclass(frozen=True)
class LabTarget:
    """One combo-box entry: a model, optionally bound to a plugged-in USB device."""

    label: str
    model: FilmModel
    mock: bool = True
    device_id: str | None = None


def with_mock_mode(target: LabTarget, mock: bool) -> LabTarget:
    """Copy ``target`` with mock/real selected from the UI checkbox."""
    return replace(target, mock=mock)


def prescan_resolution(model: FilmModel) -> int:
    if is_opticfilm_8200i_se(model):
        return PRESCAN_DPI
    return min(int(d) for d in model.resolutions_dpi)


def lab_scan_kwargs(
    model: FilmModel,
    *,
    dpi: int,
    kind: str,
    crop_norm: tuple[float, float, float, float] | None,
) -> dict:
    """``Scanner.scan`` kwargs that stay inside the SE motor window.

    ``area=None`` on the 8200i SE uses feed2=13704, which cannot fit a full-TA
    1200 dpi LINCNT (the dialog the Lab was showing). Prescan and uncropped
    scans use the capture-safe preview window; a rubber-band crop is clamped.
    """
    if is_opticfilm_8200i_se(model):
        if kind == "prescan" or crop_norm is None:
            geometry, _ = bringup_scan_geometry(model, dpi, profile="preview_safe")
        else:
            area = image_crop_to_scan_area(model, crop_norm)
            geometry, _ = crop_scan_geometry(model, dpi, area)
        return {"resolution": dpi, "geometry": geometry, "area": None}
    area = None
    if crop_norm is not None and kind == "scan":
        area = image_crop_to_scan_area(model, crop_norm)
    return {"resolution": dpi, "area": area}


def list_lab_targets() -> list[LabTarget]:
    targets: list[LabTarget] = []
    connected_models: set[str] = set()
    try:
        devices = list_devices()
    except Exception:  # noqa: BLE001
        devices = []
    for info in devices:
        try:
            model = model_for_device(info.product_id, info.bcd_device)
        except Exception:  # noqa: BLE001, S112
            continue
        name = info.product or model.model
        targets.append(
            LabTarget(
                label=f"{name} ({info.device_id}) — connected",
                model=model,
                device_id=info.device_id,
            )
        )
        connected_models.add(model.name)
    for model in KNOWN_MODELS:
        if model.name in connected_models:
            continue
        targets.append(
            LabTarget(
                label=f"{model.model} ({model.asic})",
                model=model,
            )
        )
    return targets


def open_lab_scanner(
    target: LabTarget,
    *,
    on_usb: Callable[[UsbTransaction], None] | None = None,
) -> tuple[Scanner, RecordingTransport]:
    """Open mock USB, or a plugged-in scanner wrapped in a recording transport."""
    listener = on_usb
    if target.mock:
        rec = RecordingTransport(MockScannerTransport(), listener=listener)
        scanner = Scanner.open_fake(target.model, rec)
        return scanner, rec

    from pyopticfilm.usb.device import UsbDeviceHandle

    if not target.device_id:
        raise DeviceNotFoundError("No matching scanner is connected")
    handle = UsbDeviceHandle.open(target.device_id)
    rec = RecordingTransport(handle, listener=listener)
    scanner = Scanner(handle, GenesysUsbProtocol(rec), model=target.model)
    return scanner, rec


def usb_log_line(txn: UsbTransaction) -> str:
    return format_usb_log_line(txn)


def usb_log_divider(title: str) -> str:
    return f"======== {title} ========"


def usb_log_section_key(line: str) -> str | None:
    """Return ``PRESCAN`` / ``SCAN`` / ``IR`` from a divider line, else ``None``."""
    stripped = line.strip()
    if not (stripped.startswith("======== ") and stripped.endswith(" ========")):
        return None
    inner = stripped.removeprefix("======== ").removesuffix(" ========").strip()
    if not inner:
        return None
    return inner.split()[0]


def device_banner(target: LabTarget, info: UsbDeviceInfo | None = None) -> str:
    if target.mock:
        return (
            f"MOCK hardware — {target.model.model} ({target.model.asic}). "
            "Not a real scan; motors stay locked on physical non-SE devices."
        )
    extra = f" {info.device_id}" if info is not None else ""
    if not model_is_scan_ready(target.model):
        return (
            f"REAL hardware — {target.model.model}{extra}. "
            "scan_ready is False; the library will refuse to scan."
        )
    return f"REAL hardware — {target.model.model}{extra}"
