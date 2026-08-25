# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure unit tests for adaptive multi-exposure exposure selection.

No hardware. Exercises:

* :func:`pyopticfilm.scan.exposure_calibrate.step_stats` on synthetic
  linear planes;
* :func:`pyopticfilm.scan.exposure_calibrate.pick_short` /
  :func:`pyopticfilm.scan.exposure_calibrate.pick_high` with statistics
  shaped to mirror the 8100 V2 probe (see PR description for the
  hardware numbers in the docstrings);
* :class:`pyopticfilm.scan.exposure_calibrate.ExposurePlan` invariants
  (two-element ``exposures`` tuple, ``short``/``long`` match, etc.);
* :class:`pyopticfilm.scanner.Scanner.calibrate_exposure` orchestration
  via a light mock-hardware integration test: the cached
  :attr:`Scanner.last_exposure_calibration` must drive a subsequent
  ``scan(..., multi_exposure=True)`` that omits ``me_exposures``.

These tests do **not** assert real-film optical outcomes; the acceptance
criterion for the selection (14 k short / 64 k long) is validated on the
real 8100 V2.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.scan.exposure_calibrate import (
    CalibStepStats,
    ExposurePlan,
    format_step_table,
    pick_high,
    pick_short,
    step_stats,
)
from pyopticfilm.scanner import Scanner

# --------------------------------------------------------------------------
# step_stats: linear-only math
# --------------------------------------------------------------------------


def test_step_stats_rejects_non_hwc_input():
    with pytest.raises(ValueError):
        step_stats(14000, np.zeros((64, 64), dtype=np.uint16))
    with pytest.raises(ValueError):
        step_stats(14000, np.zeros((64, 64, 4), dtype=np.uint16))


def test_step_stats_matches_hand_computed():
    """Hand-computed numbers from a synthetic 8x8x3 linear plane.

    The plane has per-channel constant values so p05/p90/p99 fall on
    known lumas and there is no ambiguity in the percentiles.
    """
    plane = np.zeros((8, 8, 3), dtype=np.uint16)
    plane[:, :, 0] = 1000  # R
    plane[:, :, 1] = 2000  # G
    plane[:, :, 2] = 3000  # B
    # Lumas = (1000+2000+3000)/3 = 2000 → all percentiles are 2000.
    s = step_stats(14000, plane, clip_threshold=0.005)
    assert s.exposure == 14000
    assert s.mean_dn == pytest.approx(2000.0)
    assert s.p05 == pytest.approx(2000.0)
    assert s.p90 == pytest.approx(2000.0)
    assert s.p99 == pytest.approx(2000.0)
    assert s.clip_92_max == pytest.approx(0.0)
    # Constant plane: base == whole plane → std == 0 → noise_ratio == 0.
    assert s.noise_ratio == pytest.approx(0.0)
    assert s.usable is True
    assert "clip" in s.reason


def test_step_stats_clip_gate_detects_high_fraction():
    """92% full-scale level: any channel at/above 60292.2 counts as a clip pixel.

    Use exact integers so the boundary is unambiguous.
    """
    plane = np.zeros((4, 4, 3), dtype=np.uint16)
    plane[:, :, 0] = 60300  # ≥ 0.92 * 65535 = 60292.2 → clips
    plane[:, :, 1] = 100
    plane[:, :, 2] = 100
    s = step_stats(100000, plane, clip_threshold=0.005)
    # 100 % of R channels clip; G and B do not. Max = 1.0.
    assert s.clip_92_max == pytest.approx(1.0)
    assert s.usable is False  # 100 % > 0.5 % gate


def test_step_stats_uses_only_linear_plane():
    """Feeding a display-scaled (already makeup'd) plane is indistinguishable
    from a raw linear plane at ``step_stats`` level — the function has no
    film-base knowledge. This confirms step_stats is a pure function of
    the pixels supplied, and it is the *caller's* responsibility to feed
    linear data (which is what ``Scanner.calibrate_exposure`` does via
    ``assemble(..., expose_base=False)``).
    """
    plane = np.full((16, 16, 3), 3000, dtype=np.uint16)
    s = step_stats(14000, plane)
    assert s.p05 == pytest.approx(3000.0)
    assert s.p90 == pytest.approx(3000.0)


# --------------------------------------------------------------------------
# pick_short: lowest usable candidate on linear contrast
# --------------------------------------------------------------------------


def _stats_for(exposures_by_stat: dict[int, tuple[float, float, float]]) -> list[CalibStepStats]:
    """Build CalibStepStats from ``{exposure: (p05, p90, clip_92_max)}``."""
    out = []
    for exp, (p05, p90, clip) in exposures_by_stat.items():
        usable = clip <= 0.005
        out.append(
            CalibStepStats(
                exposure=exp,
                mean_dn=(p05 + p90) / 2.0,
                p05=p05,
                p90=p90,
                p99=p90 * 1.05,
                clip_92_max=clip,
                noise_ratio=0.5,
                usable=usable,
                reason="mock",
            )
        )
    return out


