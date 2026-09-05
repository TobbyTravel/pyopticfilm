# SPDX-License-Identifier: GPL-3.0-or-later
"""Milestone/phase "guessing system" for a Forensic run's decoded_events.jsonl.

Ported from tools/phase_segment.py (built for the pcap-ledger capture
analysis in docs/hw-ref/8100v2/) so the SAME heuristics classify traffic
regardless of source: a live Scan Lab recording, an imported Wireshark
capture, or old pcap-derived ledgers. Classification here is explicitly a
GUESS, not ground truth - every milestone below is tagged with a
confidence level and the exact register evidence it was derived from, per
this project's PROVEN/STRONG/LIKELY/SPECULATIVE/UNKNOWN convention. It
never replaces or edits the raw/decoded event it was derived from.

What's classified (identical bit meanings to phase_segment.py, same
caveats - "guess" is not "fact"):
  * buffer_preamble w_index/size -> RAM/calib, AHB upload, or IMAGE pass
    (image pass confirmed PROVEN by wIndex alone; the shading-strip vs.
    AFE-probe split within RAM/calib is a size+register heuristic, LIKELY)
  * FEEDL (0x3d-0x3f) writes with value != 1 -> a positioning feed (STRONG:
    FEEDL=1 during acquisition is a confirmed convention, see gl128.py docstring)
  * register 0x03 changes -> lamp on/off (STRONG: LAMPPWR bit, confirmed
    across every capture in this project)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pyopticfilm.asic.registers import Gl128Registers
from tools.scanlab.forensic_event_inspector import load_decoded_events
from tools.scanlab.forensic_timecode import format_timecode

_R = Gl128Registers()
#: Sourced from Gl128Registers (single source of truth for these addresses/
#: bits) instead of re-declared bare hex literals.
REG_FEEDL_ADDRS = {hex(_R.REG_FEEDL), hex(_R.REG_FEEDL + 1), hex(_R.REG_FEEDL + 2)}
REG_LAMP = hex(_R.REG_0x03)
LAMPPWR = _R.LAMPPWR


def _registers_at(events: list[dict[str, Any]], upto_index: int) -> dict[str, int]:
    regs: dict[str, int] = {}
    for i, ev in enumerate(events):
        if i > upto_index:
            break
        if ev.get("kind") != "reg_write":
            continue
        for pair in ev.get("fields", {}).get("pairs", []):
            regs[pair["addr"]] = pair["value"]
    return regs


def _hex24(regs: dict[str, int], hi: int) -> int | None:
    a, b, c = regs.get(hex(hi)), regs.get(hex(hi + 1)), regs.get(hex(hi + 2))
    if a is None or b is None or c is None:
        return None
    return (a << 16) | (b << 8) | c


def _classify_preamble(w_index: str, bulk_size: int) -> tuple[str, str]:
    """Returns (label, confidence)."""
    if w_index == "0x8":
        return "IMAGE pass", "PROVEN"
    if w_index == "0x1":
        return "AHB upload (table)", "PROVEN"
    if w_index == "0x0":
        if bulk_size < 20000:
            return f"RAM/calib: AFE probe (small, {bulk_size}B)", "LIKELY"
        return f"RAM/calib: shading strip ({bulk_size}B)", "LIKELY"
    return f"unclassified w_index={w_index}", "UNKNOWN"


def build_milestones(decoded_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One milestone per meaningful state change - not every raw event.

    Each milestone: {index, t0, rel_s, kind, label, confidence, evidence}.
    ``rel_s`` is seconds since the first event that has a timestamp (t0);
    None for sources without per-event timing (a plain pcap import without
    per-packet timestamps still gets index-ordered milestones).
    """
    t_first = next((e.get("raw_t0") for e in decoded_events if e.get("raw_t0") is not None), None)
    milestones: list[dict[str, Any]] = []

    last_lamp: int | None = None
    last_feedl: int | None = None

    for idx, ev in enumerate(decoded_events):
        t0 = ev.get("raw_t0")
        rel_s = (t0 - t_first) if (t0 is not None and t_first is not None) else None
        kind = ev.get("kind")
        fields = ev.get("fields", {})

        if kind == "buffer_preamble":
            label, confidence = _classify_preamble(
                fields.get("w_index", "?"), int(fields.get("bulk_size") or 0)
            )
            milestones.append(
                {
                    "index": idx,
                    "t0": t0,
                    "rel_s": rel_s,
                    "kind": "preamble",
                    "label": label,
                    "confidence": confidence,
                    "evidence": fields,
                }
            )
        elif kind == "reg_write":
            regs_before = _registers_at(decoded_events, idx - 1)
            for pair in fields.get("pairs", []):
                addr, val = pair["addr"], pair["value"]
                if addr in REG_FEEDL_ADDRS:
                    merged = dict(regs_before)
                    merged[addr] = val
                    feedl = _hex24(merged, 0x3D)
                    if feedl is not None and feedl != last_feedl and feedl != 1:
                        last_feedl = feedl
                        milestones.append(
                            {
                                "index": idx,
                                "t0": t0,
                                "rel_s": rel_s,
                                "kind": "feedl_write",
                                "label": f"Positioning feed: FEEDL={feedl}",
                                "confidence": "STRONG",
                                "evidence": {"feedl": feedl},
                            }
                        )
                    elif feedl == 1:
                        last_feedl = 1
                if addr == REG_LAMP and val != last_lamp:
                    last_lamp = val
                    lamp_on = bool(val & LAMPPWR)
                    milestones.append(
                        {
                            "index": idx,
                            "t0": t0,
                            "rel_s": rel_s,
                            "kind": "lamp",
                            "label": f"Lamp {'ON' if lamp_on else 'OFF'} (0x03={hex(val)})",
                            "confidence": "STRONG",
                            "evidence": {"value": hex(val)},
                        }
                    )

    return milestones


