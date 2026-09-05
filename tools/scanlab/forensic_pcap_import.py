# SPDX-License-Identifier: GPL-3.0-or-later
"""Import a Wireshark/USBPcap .pcapng capture as a Forensic run.

This is how Scan Lab observes traffic from OTHER software (the vendor
driver, SilverFast, VueScan, ...) using the scanner: our own live path
(usb/device.py's UsbDeviceHandle) claims the USB interface exclusively via
libusb, which would fight another application for ownership of the same
device - it cannot passively "sniff" alongside another owner. USBPcap (what
Wireshark uses on Windows) taps traffic at the kernel filter driver level,
below both applications, and does not claim the interface - so the
supported workflow for "what does the OTHER software actually send" is:

  1. Start a USBPcap capture in Wireshark (or ``usbpcapcmd.exe``) on the
     scanner's device/filter before starting the other application.
  2. Run the operation in that other software.
  3. Stop the capture, save/export as .pcapng.
  4. Import it here (or point --watch at the in-progress file - see below).

Reuses ``tools/capture_ledger.py``'s streaming pcapng parser (memory-bounded
- doesn't require the whole capture in RAM even for the 400MB-1.8GB files
this project has already worked with) and converts its classified events
into the SAME decoded-event schema live Scan Lab recordings use, so the
run browser / first-divergence diff / milestone guesser all work
identically regardless of source. Bulk payload bytes are never read here
(same policy as capture_ledger.py) - only control-transfer detail plus
bulk transfer sizes/counts, which is what the milestone/diff tools need.

``--watch``: USBPcap append-writes new Enhanced Packet Blocks as capture
continues, but there's no reliable way to detect "new data since last read"
without re-parsing pcapng structure from the start (block boundaries can
appear only-partially-flushed mid-write). This polls by re-running the
full streaming parse periodically and overwriting the run's decoded
output - cheap relative to a real capture's disk I/O, and always correct,
never assumes an append offset that pcapng doesn't actually guarantee.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from tools.capture_ledger import UsbEvent, build_ledger
from tools.scanlab.forensic_session import RUNS_ROOT, capture_environment

DECODER_NAME = "wireshark_pcap_import"
DECODER_VERSION = "1"


def _to_decoded_event(ev: UsbEvent) -> dict | None:
    """Map a capture_ledger.UsbEvent onto usb/decode.py's DecodedEvent schema."""
    base = {"decoder_name": DECODER_NAME, "decoder_version": DECODER_VERSION}
    if ev.ts is not None:
        base["raw_t0"] = ev.ts

    if ev.kind == "reg_write":
        return {
            **base,
            "kind": "reg_write",
            "fields": {"pairs": [{"addr": ev.detail["addr"], "value": ev.detail["value"]}]},
            "confidence": "PROVEN",
        }
    if ev.kind == "reg_read":
        fields = {"addr": ev.detail["addr"]}
        if "value" in ev.detail:
            fields["value"] = ev.detail["value"]
        if "link_status" in ev.detail:
            fields["link_status"] = ev.detail["link_status"]
        return {**base, "kind": "reg_read", "fields": fields, "confidence": "PROVEN"}
    if ev.kind == "probe_read":
        fields = {"w_index": ev.detail["w_index"]}
        if "value" in ev.detail:
            fields["value"] = ev.detail["value"]
        return {**base, "kind": "probe_read", "fields": fields, "confidence": "PROVEN"}
    if ev.kind == "buffer_preamble":
        return {
            **base,
            "kind": "buffer_preamble",
            "fields": {
                "w_index": ev.detail["w_index"],
                "bulk_addr": ev.detail["bulk_addr"],
                "bulk_size": ev.detail["bulk_size"],
            },
            "confidence": "PROVEN",
        }
    return None  # bulk_in/bulk_out sizes are in the summary, not per-event here


def import_pcap(pcap_path: Path, *, name: str, run_id: str | None = None) -> Path:
    events, summary, _ = build_ledger(pcap_path)
    run_id = run_id or datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%SZ")
    out_dir = RUNS_ROOT / name / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    decoded = [d for ev in events if (d := _to_decoded_event(ev)) is not None]
    (out_dir / "decoded_events.jsonl").write_text(
        "\n".join(json.dumps(d) for d in decoded) + ("\n" if decoded else "")
    )
    # No usb_raw.jsonl: capture_ledger.py never retains bulk payload bytes
    # and this importer doesn't re-derive full raw USBPcap framing either -
    # decoded_events.jsonl is the complete record for this source. Recorded
    # explicitly in manifest.json so nothing downstream assumes it's there.
    (out_dir / "usb_events_raw_ledger.json").write_text(
        json.dumps([asdict(e) for e in events], indent=2)
    )

    manifest = {
        "name": name,
        "run_id": run_id,
        "source": "wireshark_pcap_import",
        "source_pcap_path": str(pcap_path),
        "has_usb_raw_jsonl": False,
        "imported_at": datetime.now(UTC).isoformat(),
        "environment": capture_environment(),
        "pcap_summary": summary,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    result = {"outcome": "imported", "classification": None, "notes": f"Imported from {pcap_path}", "extra": {}}
    (out_dir / "result.json").write_text(json.dumps(result, indent=2))

    summary_json = {
        "name": name,
        "run_id": run_id,
        "scanner": None,
        "git_commit": manifest["environment"]["git_commit"],
        "parameters": {"source_pcap": str(pcap_path)},
        "result": result,
        "n_usb_events": len(decoded),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary_json, indent=2))
    md = [
        f"# Imported Wireshark capture: {name}/{run_id}",
        "",
        f"- Source pcap: `{pcap_path}`",
        f"- Decoded events: {len(decoded)}",
        f"- Capture duration: {summary.get('duration_s')}",
        f"- Register writes: {summary.get('register_write_events')}",
        f"- Register reads: {summary.get('register_read_events')}",
        f"- Bulk IN total bytes: {summary.get('bulk_in_total_bytes')}",
        "",
        (
            "No usb_raw.jsonl for imports - bulk payload bytes are never read "
            "(same policy as tools/capture_ledger.py); decoded_events.jsonl is "
            "the complete record for this source."
        ),
    ]
    (out_dir / "summary.md").write_text("\n".join(md))
    return out_dir


def watch_pcap(
    pcap_path: Path, *, name: str, run_id: str, interval_s: float, max_iterations: int | None = None
) -> None:
    """Re-import into the SAME run_id every interval_s - see module docstring
    for why this polls-and-reparses instead of tailing an append offset."""
    i = 0
    while max_iterations is None or i < max_iterations:
        try:
            out_dir = import_pcap(pcap_path, name=name, run_id=run_id)
            print(f"[{datetime.now(UTC).isoformat()}] reimported -> {out_dir}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - keep watching even if the file is mid-write
            print(f"[{datetime.now(UTC).isoformat()}] import failed (will retry): {exc}", file=sys.stderr)
        i += 1
        if max_iterations is None or i < max_iterations:
            time.sleep(interval_s)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pcap", type=Path)
    parser.add_argument("--name", default="wireshark-import")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--watch", action="store_true", help="poll and re-import periodically")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args(argv[1:])

    if args.watch:
        run_id = args.run_id or datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%SZ")
        watch_pcap(args.pcap, name=args.name, run_id=run_id, interval_s=args.interval)
        return 0

    out_dir = import_pcap(args.pcap, name=args.name, run_id=args.run_id)
    print(f"imported -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