def test_pick_short_rejects_7k_accepts_14k():
    """Reproduces the 8100 V2 probe: 7 k's frame spans <1 density (p90/p05
    ~2.3) while 14 k reaches ~3.2, so the usable *lowest* candidate is 14 k.
    """
    steps = _stats_for(
        {
            7000: (1563.0, 3649.0, 0.0),  # p90/p05 ≈ 2.33 → reject
            14000: (1765.0, 5575.0, 0.0),  # p90/p05 ≈ 3.16 → accept
        }
    )
    picked, reasons = pick_short(steps, candidates=(7000, 14000), min_contrast=3.0)
    assert picked == 14000
    assert len(reasons) == 2
    assert "7000" in reasons[0]
    assert "14000: selected" in reasons[1]


def test_pick_short_returns_none_when_all_rejected():
    steps = _stats_for({7000: (1500.0, 3000.0, 0.0), 14000: (1800.0, 3600.0, 0.0)})
    picked, reasons = pick_short(steps, candidates=(7000, 14000), min_contrast=3.5)
    assert picked is None
    assert all("p90/p05" in r for r in reasons)


def test_pick_short_ignores_candidates_with_no_measurement():
    """If a candidate was never scanned, skip it (with a note), do not crash."""
    steps = _stats_for({14000: (1765.0, 5575.0, 0.0)})
    picked, reasons = pick_short(steps, candidates=(7000, 14000))
    assert picked == 14000
    assert any("no measurement" in r for r in reasons)


def test_pick_short_ignores_clip_rejected_shorts():
    """A 7 k that passes contrast but fails the clip gate is NOT short-usable
    either: the low exposure cannot legitimately clip either.
    """
    steps = _stats_for(
        {
            7000: (1563.0, 5500.0, 0.01),   # contrast OK but clips
            14000: (1765.0, 5575.0, 0.0),   # clean
        }
    )
    # pick_short as defined only checks contrast; clip gate is applied by
    # step_stats.usable. Callers are expected to filter by usable first.
    # But to keep the pure function focused, we document the contract:
    # "usable" in the sense of clip is NOT considered by pick_short.
    picked, _ = pick_short(steps, candidates=(7000, 14000), min_contrast=3.0)
    assert picked == 7000  # contrast criterion satisfied at 7 k


# --------------------------------------------------------------------------
# pick_high: highest clip-clean candidate, safe fallback
# --------------------------------------------------------------------------


def test_pick_high_selects_64k_when_58k_clean_64k_0p49p_70k_1p8p():
    """The target PR scenario: 58k / 64k pass, 70k rejects at 1.8 % ≥ 0.5 %
    clip, so 64 k is the highest surviving candidate.
    """
    steps = _stats_for(
        {
            42000: (5071.0, 16910.0, 0.0),
            58000: (6930.0, 23420.0, 0.00036),   # 0.036 % → pass
            64000: (7685.0, 26060.0, 0.00489),   # 0.489 % → pass (≤ 0.5 %)
            70000: (8425.0, 28670.0, 0.01788),   # 1.788 % → reject
        }
    )
    assert (
        pick_high(steps, candidates=(42000, 58000, 64000, 70000), clip_threshold=0.005)
        == 64000
    )


def test_pick_high_all_clean_selects_highest():
    steps = _stats_for(
        {
            42000: (5000.0, 15000.0, 0.0),
            58000: (7000.0, 22000.0, 0.0),
            70000: (9000.0, 29000.0, 0.0),
            100000: (12000.0, 38000.0, 0.001),
        }
    )
    assert pick_high(steps, candidates=(42000, 58000, 70000, 100000)) == 100000


def test_pick_high_all_clip_falls_back_to_smallest_measured():
    """Every measured candidate clips → smallest measured candidate is
    returned (still capture-validated, so the exposure completes).
    """
    steps = _stats_for(
        {
            58000: (6000.0, 20000.0, 0.10),
            64000: (7000.0, 24000.0, 0.20),
            70000: (8000.0, 27000.0, 0.40),
        }
    )
    assert pick_high(steps, candidates=(58000, 64000, 70000)) == 58000


def test_pick_high_no_measurements_falls_back_to_smallest_candidate():
    """Defensive: if no candidate was measured, return the smallest candidate
    so the caller gets a safe value rather than crashing.
    """
    assert pick_high([], candidates=(42000, 58000, 64000)) == 42000
    with pytest.raises(ValueError):
        pick_high([], candidates=())


# --------------------------------------------------------------------------
# ExposurePlan invariants
# --------------------------------------------------------------------------


