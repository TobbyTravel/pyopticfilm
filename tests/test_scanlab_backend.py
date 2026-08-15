# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan Lab backend: mock checkbox vs real USB (no display)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pyopticfilm.device.model_7400 import MODEL_8100
from pyopticfilm.device.model_8200i import MODEL_8200I
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.device.select import KNOWN_MODELS
from pyopticfilm.exceptions import DeviceNotFoundError
from tools.scanlab.backend import (
    LabTarget,
    apply_lab_decode_layout,
    apply_lab_hw_override,
    device_banner,
    lab_scan_kwargs,
    lab_scan_needs_motor_warning,
    list_lab_targets,
    nonse_safe_area,
    nonse_safe_y_fraction,
    open_lab_scanner,
    usb_log_divider,
    usb_log_section_key,
    with_hw_override,
    with_mock_mode,
    with_usb_planar,
)


def _empty_devices():
    return []


def test_list_lab_targets_without_usb(monkeypatch):
    monkeypatch.setattr("tools.scanlab.backend.list_devices", _empty_devices)
    targets = list_lab_targets()
    assert {t.model.name for t in targets} == {m.name for m in KNOWN_MODELS}
    assert all(t.mock and t.device_id is None for t in targets)


def test_with_mock_mode_selects_real():
    target = LabTarget(label="se", model=MODEL_8200I, device_id="plustek:usb:07b3:1825:001:002")
    real = with_mock_mode(target, False)
    assert real.mock is False
    assert real.device_id == target.device_id
    assert with_mock_mode(real, True).mock is True


def test_with_hw_override_sets_flag():
    target = LabTarget(label="x", model=MODEL_8200I, device_id="id")
    assert target.allow_unvalidated is False
    unlocked = with_hw_override(with_mock_mode(target, False), True)
    assert unlocked.mock is False
    assert unlocked.allow_unvalidated is True
    assert with_hw_override(unlocked, False).allow_unvalidated is False


def test_device_banner_override_vs_locked():
    locked = with_mock_mode(
        LabTarget(label="x", model=MODEL_8200I, device_id="id"),
        False,
    )
    text = device_banner(locked)
    assert "refuse to scan" in text
    assert "OVERRIDDEN" not in text

    unlocked = with_hw_override(locked, True)
    text = device_banner(unlocked)
    assert "OVERRIDDEN" in text
    assert "refuse to scan" not in text


def test_apply_lab_hw_override_real_only():
    scanner = MagicMock()
    scanner._allow_unvalidated_scan = False
    mock_target = LabTarget(label="m", model=MODEL_8200I, allow_unvalidated=True)
    apply_lab_hw_override(scanner, mock_target)
    assert scanner._allow_unvalidated_scan is False

    real_locked = with_mock_mode(
        LabTarget(label="r", model=MODEL_8200I, device_id="id"),
        False,
    )
    apply_lab_hw_override(scanner, real_locked)
    assert scanner._allow_unvalidated_scan is False

    real_unlocked = with_hw_override(real_locked, True)
    apply_lab_hw_override(scanner, real_unlocked)
    assert scanner._allow_unvalidated_scan is True


def test_apply_lab_decode_layout_sets_planar():
    scanner = MagicMock()
    asic = MagicMock()
    scanner._asic = asic
    target = with_usb_planar(LabTarget(label="x", model=MODEL_8100), True)
    apply_lab_decode_layout(scanner, target)
    assert asic.usb_planar_rgb is True
    apply_lab_decode_layout(scanner, with_usb_planar(target, False))
    assert asic.usb_planar_rgb is False


def test_open_real_without_connected_device_raises():
    target = with_mock_mode(LabTarget(label="x", model=MODEL_8200I), False)
    with pytest.raises(DeviceNotFoundError, match="connected"):
        open_lab_scanner(target)


def test_lab_prescan_se_1200_stays_in_window():
    kwargs = lab_scan_kwargs(MODEL_8200I_SE, dpi=1200, kind="prescan", crop_norm=None)
    geometry = kwargs["geometry"]
    feed2 = MODEL_8200I_SE.feed_to_scan_steps_for_area(geometry.area)
    assert feed2 == MODEL_8200I_SE.feed_to_scan_top_steps == 13128
    assert geometry.lincnt_register <= MODEL_8200I_SE.max_lincnt_for(feed2, 1200)


def test_lab_scan_se_crop_clamps_lincnt():
    kwargs = lab_scan_kwargs(
        MODEL_8200I_SE,
        dpi=1800,
        kind="scan",
        crop_norm=(0.0, 0.0, 1.0, 1.0),
    )
    geometry = kwargs["geometry"]
    feed2 = MODEL_8200I_SE.feed_to_scan_steps_for_area(geometry.area)
    assert geometry.lincnt_register <= MODEL_8200I_SE.max_lincnt_for(feed2, 1800)


def test_lab_non_se_prescan_uses_short_area():
    kwargs = lab_scan_kwargs(MODEL_8100, dpi=600, kind="prescan", crop_norm=None)
    assert "geometry" not in kwargs
    area = kwargs["area"]
    assert area is not None
    x1, y1, x2, y2 = area
    assert x1 == 0.0 and x2 == 1.0
    assert y2 - y1 <= nonse_safe_y_fraction(MODEL_8100) + 1e-9
    assert area == nonse_safe_area(MODEL_8100)


def test_lab_non_se_uncropped_scan_clamps_not_full_ta():
    kwargs = lab_scan_kwargs(MODEL_8100, dpi=3600, kind="scan", crop_norm=None)
    area = kwargs["area"]
    assert area is not None
    assert (area[3] - area[1]) <= nonse_safe_y_fraction(MODEL_8100) + 1e-9


def test_lab_non_se_tall_crop_is_clamped():
    kwargs = lab_scan_kwargs(
        MODEL_8100,
        dpi=1200,
        kind="scan",
        crop_norm=(0.0, 0.0, 1.0, 1.0),
    )
    area = kwargs["area"]
    assert area is not None
    assert (area[3] - area[1]) <= nonse_safe_y_fraction(MODEL_8100) + 1e-9


def test_lab_scan_needs_motor_warning_high_ppi():
    assert lab_scan_needs_motor_warning(MODEL_8100, dpi=3600, crop_norm=None) is True
    assert lab_scan_needs_motor_warning(MODEL_8100, dpi=600, crop_norm=None) is False
    assert lab_scan_needs_motor_warning(MODEL_8200I_SE, dpi=3600, crop_norm=None) is False


def test_usb_log_section_key_from_divider():
    assert usb_log_section_key(usb_log_divider("PRESCAN 1200 dpi")) == "PRESCAN"
    assert usb_log_section_key(usb_log_divider("SCAN 1800 dpi")) == "SCAN"
    assert usb_log_section_key(usb_log_divider("IR 1800 dpi")) == "IR"
    assert usb_log_section_key("control_write type=0x40") is None
