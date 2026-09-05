# SPDX-License-Identifier: GPL-3.0-or-later
"""Correlation-ID context for Scan Lab instrumentation.

Lets USB-level recording (``usb/trace.py``) stamp every transaction with
which high-level operation caused it, WITHOUT threading an id parameter
through every function in scan/session_gl128.py, asic/gl128.py, etc. Wrap an
existing call site:

    with correlate(operation_id="MOTOR_POSITION_0017"):
        asic.feed(...)

Nesting is allowed and expected (a scan_id contains many operation_ids); the
innermost value set for each field wins, and leaving the ``with`` block
restores whatever was set before it - so callers can nest freely without
coordinating with each other.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

_investigation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "investigation_id", default=None
)
_experiment_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "experiment_id", default=None
)
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
_scan_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("scan_id", default=None)
_operation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "operation_id", default=None
)


@dataclass(frozen=True)
class Correlation:
    investigation_id: str | None = None
    experiment_id: str | None = None
    run_id: str | None = None
    scan_id: str | None = None
    operation_id: str | None = None


def current() -> Correlation:
    """Snapshot of the correlation context active right now."""
    return Correlation(
        investigation_id=_investigation_id.get(),
        experiment_id=_experiment_id.get(),
        run_id=_run_id.get(),
        scan_id=_scan_id.get(),
        operation_id=_operation_id.get(),
    )


@contextmanager
def correlate(
    *,
    investigation_id: str | None = None,
    experiment_id: str | None = None,
    run_id: str | None = None,
    scan_id: str | None = None,
    operation_id: str | None = None,
) -> Iterator[Correlation]:
    """Set any subset of correlation fields for the duration of the ``with`` block."""
    tokens = []
    if investigation_id is not None:
        tokens.append((_investigation_id, _investigation_id.set(investigation_id)))
    if experiment_id is not None:
        tokens.append((_experiment_id, _experiment_id.set(experiment_id)))
    if run_id is not None:
        tokens.append((_run_id, _run_id.set(run_id)))
    if scan_id is not None:
        tokens.append((_scan_id, _scan_id.set(scan_id)))
    if operation_id is not None:
        tokens.append((_operation_id, _operation_id.set(operation_id)))
    try:
        yield current()
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def replace_operation(corr: Correlation, operation_id: str) -> Correlation:
    """Convenience: same run/scan, new operation_id (for a manual loop)."""
    return replace(corr, operation_id=operation_id)
