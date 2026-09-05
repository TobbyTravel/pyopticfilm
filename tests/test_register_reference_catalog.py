# SPDX-License-Identifier: GPL-3.0-or-later
"""Shape checks for tools/register_reference.py's catalog — not content
validation (citations are free prose, deliberately not checked against the
filesystem), just structural guarantees: every address parses, every
CONFIRMED entry is cited, every safety_note is non-empty, every scope is a
real model identity, and rendering is deterministic."""

from __future__ import annotations

from tools.register_reference import (
    BEHAVIORAL_NOTES,
    KNOWN_SCOPES,
    REGISTERS,
    Confidence,
    _addr_matches,
    render_markdown,
)


def test_every_register_addr_parses():
    # A representative target per entry's own addr spec must match itself —
    # proves _addr_matches can parse every spec used in the catalog (single,
    # range, and discrete-list forms alike).
    for entry in REGISTERS:
        first_token = entry.addr.split("/")[0]
        target = int(first_token.split("-")[0], 16)
        assert _addr_matches(entry.addr, target), entry.addr


def test_every_confirmed_register_has_a_citation():
    for entry in REGISTERS:
        if entry.confidence == Confidence.CONFIRMED:
            assert entry.citations, f"{entry.addr} ({entry.name}) is CONFIRMED with no citation"
        for bit in entry.bits:
            if bit.confidence == Confidence.CONFIRMED:
                assert bit.citations or entry.citations, f"{entry.addr} bit {bit.mask} ({bit.name})"


def test_every_confirmed_behavioral_note_has_a_citation():
    for note in BEHAVIORAL_NOTES:
        if note.confidence == Confidence.CONFIRMED:
            assert note.citations, note.topic


def test_safety_notes_are_non_empty_where_present():
    for entry in REGISTERS:
        if entry.safety_note is not None:
            assert entry.safety_note.strip(), entry.addr
    for note in BEHAVIORAL_NOTES:
        if note.safety_note is not None:
            assert note.safety_note.strip(), note.topic


def test_scopes_are_all_known():
    for entry in REGISTERS:
        for scope in entry.scope:
            assert scope in KNOWN_SCOPES, f"{entry.addr}: unknown scope {scope!r}"
    for note in BEHAVIORAL_NOTES:
        for scope in note.scope:
            assert scope in KNOWN_SCOPES, f"{note.topic}: unknown scope {scope!r}"


def test_render_markdown_is_deterministic_and_nonempty():
    first = render_markdown()
    second = render_markdown()
    assert first == second
    assert first.strip()
    assert "# Register Reference" in first


def test_v2_feedl_entry_carries_the_hardware_incident_safety_note():
    # The single most important safety fact in the catalog — regression-guard
    # that a future edit to this entry doesn't accidentally drop it.
    feedl_shared = [
        e
        for e in REGISTERS
        if e.addr == "0x3D-0x3F" and "gl128-shared" in e.scope
    ]
    assert feedl_shared, "expected a shared 0x3D-0x3F FEEDL entry"
    assert any(e.safety_note and "SLOPE_TABLE" in e.safety_note for e in feedl_shared)


def test_feed_probe_entry_warns_against_reintroducing_the_disable_override():
    probe_entries = [
        e
        for e in REGISTERS
        if e.addr == "0x21" and "plustek-opticfilm-8100-v2" in e.scope
    ]
    assert probe_entries
    assert any(
        e.safety_note and "reintroduce" in e.safety_note.lower() for e in probe_entries
    )