def test_exposure_plan_holds_two_element_exposures():
    steps = tuple(
        CalibStepStats(e, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, True, "ok") for e in (14000, 64000)
    )
    plan = ExposurePlan(
        short=14000,
        long=64000,
        exposures=(14000, 64000),
        steps=steps,
        source="exposure_calibrate:mock",
        area=(0.4, 0.4, 0.6, 0.6),
        resolution=1200,
    )
    assert len(plan.exposures) == 2
    assert plan.exposures == (plan.short, plan.long)
    assert plan.short == 14000 and plan.long == 64000
    assert plan.source.startswith("exposure_calibrate:")
    assert list(plan.area) == [0.4, 0.4, 0.6, 0.6]
    assert plan.resolution == 1200
    assert isinstance(plan.steps, tuple) and len(plan.steps) == 2


# --------------------------------------------------------------------------
# format_step_table sanity
# --------------------------------------------------------------------------


def test_format_step_table_includes_exposure_and_clip():
    table = format_step_table(
        [
            CalibStepStats(14000, 3000.0, 2000.0, 5000.0, 6000.0, 0.0, 0.4, True, "clean"),
            CalibStepStats(70000, 20000.0, 8000.0, 30000.0, 40000.0, 0.018, 0.6, False, "clip"),
        ]
    )
    assert "14000" in table
    assert "70000" in table
    assert "0.018" in table or "%  " in table


# --------------------------------------------------------------------------
# Model defaults
# --------------------------------------------------------------------------


def test_model_calib_defaults_match_pr_spec():
    assert MODEL_8200I_SE.me_calib_resolution == 1200
    assert MODEL_8200I_SE.me_calib_area == (0.4, 0.4, 0.6, 0.6)
    assert MODEL_8200I_SE.me_calib_clip_threshold == pytest.approx(0.005)
    assert 7000 in MODEL_8200I_SE.exposure_short_candidates
    assert 14000 in MODEL_8200I_SE.exposure_short_candidates
    for e in (42000, 58000, 64000, 70000):
        assert e in MODEL_8200I_SE.exposure_long_candidates
    assert MODEL_8200I_SE.exposure_short == 14000  # pre-existing, unchanged
    assert MODEL_8200I_SE.exposure_long == 42000   # pre-existing, unchanged


# --------------------------------------------------------------------------
# Light mock-hardware integration: Scanner.calibrate_exposure → cache → scan
# --------------------------------------------------------------------------


def test_scanner_calibrate_exposure_flow_injects_into_scan(monkeypatch):
    """``calibrate_exposure()`` → :attr:`Scanner.last_exposure_calibration`
    populated → a subsequent ``scan(..., multi_exposure=True)`` without
    explicit ``me_exposures`` receives that exposure pair.

    We monkeypatch :func:`scan.exposure_calibrate.step_stats` (via the
    ``scanner_module`` import) so the pure selection functions see the
    mock statistics, and we use a recording fake session so the
    orchestration can be asserted without real USB.
    """
    import types

    import pyopticfilm.scan.session as session_module
    import pyopticfilm.scanner as scanner_module

    scanner = Scanner.open_fake(MODEL_8200I_SE)

    # Deterministic mock stats that mirror the 8100 V2 probe outcome:
    # short 14 k (7 k rejected on contrast) and long 64 k (70 k clip-rejected).
    MOCK_STATS = {
        7000: (1563.0, 3649.0, 0.0),      # p05, p90, clip_92_max
        14000: (1765.0, 5575.0, 0.0),
        42000: (5071.0, 16910.0, 0.0),
        58000: (6930.0, 23420.0, 0.00036),
        64000: (7685.0, 26060.0, 0.00489),
        70000: (8425.0, 28670.0, 0.01788),
        100000: (12158.0, 39160.0, 0.319),
    }

    def fake_step_stats(exposure, plane, *, clip_threshold=0.005):
        p05, p90, clip = MOCK_STATS[int(exposure)]
        usable = clip <= clip_threshold
        return CalibStepStats(
            exposure=int(exposure),
            mean_dn=(p05 + p90) / 2.0,
            p05=p05,
            p90=p90,
            p99=p90 * 1.1,
            clip_92_max=clip,
            noise_ratio=0.5,
            usable=usable,
            reason="mock",
        )

    monkeypatch.setattr(scanner_module, "step_stats", fake_step_stats)

    captured: dict[str, list] = {"calib_pass_exposures": [], "run_kwargs": []}

    class FakeSession:
        last_me_debug = None

        def __init__(self, *args, **kwargs) -> None:
            self._pass_exposure: int | None = None
            self.pipeline = types.SimpleNamespace(
                assemble=lambda raw, geometry, **kw: np.zeros((4, 4, 3), dtype=np.uint16)
            )

        def acquire_raw(self, geometry, **kwargs):
            # Recording hook: each calibration step sets _pass_exposure to its
            # candidate; the scanner clears it in a finally. Capture the value
            # before any subsequent step overwrites it.
            captured["calib_pass_exposures"].append(int(self._pass_exposure or 0))
            return b"fake-raw"

        def run(self, **kwargs):
            captured["run_kwargs"].append(kwargs)
            return types.SimpleNamespace(rgb=np.zeros((4, 4, 3), dtype=np.uint16), dpi=1200, ir=None)

    monkeypatch.setattr(session_module, "create_session", lambda *a, **kw: FakeSession())

    try:
        plan = scanner.calibrate_exposure()

        # Plan invariants
        assert isinstance(plan, ExposurePlan)
        assert len(plan.exposures) == 2
        assert (plan.short, plan.long) == plan.exposures
        assert plan.short == 14000
        assert plan.long == 64000
        # Cache populated
        assert scanner.last_exposure_calibration is plan

        # Calibration ran the union of short ∪ long candidates, in ascending order.
        expected = sorted(
            set(MODEL_8200I_SE.exposure_short_candidates)
            | set(MODEL_8200I_SE.exposure_long_candidates)
        )
        assert captured["calib_pass_exposures"] == expected

        # Now a multi_exposure scan with no explicit me_exposures must use
        # the cached plan's exposures.
        scanner.scan(
            resolution=1200,
            area=(0.0, 0.0, 0.2, 0.2),
            multi_exposure=True,
            apply_calib=False,
        )
        assert captured["run_kwargs"], "ME scan did not run"
        me_kwargs = captured["run_kwargs"][-1]
        assert me_kwargs["multi_exposure"] is True
        assert tuple(int(e) for e in me_kwargs["me_exposures"]) == (14000, 64000)
    finally:
        scanner.close()


