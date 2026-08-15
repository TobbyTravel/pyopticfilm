# SPDX-License-Identifier: GPL-3.0-or-later
"""GL845 AFE dichotomy (SANE genesys offset then gain) — no hardware."""

from __future__ import annotations

from pyopticfilm.asic.gl845 import Gl845
from pyopticfilm.device.model_8200i import MODEL_8200I
from pyopticfilm.scan.calib_gl128 import AfeFrontend, AfeSearchConfig
from pyopticfilm.usb.fake import FakeUsbTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol


def _gl845() -> Gl845:
    usb = FakeUsbTransport()
    # Boot path expects FESET ADI in reg 0x04.
    usb.registers[0x04] = 0x22
    proto = GenesysUsbProtocol(usb)
    asic = Gl845(proto, MODEL_8200I)
    asic._initialized = True
    asic._reg_cache = {0x04: 0x22}
    return asic


def test_adi_feset_skips_dichotomy_without_force():
    asic = _gl845()
    calls: list[AfeFrontend] = []

    def measure(fe: AfeFrontend) -> tuple[float, float, float]:
        calls.append(fe)
        return (0.0, 0.0, 0.0)

    result = asic.search_afe(method="transparency", measure=measure)
    assert calls == []
    assert asic.afe_dichotomy_applicable() is False
    assert result.gains == asic.last_afe_gains
    assert result.offsets == asic.last_afe_offsets
    # Table seed from MODEL_8200I frontend_regs
    assert asic.last_afe_gains == (0x28, 0x20, 0x28)
    assert asic.last_afe_offsets == (0x2F, 0x2D, 0x23)


def test_force_dichotomy_converges_with_scripted_strip_levels():
    """Controllable strip means (as if FakeUsb returned levelled bulk data)."""
    asic = _gl845()
    cfg = AfeSearchConfig(
        offset_target=1000,
        gain_target=20000,
        offset_max=255,
        gain_max=255,
        iterations=12,
        tolerance=80.0,
        offset_increases_mean=True,
        gain_increases_mean=True,
    )

    def measure(fe: AfeFrontend) -> tuple[float, float, float]:
        # Synthetic strip: mean ≈ offset*40 + gain*80 (chunky RGB average).
        means = []
        for c in range(3):
            means.append(fe.offsets[c] * 40.0 + fe.gains[c] * 80.0)
        return (means[0], means[1], means[2])

    result = asic.search_afe(
        method="transparency",
        measure=measure,
        config=cfg,
        force_dichotomy=True,
    )
    assert asic.last_afe_gains == result.gains
    assert asic.last_afe_offsets == result.offsets
    # Re-apply path used by image configure must keep last_* codes.
    asic.apply_frontend_for_scan(resolution=1800, method="transparency")
    assert asic.last_afe_gains == result.gains
    assert asic.last_afe_offsets == result.offsets
    for c in range(3):
        mean = result.offsets[c] * 40.0 + result.gains[c] * 80.0
        # Gain stage dominates; offset then gain should land near gain_target.
        assert abs(mean - cfg.gain_target) < 2500


def test_fake_usb_records_fe_writes_during_table_search():
    usb = FakeUsbTransport()
    usb.registers[0x04] = 0x22
    asic = Gl845(GenesysUsbProtocol(usb), MODEL_8200I)
    asic._initialized = True
    asic._reg_cache = {0x04: 0x22}
    asic.search_afe(method="transparency", resolution=1800)
    assert any(t.operation == "control_write" for t in usb.transactions)
