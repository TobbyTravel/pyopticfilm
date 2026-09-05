# SPDX-License-Identifier: GPL-3.0-or-later
"""One timecode format, used everywhere a ``rel_s`` (seconds since run start)
is displayed — Live timeline, milestones, anomalies, Event Inspector, and
the graphical timeline's axis/tooltips — so the same moment reads
identically across every view and can be cross-referenced by eye alone,
not just by clicking.
"""

from __future__ import annotations


def format_timecode(rel_s: float | None) -> str:
    """``12.381`` -> ``"+00:00:12.381"``. ``None`` -> ``"--:--:--.---"``."""
    if rel_s is None:
        return "--:--:--.---"
    sign = "-" if rel_s < 0 else "+"
    rel_s = abs(rel_s)
    hours, rem = divmod(rel_s, 3600.0)
    minutes, seconds = divmod(rem, 60.0)
    return f"{sign}{int(hours):02d}:{int(minutes):02d}:{seconds:06.3f}"