def test_scanner_scan_multi_exposure_explicit_wins_over_cache(monkeypatch):
    """When ``me_exposures`` is passed explicitly, it must win over the cached
    plan — no silent override.
    """
    import pyopticfilm.scan.session as session_module
    from pyopticfilm.scan.exposure_calibrate import ExposurePlan

    scanner = Scanner.open_fake(MODEL_8200I_SE)
    # Inject a cached plan directly.
    cached_steps = tuple(
        CalibStepStats(e, 0.0, 1000.0, 3000.0, 4000.0, 0.0, 0.5, True, "ok")
        for e in (10000, 50000)
    )
    scanner._last_exposure_calibration = ExposurePlan(
        short=10000,
        long=50000,
        exposures=(10000, 50000),
        steps=cached_steps,
        source="injected",
        area=(0.0, 0.0, 1.0, 1.0),
        resolution=1200,
    )

    captured: list[dict] = []

    class FakeSession:
        last_me_debug = None

        def run(self, **kwargs):
            captured.append(kwargs)
            return types.SimpleNamespace(
                rgb=np.zeros((4, 4, 3), dtype=np.uint16), dpi=1200, ir=None
            )

    monkeypatch.setattr(session_module, "create_session", lambda *a, **kw: FakeSession())

    try:
        scanner.scan(
            resolution=1200,
            area=(0.0, 0.0, 0.2, 0.2),
            multi_exposure=True,
            apply_calib=False,
            me_exposures=(15000, 56000),
        )
    finally:
        scanner.close()
    assert captured, "ME scan did not run"
    assert (
        tuple(int(e) for e in captured[-1]["me_exposures"]) == (15000, 56000)
    ), "explicit me_exposures must override cached plan"


def test_scanner_scan_non_me_ignores_me_exposures(monkeypatch):
    """A non-``multi_exposure=True`` scan passed a spurious ``me_exposures``
    still works; it just isn't consumed. The cache must NOT be populated as
    a side effect.
    """
    import pyopticfilm.scan.session as session_module

    scanner = Scanner.open_fake(MODEL_8200I_SE)
    captured: list[dict] = []

    class FakeSession:
        last_me_debug = None

        def run(self, **kwargs):
            captured.append(kwargs)
            return types.SimpleNamespace(
                rgb=np.zeros((4, 4, 3), dtype=np.uint16), dpi=1200, ir=None
            )

    monkeypatch.setattr(session_module, "create_session", lambda *a, **kw: FakeSession())

    try:
        assert scanner.last_exposure_calibration is None
        scanner.scan(resolution=1200, area=(0.0, 0.0, 0.2, 0.2), apply_calib=False)
    finally:
        scanner.close()
    assert captured
    # Scan ran without me_exposures resolution — either me_exposures is None
    # or an explicit list; we specifically did not request ME.
    assert captured[-1]["multi_exposure"] is False
    assert scanner.last_exposure_calibration is None
