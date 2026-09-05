# SPDX-License-Identifier: GPL-3.0-or-later
"""General-purpose anomaly rules over a Forensic run's decoded events.

Deliberately NOT tied to any specific open issue (#33/#43/#49) — these are
generic shape-based detectors (stale state, long gaps, prolonged motor-on,
writes to known-hazardous addresses, transport errors, unrecognized
traffic) that are useful for any future investigation. A rule firing is a
flag to look closer, never a diagnosis — every Anomaly carries the raw
evidence (event index, timestamp, values) so a human/AI can verify it
against decoded_events.jsonl / usb_raw.jsonl directly.

This is also the single source for the motion/lamp-adjacent "unsafe
address" list, previously duplicated between tools/scanlab/worker.py and
experimental/scanner_lab/live.py — both now import UNSAFE_ADDRESSES from
here instead of keeping their own copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pyopticfilm.asic.registers import Gl128Registers
from tools.register_reference import parse_addr
from tools.scanlab.forensic_timecode import format_timecode

Severity = Literal["info", "warning", "critical"]

_R = Gl128Registers()

# Addresses known to trigger physical motion, lamp, or scan-start when
# written. Shared by worker.py's regwrite guard and experimental/
# scanner_lab/live.py's guard, and used here to flag such writes even when
# explicitly allowed (force=True) — a permitted write to a hazardous
# address is still worth a low-severity note in the anomaly list.
#
# Addresses sourced from Gl128Registers (single numeric source of truth —
# see tools/register_reference.py for the fuller confidence-tagged
# catalog these overlap with, e.g. 0x3D-0x3F's documented hardware
# incident); the description text here stays scoped to "why a write is
# physically hazardous", distinct from that catalog's broader per-register
# confidence/citation record.
UNSAFE_ADDRESSES: dict[int, str] = {
    _R.REG_0x01: "GO/MOTOR bit lives in this byte on GL128/GL845-family ASICs",
    _R.REG_0x03: "lamp enable / AVEENB",
    _R.REG_FEEDL: "FEEDL region",
    _R.REG_FEEDL + 1: "FEEDL region",
    _R.REG_FEEDL + 2: "FEEDL region",
}


@dataclass
class Anomaly:
    rule_id: str
    severity: Severity
    description: str
    index: int | None
    rel_s: float | None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "description": self.description,
            "index": self.index,
            "rel_s": self.rel_s,
            "evidence": self.evidence,
        }

    def dedup_key(self) -> tuple:
        """A stable identity for "is this the same anomaly as one already
        reported", independent of description text or buffer-relative
        index. Live detection re-scans a sliding window repeatedly as new
        events arrive, so the SAME ongoing condition (a stale-status run
        that's still growing, a gap between two fixed wall-clock timestamps)
        gets re-detected on every check with a shifted index/growing count -
        keying dedup on the raw description text (as an earlier version of
        this module did) meant "repeated 20x" and "repeated 21x" looked like
        two different anomalies and neither ever got suppressed, flooding
        the live timeline with near-duplicates for the one real condition.
        Anchoring on timestamps from ``evidence`` instead is stable because
        wall-clock times of already-seen events never change, only the
        window's view of them does.
        """
        if self.rule_id == "long_gap":
            return (self.rule_id, round(self.evidence.get("t_before", 0.0), 3))
        if self.rule_id == "stale_status":
            return (
                self.rule_id,
                self.evidence.get("kind"),
                self.evidence.get("value"),
                round(self.evidence.get("t_start", 0.0), 3),
            )
        if self.rule_id == "motor_enabled_prolonged":
            return (self.rule_id, round(self.evidence.get("since_t", 0.0), 3), self.evidence.get("bucket"))
        if self.rule_id in ("unsafe_register_write", "usb_error"):
            return (self.rule_id, round(self.evidence.get("t0", 0.0), 3), self.description)
        if self.rule_id == "unclassified_traffic_spike":
            return (self.rule_id, round(self.evidence.get("window_start_t", 0.0), 3))
        return (self.rule_id, self.description)


DEFAULT_THRESHOLDS = {
    "stale_status_repeats": 20,  # consecutive identical (status,probe) samples
    "long_gap_s": 2.0,
    "motor_enabled_prolonged_s": 5.0,
    "unclassified_burst_count": 10,
    "unclassified_burst_window": 20,  # look at the last N events for the burst check
}


def _rel_s(events: list[dict[str, Any]], idx: int, t_first: float | None) -> float | None:
    t0 = events[idx].get("raw_t0")
    if t0 is None or t_first is None:
        return None
    return t0 - t_first


def detect_anomalies(
    decoded_events: list[dict[str, Any]],
    *,
    phase_markers: list[dict[str, Any]] | None = None,
    thresholds: dict[str, Any] | None = None,
) -> list[Anomaly]:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    anomalies: list[Anomaly] = []
    t_first = next((e.get("raw_t0") for e in decoded_events if e.get("raw_t0") is not None), None)

    # --- long_gap: consecutive timestamped events far apart ---
    last_t: float | None = None
    last_idx: int | None = None
    for i, ev in enumerate(decoded_events):
        t0 = ev.get("raw_t0")
        if t0 is None:
            continue
        if last_t is not None and (t0 - last_t) > th["long_gap_s"]:
            anomalies.append(
                Anomaly(
                    rule_id="long_gap",
                    severity="warning",
                    description=f"{t0 - last_t:.3f}s gap between events {last_idx} and {i} "
                    f"(threshold {th['long_gap_s']}s) — possible stall.",
                    index=i,
                    rel_s=_rel_s(decoded_events, i, t_first),
                    evidence={"gap_s": t0 - last_t, "prev_index": last_idx, "t_before": last_t, "t_after": t0},
                )
            )
        last_t, last_idx = t0, i

    # --- stale_status: identical reg_read(0x101)/probe_read repeated ---
    run_kind: str | None = None
    run_value: Any = None
    run_start: int | None = None
    run_len = 0

    def _flush_stale_run(end_idx: int) -> None:
        nonlocal run_kind, run_value, run_start, run_len
        if run_kind is not None and run_len >= th["stale_status_repeats"]:
            t_start = decoded_events[run_start].get("raw_t0") if run_start is not None else None
            anomalies.append(
                Anomaly(
                    rule_id="stale_status",
                    severity="warning",
                    description=f"{run_kind}={run_value!r} repeated {run_len}x unchanged "
                    f"(events {run_start}-{end_idx}) — possible stuck state.",
                    index=run_start,
                    rel_s=_rel_s(decoded_events, run_start, t_first) if run_start is not None else None,
                    evidence={
                        "kind": run_kind,
                        "value": run_value,
                        "count": run_len,
                        "end_index": end_idx,
                        "t_start": t_start,
                    },
                )
            )
        run_kind, run_value, run_start, run_len = None, None, None, 0

    for i, ev in enumerate(decoded_events):
        kind = ev.get("kind")
        if kind not in ("reg_read", "probe_read"):
            continue
        fields = ev.get("fields", {})
        key = (kind, fields.get("addr") or fields.get("w_index"))
        value = fields.get("value")
        if run_kind == key and run_value == value:
            run_len += 1
        else:
            _flush_stale_run(i - 1)
            run_kind, run_value, run_start, run_len = key, value, i, 1
    if run_kind is not None:
        _flush_stale_run(len(decoded_events) - 1)

    # --- motor_enabled_prolonged: consecutive reg_read(0x101) with MOTORENB bit set ---
    # One escalating alert per whole multiple of the threshold since the motor
    # FIRST went enabled (bucket = how many thresholds have elapsed) - anchored
    # on the true, never-reset start time so live re-detection (which re-walks
    # the whole rolling window on every check) reports the same bucket the
    # same way every time instead of a fresh "since" timestamp per check.
    MOTORENB = 0x01
    motor_true_start_t: float | None = None
    motor_last_bucket = -1
    for i, ev in enumerate(decoded_events):
        if ev.get("kind") != "reg_read" or ev.get("fields", {}).get("addr") != "0x101":
            continue
        value = ev.get("fields", {}).get("value")
        t0 = ev.get("raw_t0")
        motor_on = isinstance(value, int) and bool(value & MOTORENB)
        if motor_on:
            if motor_true_start_t is None:
                motor_true_start_t, motor_last_bucket = t0, -1
            elif motor_true_start_t is not None and t0 is not None:
                bucket = int((t0 - motor_true_start_t) // th["motor_enabled_prolonged_s"])
                if bucket > motor_last_bucket and bucket >= 1:
                    motor_last_bucket = bucket
                    anomalies.append(
                        Anomaly(
                            rule_id="motor_enabled_prolonged",
                            severity="critical",
                            description=f"Motor reported enabled continuously for "
                            f">{bucket * th['motor_enabled_prolonged_s']:.0f}s.",
                            index=i,
                            rel_s=_rel_s(decoded_events, i, t_first),
                            evidence={
                                "duration_s": t0 - motor_true_start_t,
                                "since_t": motor_true_start_t,
                                "bucket": bucket,
                            },
                        )
                    )
        else:
            motor_true_start_t, motor_last_bucket = None, -1

    # --- unsafe_register_write ---
    for i, ev in enumerate(decoded_events):
        if ev.get("kind") != "reg_write":
            continue
        for pair in ev.get("fields", {}).get("pairs", []):
            addr = parse_addr(pair["addr"])
            if addr is None:
                continue
            if addr in UNSAFE_ADDRESSES:
                anomalies.append(
                    Anomaly(
                        rule_id="unsafe_register_write",
                        severity="info",
                        description=f"Write to motion/lamp-adjacent register {pair['addr']} "
                        f"({UNSAFE_ADDRESSES[addr]}) = {pair['value']!r}.",
                        index=i,
                        rel_s=_rel_s(decoded_events, i, t_first),
                        evidence={"addr": pair["addr"], "value": pair["value"], "t0": ev.get("raw_t0") or 0.0},
                    )
                )

    # --- usb_error ---
    for i, ev in enumerate(decoded_events):
        if ev.get("error"):
            anomalies.append(
                Anomaly(
                    rule_id="usb_error",
                    severity="critical",
                    description=f"Transport error: {ev['error']}",
                    index=i,
                    rel_s=_rel_s(decoded_events, i, t_first),
                    evidence={"error": ev["error"], "t0": ev.get("raw_t0") or 0.0},
                )
            )

    # --- unclassified_traffic_spike ---
    window = th["unclassified_burst_window"]
    for i in range(len(decoded_events)):
        chunk = decoded_events[max(0, i - window + 1) : i + 1]
        n_unclassified = sum(1 for e in chunk if str(e.get("kind", "")).startswith("unclassified_"))
        if n_unclassified >= th["unclassified_burst_count"]:
            anomalies.append(
                Anomaly(
                    rule_id="unclassified_traffic_spike",
                    severity="warning",
                    description=f"{n_unclassified} unclassified control transfers in the last "
                    f"{len(chunk)} events — the decoder may not recognize this traffic.",
                    index=i,
                    rel_s=_rel_s(decoded_events, i, t_first),
                    evidence={
                        "count": n_unclassified,
                        "window": len(chunk),
                        "window_start_t": chunk[0].get("raw_t0") or 0.0,
                    },
                )
            )
            break  # one flag per contiguous spike is enough, not one per event in it

    return anomalies


def format_anomalies(anomalies: list[Anomaly]) -> str:
    if not anomalies:
        return "No anomalies flagged."
    lines = ["| idx | timecode | severity | rule | description |", "|---|---|---|---|---|"]
    for a in anomalies:
        lines.append(f"| {a.index} | {format_timecode(a.rel_s)} | {a.severity} | {a.rule_id} | {a.description} |")
    return "\n".join(lines)
