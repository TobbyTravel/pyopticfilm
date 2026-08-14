# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan Lab backend: mock checkbox vs real USB (no display)."""

from __future__ import annotations

import pytest

from pyopticfilm.device.model_8200i import MODEL_8200I
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.device.select import KNOWN_MODELS
from pyopticfilm.exceptions import DeviceNotFoundError
from tools.scanlab.backend import (
    LabTarget,
    lab_scan_kwargs,
    list_lab_targets,
    open_lab_scanner,
    usb_log_divider,
    usb_log_section_key,
    with_mock_mode,
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


def test_lab_scan_non_se_uses_area_not_bringup_geometry():
    kwargs = lab_scan_kwargs(MODEL_8200I, dpi=900, kind="prescan", crop_norm=None)
    assert "geometry" not in kwargs
    assert kwargs["area"] is None


def test_usb_log_section_key_from_divider():
    assert usb_log_section_key(usb_log_divider("PRESCAN 1200 dpi")) == "PRESCAN"
    assert usb_log_section_key(usb_log_divider("SCAN 1800 dpi")) == "SCAN"
    assert usb_log_section_key(usb_log_divider("IR 1800 dpi")) == "IR"
    assert usb_log_section_key("control_write type=0x40") is None
