# SPDX-License-Identifier: GPL-3.0-or-later
"""GL128 feeds hand off like SilverFast: power off, wait for MOTORENB to clear, then arm."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pyopticfilm.asic.gl128 as gl128_mod
from pyopticfilm.asic.gl128 import Gl128
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.device.select import create_asic
from pyopticfilm.exceptions import MotorTimeoutError
from pyopticfilm.usb.fake import MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol


class _Motor:
    """Session-04 behaviour: the probe reads done while MOTORENB stays set until power drops."""

    def __init__(self, asic: Gl128, *, stops: bool = True, idle_polls: int = 3) -> None:
        self.asic = asic
        self.stops = stops
        self.idle_polls = idle_polls
        self.enabled = False
        self.running = False
        self.polls = 0
        self.unpowered_polls = 0
        self.feeds = 0
        self.log: list[tuple[str, int]] = []
        r = asic.registers
        self.r = r
        orig_write = asic._write

        def _write(address: int, value: int) -> None:
            if address == r.REG_0x02:
                self._guard("0x02", value)
                if not value & r.MTRPWR:
                    self.unpowered_polls = 0
                    self.running = False
                self.log.append(("0x02", value))
            elif address == r.REG_START:
                self._guard("start", value)
                self.log.append(("start", value))
                self.enabled = True
                self.running = True
                self.polls = 0
                self.feeds += 1
            orig_write(address, value)

        def _write_u24(address: int, value: int) -> None:
            if address == r.REG_FEEDL:
                self.log.append(("feedl", value))

        asic._write = _write
        asic.protocol.write_u24 = MagicMock(side_effect=_write_u24)
        asic._upload_fast_slopes = MagicMock(side_effect=lambda **_k: self._slopes())
        asic.read_status_reliable = self._status
        asic._read_feed_probe = lambda: gl128_mod._FEED_PROBE_DONE if (not self.running or self.polls >= 3) else 0

    def _guard(self, what: str, value: int) -> None:
        # Dropping MTRPWR is the one write allowed on a moving motor.
        if what == "0x02" and not value & self.r.MTRPWR:
            return
        assert not self.enabled, f"{what}={value:#04x} written while MOTORENB is set"

    def _slopes(self) -> None:
        self._guard("slopes", 0)
        self.log.append(("slopes", 0))

    def _status(self):
        if self.running:
            self.polls += 1
        elif self.enabled and self.stops:
            self.unpowered_polls += 1
            if self.unpowered_polls >= self.idle_polls:
                self.enabled = False
        self.log.append(("status", int(self.enabled)))
        return SimpleNamespace(
            is_motor_enabled=self.enabled,
            is_feeding_finished=not self.running,
            is_at_home=self.feeds == 0,
            raw=0xC5 if self.enabled else 0xC4,
        )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(gl128_mod.time, "sleep", lambda *_a, **_k: None)


def _asic() -> Gl128:
    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    asic._motor_moves_enabled = True
    return asic


def test_motor_power_drops_before_the_idle_wait():
    asic = _asic()
    motor = _Motor(asic)

    asic._feed_capture(28292, timeout_s=1.0, require_motion=True, use_slow_slope=True)

    tail = motor.log[motor.log.index(("0x02", asic.registers.FASTFED)) :]
    assert tail[1] == ("feedl", 1)
    statuses = [value for kind, value in tail[2:] if kind == "status"]
    assert statuses[0] == 1
    assert statuses[-1] == 0
    assert not motor.enabled


def test_the_second_feed_arms_only_after_the_motor_is_idle():
    """_Motor asserts that no 0x02, slope upload or START lands while MOTORENB is set."""
    asic = _asic()
    motor = _Motor(asic, idle_polls=6)

    asic._feed_capture(28292, timeout_s=1.0, require_motion=True, use_slow_slope=True)
    asic._feed_capture(13704, timeout_s=1.0, require_motion=True, use_slow_slope=False)

    assert motor.feeds == 2
    second_arm = motor.log.index(("feedl", 13704))
    last_status = [e for e in motor.log[:second_arm] if e[0] == "status"][-1]
    assert last_status == ("status", 0)


def test_a_motor_that_never_stops_raises_and_the_next_feed_is_not_armed(monkeypatch):
    monkeypatch.setattr(gl128_mod, "_MOTOR_IDLE_TIMEOUT_S", 0.02)
    asic = _asic()
    motor = _Motor(asic, stops=False)

    with pytest.raises(MotorTimeoutError, match="after a 28292-step feed"):
        asic._feed_capture(28292, timeout_s=1.0, require_motion=True, use_slow_slope=True)

    assert motor.feeds == 1


def test_a_feed_refuses_to_arm_a_moving_motor(monkeypatch):
    monkeypatch.setattr(gl128_mod, "_MOTOR_IDLE_TIMEOUT_S", 0.02)
    asic = _asic()
    motor = _Motor(asic, stops=False)
    motor.enabled = True

    with pytest.raises(MotorTimeoutError, match="before arming a 13704-step feed"):
        asic._feed_capture(13704, timeout_s=1.0, require_motion=True)

    assert not any(kind in ("slopes", "start", "feedl") for kind, _ in motor.log)


def test_the_idle_wait_returns_once_the_motor_bit_clears():
    asic = _asic()
    reads = iter([True, True, False])
    asic.read_status_reliable = MagicMock(side_effect=lambda: SimpleNamespace(is_motor_enabled=next(reads), raw=0))

    asic.wait_motor_idle("before the test")

    assert asic.read_status_reliable.call_count == 3


def test_positioning_pair_still_completes_on_the_mock():
    usb = MockScannerTransport()
    asic = create_asic(GenesysUsbProtocol(usb), MODEL_8200I_SE)
    asic._motor_moves_enabled = True

    asic.position_for_full_frame_scan(scan_steps=13704)

    assert not usb.at_home
