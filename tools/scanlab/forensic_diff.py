# SPDX-License-Identifier: GPL-3.0-or-later
"""First-divergence diff between two recorded Forensic runs.

Deliberately NOT a byte-level diff of usb_raw.jsonl - two genuinely
identical sessions never produce byte-identical raw logs (timestamps,
latencies, and OS-level jitter differ every time). This compares
decoded_events.jsonl instead: kind + semantic fields only, with response
timing available as *context* on a divergence but never as the trigger for
one. That matches the forensic report's method: successful and failed
runs are compared as a synchronized sequence of GL128 operations, and the
first meaningful divergence in that sequence is what matters - not the
first raw byte difference (see docs/hw-ref/8100v2/PROGRESS.md's account of
chasing timing hypotheses that turned out to be looking at the wrong
divergence entirely).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DivergenceKind = Literal[
    "none", "command", "register", "value", "response", "length", "fields"
]


@dataclass
class DivergenceResult:
    index: int | None  # None if no divergence found within the compared range
    kind: DivergenceKind
    a_event: dict[str, Any] | None
    b_event: dict[str, Any] | None
    a_len: int
    b_len: int
    note: str


def _load_decoded(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "decoded_events.jsonl"
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _classify(a: dict[str, Any], b: dict[str, Any]) -> tuple[DivergenceKind, str]:
    if a.get("kind") != b.get("kind"):
        return "command", f"kind {a.get('kind')!r} vs {b.get('kind')!r}"

    a_fields = a.get("fields", {})
    b_fields = b.get("fields", {})

    a_addr = a_fields.get("addr") or a_fields.get("w_index")
    b_addr = b_fields.get("addr") or b_fields.get("w_index")
    if a_addr != b_addr:
        return "register", f"address {a_addr!r} vs {b_addr!r}"

    a_val = a_fields.get("value")
    b_val = b_fields.get("value")
    if a_val != b_val:
        return "value", f"value {a_val!r} vs {b_val!r} at address {a_addr!r}"

    a_pairs = a_fields.get("pairs")
    b_pairs = b_fields.get("pairs")
    if a_pairs is not None and a_pairs != b_pairs:
        return "value", f"reg_write pairs differ: {a_pairs!r} vs {b_pairs!r}"

    if a_fields != b_fields:
        return "fields", f"{a_fields!r} vs {b_fields!r}"

    return "none", ""


def first_divergence(run_a: Path, run_b: Path) -> DivergenceResult:
    events_a = _load_decoded(run_a)
    events_b = _load_decoded(run_b)
    n = min(len(events_a), len(events_b))

    for i in range(n):
        kind, note = _classify(events_a[i], events_b[i])
        if kind != "none":
            return DivergenceResult(
                index=i,
                kind=kind,
                a_event=events_a[i],
                b_event=events_b[i],
                a_len=len(events_a),
                b_len=len(events_b),
                note=note,
            )

    if len(events_a) != len(events_b):
        shorter, longer = (
            ("a", "b") if len(events_a) < len(events_b) else ("b", "a")
        )
        extra_events = events_b if longer == "b" else events_a
        return DivergenceResult(
            index=n,
            kind="length",
            a_event=events_a[n] if n < len(events_a) else None,
            b_event=events_b[n] if n < len(events_b) else None,
            a_len=len(events_a),
            b_len=len(events_b),
            note=(
                f"run {shorter} ended after {n} events; run {longer} continues "
                f"with e.g. {extra_events[n].get('kind') if n < len(extra_events) else '?'}"
            ),
        )

    return DivergenceResult(
        index=None, kind="none", a_event=None, b_event=None,
        a_len=len(events_a), b_len=len(events_b), note="No divergence found - sequences match.",
    )


def format_divergence(result: DivergenceResult, *, label_a: str = "A", label_b: str = "B") -> str:
    lines = [
        f"Run {label_a}: {result.a_len} decoded events",
        f"Run {label_b}: {result.b_len} decoded events",
        "",
    ]
    if result.index is None:
        lines.append("No divergence found in the overlapping range.")
        return "\n".join(lines)

    lines.append(f"First divergence at decoded-event index {result.index} (kind: {result.kind})")
    lines.append(f"  {result.note}")
    lines.append("")
    lines.append(f"  {label_a}[{result.index}] = {json.dumps(result.a_event)}")
    lines.append(f"  {label_b}[{result.index}] = {json.dumps(result.b_event)}")
    return "\n".join(lines)
