# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan Lab forensic-tab evidence recording.

Trimmed, GUI-oriented version of the vertical slice proven against real
hardware in ``experimental/scanner_lab/session.py`` (register-level
recording, environment capture, streaming JSONL sink, decoded-events
alongside raw). Moved here (rather than importing
from ``experimental/``) because this is the version meant to actually ship
as part of Scan Lab; ``experimental/`` stays a personal scratch area and is
not a dependency of the shipped tool.

One ``ForensicRun`` per "Start recording" click in the Forensic tab. Not
tied to a scan's Prescan/Scan lifecycle - the forensic tab can record
while a live-monitor poll loop runs, independent of scanner.scan().
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = Path(__file__).resolve().parent / "runs"


def _run_git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5, check=False
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def capture_environment() -> dict[str, Any]:
    dirty = _run_git("status", "--porcelain")
    env_vars = {k: v for k, v in os.environ.items() if k.startswith("POF_")}
    backend = None
    try:
        import libusb_package

        backend = "libusb_package:" + str(type(libusb_package.get_libusb1_backend()))
    except Exception:  # noqa: BLE001
        backend = None
    return {
        "git_commit": _run_git("rev-parse", "HEAD"),
        "git_branch": _run_git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(dirty),
        "git_dirty_files": dirty.splitlines() if dirty else [],
        "python_version": sys.version,
        "platform": platform.platform(),
        "usb_backend": backend,
        "pof_env_vars": env_vars,
    }


@dataclass
class ForensicRunResult:
    outcome: str = "unknown"  # success | failure | error | unknown
    classification: str | None = None
    notes: str = ""
    extra: dict[str, Any] | None = None


class ForensicRun:
    """One Forensic-tab recording session: owns the output folder + JSONL sinks.

    Unlike the scan worker's own USB log (a scrolling text widget, discarded
    on Clear), every ForensicRun always writes to disk immediately - so an
    unexpected scanner crash mid-session still leaves the evidence bundle
    (§25 of the Scan Lab spec: "do not discard evidence because the scanner
    crashed").
    """

    def __init__(
        self,
        *,
        name: str,
        device_info: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        # Readable date/time in the folder name itself (was the compact,
        # hard-to-scan "run-20260904T182056Z") - this doubles as the run's
        # display label in the Run browser, so it needs to read at a glance.
        self.run_id = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%SZ")
        self.parameters = parameters or {}
        self.device_info = device_info
        self.out_dir = RUNS_ROOT / name / self.run_id
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._raw_fp = (self.out_dir / "usb_raw.jsonl").open("w", encoding="utf-8")
        self._decoded_fp = (self.out_dir / "decoded_events.jsonl").open("w", encoding="utf-8")
        self._n_events = 0
        self._started = datetime.now(UTC).isoformat()
        self._finished = False
        self._phase_t0: float | None = None
        self._phase_fp = (self.out_dir / "phase_markers.jsonl").open("w", encoding="utf-8")

    def record(self, txn_json: dict[str, Any], decoded_json: dict[str, Any] | None) -> None:
        self._raw_fp.write(json.dumps(txn_json) + "\n")
        self._raw_fp.flush()
        if decoded_json is not None:
            self._decoded_fp.write(json.dumps(decoded_json) + "\n")
            self._decoded_fp.flush()
        self._n_events += 1

    def mark_phase(self, label: str, details: dict[str, Any] | None = None) -> None:
        """Record a host-side "what pyopticfilm believes is happening right
        now" boundary (Layer 3, per the Scan Lab spec's layer separation) -
        e.g. from ScanWorker's existing _usb_divider() calls (PRESCAN/SCAN/
        PRIMING/...), or a GUI button press with its full settings snapshot
        (label starting "BUTTON: ..."). PROVEN, not a guess: this is the
        software's own record of what it just started doing or what the
        user clicked, not an inference from USB traffic.

        ``details`` must be JSON-serializable (plain dict/list/str/int/float/
        bool/None) - callers passing e.g. a numpy shape should convert it
        (``tuple(image.rgb.shape)``) before calling this.
        """
        t = time.monotonic()
        if self._phase_t0 is None:
            self._phase_t0 = t
        entry: dict[str, Any] = {
            "label": label,
            "t": t,
            "rel_s": t - self._phase_t0,
            "wall_clock": datetime.now(UTC).isoformat(),
        }
        if details:
            entry["details"] = details
        self._phase_fp.write(json.dumps(entry) + "\n")
        self._phase_fp.flush()

    def finish(self, result: ForensicRunResult) -> Path:
        if self._finished:
            return self.out_dir
        self._finished = True
        self._raw_fp.close()
        self._decoded_fp.close()
        self._phase_fp.close()
        finished = datetime.now(UTC).isoformat()

        manifest = {
            "name": self.name,
            "run_id": self.run_id,
            "started": self._started,
            "finished": finished,
            "parameters": self.parameters,
            "environment": capture_environment(),
            "device": self.device_info,
        }
        (self.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        result_dict = {
            "outcome": result.outcome,
            "classification": result.classification,
            "notes": result.notes,
            "extra": result.extra or {},
        }
        (self.out_dir / "result.json").write_text(json.dumps(result_dict, indent=2))

        summary = {
            "name": self.name,
            "run_id": self.run_id,
            "scanner": (self.device_info or {}).get("device_id"),
            "git_commit": manifest["environment"]["git_commit"],
            "parameters": self.parameters,
            "result": result_dict,
            "n_usb_events": self._n_events,
        }
        (self.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        md = [
            f"# Forensic run: {self.name}/{self.run_id}",
            "",
            f"- Scanner: {summary['scanner']}",
            f"- Git commit: {summary['git_commit']}",
            f"- Result: {result.outcome} ({result.classification or 'unclassified'})",
            f"- USB events recorded: {self._n_events}",
            "",
            "## Notes",
            result.notes or "(none)",
        ]
        (self.out_dir / "summary.md").write_text("\n".join(md))
        return self.out_dir


def list_runs() -> list[Path]:
    """All recorded run directories, newest first."""
    if not RUNS_ROOT.exists():
        return []
    return sorted(RUNS_ROOT.glob("*/*"), key=lambda p: p.name, reverse=True)


def export_run_zip(run_dir: Path, dest_path: Path) -> Path:
    """Bundle a run's folder as-is into a .zip at ``dest_path``.

    Plain function (no Qt) so it's directly testable — the GUI's export
    button is a thin wrapper that just picks ``dest_path`` via a file
    dialog and calls this.
    """
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in run_dir.rglob("*"):
            if file.is_file():
                zf.write(file, arcname=str(file.relative_to(RUNS_ROOT)))
    return dest_path


_BASELINE_FILE = RUNS_ROOT / "_baseline.json"


def get_baseline() -> Path | None:
    """The currently marked baseline run, or None if unset / no longer exists."""
    if not _BASELINE_FILE.exists():
        return None
    try:
        data = json.loads(_BASELINE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    path = data.get("path")
    if not path:
        return None
    candidate = Path(path)
    return candidate if candidate.exists() else None


def set_baseline(run_dir: Path) -> None:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    _BASELINE_FILE.write_text(json.dumps({"path": str(run_dir)}, indent=2))
