# SPDX-License-Identifier: GPL-3.0-or-later
"""Load one event's raw + decoded + register-meaning view, by index, from a
run's evidence files - the data side of the "Event Inspector" panel.

decoded_events.jsonl and usb_raw.jsonl are written 1:1, same order (every
UsbTransaction produces exactly one DecodedEvent for the four operation
types that occur in practice - control_read/control_write/bulk_read/
bulk_write - see usb/decode.py), so "event index N" identifies the same
transaction in both files for a live-recorded run. Imported Wireshark runs
have no usb_raw.jsonl (bulk payloads are deliberately never read - see
forensic_pcap_import.py) - handled here as an explicit, honest "not
available" rather than an error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.scanlab.forensic_reference import explain_register, explain_status


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_decoded_events(run_dir: Path) -> list[dict[str, Any]]:
    """The full decoded_events.jsonl for a run, in order. Shared by
    forensic_milestones.py (build_milestones_for_run) and the Reference
    tab's "jump to timeline" match count — one reader instead of two."""
    return _read_jsonl(run_dir / "decoded_events.jsonl")


def _meaning_for(decoded: dict[str, Any] | None) -> str | None:
    if decoded is None:
        return None
    fields = decoded.get("fields", {})
    kind = decoded.get("kind")
    if kind == "reg_read":
        addr = fields.get("addr")
        value = fields.get("value")
        if addr == "0x101" and isinstance(value, int):
            return explain_status(value)
        if addr:
            return explain_register(addr, value)
    if kind == "reg_write":
        pairs = fields.get("pairs", [])
        meanings = [
            m
            for p in pairs
            if (m := explain_register(p.get("addr", ""), p.get("value")))
        ]
        return "; ".join(meanings) if meanings else None
    if kind == "probe_read":
        return explain_register(fields.get("w_index", ""), fields.get("value"))
    return None


def load_event(run_dir: Path, index: int) -> dict[str, Any]:
    """Returns {"raw": dict|None, "decoded": dict|None, "meaning": str|None,
    "raw_available": bool, "index": int}. Never raises for an out-of-range
    or missing-file index - returns Nones instead, so the GUI can show a
    clear empty state."""
    raw_path = run_dir / "usb_raw.jsonl"
    decoded_path = run_dir / "decoded_events.jsonl"
    raw_available = raw_path.exists()

    raw_events = _read_jsonl(raw_path) if raw_available else []
    decoded_events = _read_jsonl(decoded_path)

    raw = raw_events[index] if 0 <= index < len(raw_events) else None
    decoded = decoded_events[index] if 0 <= index < len(decoded_events) else None

    return {
        "index": index,
        "raw": raw,
        "decoded": decoded,
        "meaning": _meaning_for(decoded),
        "raw_available": raw_available,
    }


def format_event(event: dict[str, Any]) -> str:
    """One human-readable block combining all three views - what the Event
    Inspector panel actually displays."""
    lines = [f"# Event {event['index']}", ""]
    lines.append("## Decoded")
    lines.append(json.dumps(event["decoded"], indent=2) if event["decoded"] else "(no decoded event at this index)")
    lines.append("")
    lines.append("## Raw")
    if not event["raw_available"]:
        lines.append("(not available - this run has no usb_raw.jsonl, e.g. an imported Wireshark capture)")
    elif event["raw"] is None:
        lines.append("(no raw event at this index)")
    else:
        lines.append(json.dumps(event["raw"], indent=2))
    lines.append("")
    lines.append("## Meaning")
    lines.append(event["meaning"] or "(nothing known about this register/address - see Reference tab)")
    return "\n".join(lines)
