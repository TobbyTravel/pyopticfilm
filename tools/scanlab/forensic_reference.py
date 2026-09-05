# SPDX-License-Identifier: GPL-3.0-or-later
"""Static register/status reference data for the Forensic tab's "Reference"
tab and inline hints — the "status page" where expected behavior is shown.

This module is now a thin adapter over the canonical catalog in
``tools/register_reference.py`` (confidence-tagged CONFIRMED/INHERITED/
SUSPECTED/UNKNOWN, ~25-40 entries) instead of hand-maintaining its own
5-register list. The public API below (``BitRef``, ``RegisterRef``,
``STATUS_BITS``, ``KNOWN_REGISTERS``, ``KNOWN_GOOD_STATUS_VALUES``,
``explain_status``, ``explain_register``, ``_addr_matches``) is unchanged in
shape and contract, so ``forensic_tab.py``/``forensic_event_inspector.py``
need no changes beyond the richer data they now receive.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyopticfilm.asic.status import ScannerStatus
from tools.register_reference import AsicFamily, _addr_matches, entries_for, parse_addr


@dataclass(frozen=True)
class BitRef:
    bit: str
    name: str
    meaning: str
    confidence: str


@dataclass(frozen=True)
class RegisterRef:
    addr: str
    name: str
    meaning: str
    confidence: str
    scanner_scope: str
    #: Additive field (default ""), independent of confidence — a
    #: CONFIRMED register can still be hardware-dangerous. See
    #: tools/register_reference.py's module docstring.
    safety_note: str = ""


def _project_status_bits() -> list[BitRef]:
    """GL128's shared REG_STATUS entry carries the 0x101/0x41 bit table —
    project its BitEntry rows into the existing BitRef shape."""
    for entry in entries_for(asic=AsicFamily.GL128, scope="gl128-shared"):
        if entry.addr == "0x101":
            return [
                BitRef(b.mask, b.name, b.meaning, b.confidence.value.upper())
                for b in entry.bits
            ]
    return []


def _project_known_registers() -> list[RegisterRef]:
    """Every GL128 catalog entry (shared or model-specific), projected into
    the existing flat RegisterRef shape ScanLab's Reference tab builds
    tables from."""
    return [
        RegisterRef(
            e.addr,
            e.name,
            e.meaning,
            e.confidence.value.upper(),
            ", ".join(e.scope),
            e.safety_note or "",
        )
        for e in REGISTERS_GL128
    ]


REGISTERS_GL128 = entries_for(asic=AsicFamily.GL128)

# Register 0x101 (GL128) / 0x41 (GL845) — same bit layout, per gl128.py's
# own docstring ("status is at 0x101 ... though the bit layout is the same").
STATUS_BITS: list[BitRef] = _project_status_bits()

# A handful of known-idle-at-home readings observed live this session
# (8100 V2, PID 0x1824) — kept as concrete, session-verified data points,
# not a claim about every scanner/state.
KNOWN_GOOD_STATUS_VALUES = {
    0xE8: "Idle at home (8100 V2, verified live this session): replugged=no, buffer_empty=yes, "
    "feed/scan finished=yes/no, at_home=yes, lamp=off, AFE busy=no, motor=off.",
}

KNOWN_REGISTERS: list[RegisterRef] = _project_known_registers()


def explain_status(raw: int) -> str:
    """One-line, human-readable interpretation of a status_raw byte.

    Always derived live from the same ScannerStatus.from_reg41() the rest
    of the codebase uses — never a second, separately-maintained decode.
    """
    s = ScannerStatus.from_reg41(raw)
    bits = []
    bits.append("at home" if s.is_at_home else "NOT at home")
    bits.append("motor ON" if s.is_motor_enabled else "motor off")
    bits.append("lamp on" if s.is_lamp_on else "lamp off")
    if s.is_front_end_busy:
        bits.append("AFE busy")
    if s.is_replugged:
        bits.append("replugged since last check")
    known_note = KNOWN_GOOD_STATUS_VALUES.get(raw)
    text = ", ".join(bits)
    if known_note:
        return f"{text} — {known_note}"
    return text


def explain_register(addr: str, value: int | None = None) -> str | None:
    """Looks up a known register/address (range) match.
    Returns None if nothing is known about this address (an honest "we
    don't know" rather than guessing)."""
    target = parse_addr(addr)
    if target is None:
        return None
    for ref in KNOWN_REGISTERS:
        if _addr_matches(ref.addr, target):
            value_note = f" (value={hex(value)})" if value is not None else ""
            return f"{ref.name}: {ref.meaning}{value_note} [{ref.confidence}]"
    return None