def build_milestones_for_run(run_dir: Path) -> list[dict[str, Any]]:
    return build_milestones(load_decoded_events(run_dir))


def _collapse_repeats(milestones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse consecutive milestones sharing (kind, label) into one summary
    row with a count and elapsed span - a single high-DPI image pass is
    otherwise one row per bulk chunk (hundreds to thousands). Never called
    on the raw data used elsewhere (diff, export) - display-only."""
    collapsed: list[dict[str, Any]] = []
    i = 0
    n = len(milestones)
    while i < n:
        j = i
        while j + 1 < n and milestones[j + 1]["kind"] == milestones[i]["kind"] and milestones[j + 1]["label"] == milestones[i]["label"]:
            j += 1
        if j > i:
            first, last = milestones[i], milestones[j]
            span = None
            if first["rel_s"] is not None and last["rel_s"] is not None:
                span = last["rel_s"] - first["rel_s"]
            collapsed.append(
                {
                    "index": f"{first['index']}-{last['index']}",
                    "rel_s": first["rel_s"],
                    "kind": first["kind"],
                    "label": f"{first['label']}  (×{j - i + 1}, span {span:.3f}s)" if span is not None else f"{first['label']} (×{j - i + 1})",
                    "confidence": first["confidence"],
                }
            )
        else:
            collapsed.append(milestones[i])
        i = j + 1
    return collapsed


def format_milestones(
    milestones: list[dict[str, Any]],
    *,
    phase_markers: list[dict[str, Any]] | None = None,
    collapse_repeats: bool = True,
) -> str:
    """Human-readable table, plus host-side phase durations if provided.

    ``collapse_repeats``: fold runs of identical (kind, label) milestones
    (e.g. hundreds of "IMAGE pass" bulk-chunk preambles) into one summary
    row with a count and time span - purely a display transform, the
    underlying milestone list (and decoded_events.jsonl) is untouched.
    """
    display = _collapse_repeats(milestones) if collapse_repeats else milestones
    lines = ["| idx | timecode | kind | label | confidence |", "|---|---|---|---|---|"]
    for m in display:
        rel = format_timecode(m["rel_s"])
        lines.append(f"| {m['index']} | {rel} | {m['kind']} | {m['label']} | {m['confidence']} |")
    if not milestones:
        lines.append("| - | - | (none found) | - | - |")

    if phase_markers:
        lines.append("")
        lines.append(
            "## Host-side phase/button markers (PROVEN - software's own record of what it did or "
            "what was clicked, not an inference from the wire)"
        )
        lines.append("| phase | timecode | duration_s | details |")
        lines.append("|---|---|---|---|")
        for i, marker in enumerate(phase_markers):
            dur = None
            if i + 1 < len(phase_markers):
                dur = phase_markers[i + 1]["t"] - marker["t"]
            dur_s = f"{dur:.3f}" if dur is not None else "(ongoing/last)"
            details = marker.get("details")
            details_s = json.dumps(details) if details else ""
            lines.append(
                f"| {marker['label']} | {format_timecode(marker['rel_s'])} | {dur_s} | {details_s} |"
            )
    return "\n".join(lines)
