# SPDX-License-Identifier: GPL-3.0-or-later
"""python -m tools.scanlab"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root on sys.path when launched as a file.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))


def main() -> int:
    try:
        from tools.scanlab.app import run
    except ImportError as exc:
        missing = "PyQt6" in str(exc) or getattr(exc, "name", "") == "PyQt6"
        if missing:
            print(
                "Scan lab needs PyQt6. From the repo root:\n"
                "  uv sync --all-groups --extra lab\n"
                "  uv run python -m tools.scanlab",
                file=sys.stderr,
            )
            return 1
        raise
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
