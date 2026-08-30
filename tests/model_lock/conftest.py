# SPDX-License-Identifier: GPL-3.0-or-later
"""Mark every test under ``tests/model_lock/`` as frozen driver oracles."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    marker = pytest.mark.model_lock
    for item in items:
        path = str(getattr(item, "path", item.fspath)).replace("\\", "/")
        if "/model_lock/" in path:
            item.add_marker(marker)
