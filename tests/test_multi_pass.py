# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct tests of the multi-pass (N-pass) ME merge vs single-pass and 2-pass ME.

These are pure/synthetic (no scanner hardware, no OpenCV dependency) and fixed-seed
so they are deterministic. They cover the three regimes the feature advertises::

    * N = 1  → single pass (returned as-is, no fusion)
    * N = 2  → classic ME short+long (must stay byte-identical to the old path)
    * N >= 3 → new streaming inverse-variance merge + cross-pass dispersion gate

Run::

    env -u PYTHONPATH uv run --all-groups pytest tests/test_multi_pass.py -v
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE as MODEL
from pyopticfilm.exceptions import ScanError
from pyopticfilm.scan.exposure_merge import (
    merge_exposures_result,
    reduce_passes,
)
from pyopticfilm.scan.me_debug import NPassDebug

EXP_SHORT = MODEL.exposure_short  # 14000
EXP_LONG = MODEL.exposure_long  # 42000
# The session reads these with getattr(..., default=...); assert the defaults the
# GL128 path actually uses so a model regression can't silently change the merge.
ALPHA = 1.0
BETA = 4096.0


# --- synthetic scene + pass harness (shared with the export script) -------------


def make_truth(h: int = 112, w: int = 144) -> np.ndarray:
    """A believable linear short-exposure-scale ground truth (grad + warm/cool bias)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    base = 2500 + (xx / w) * 14000 + (yy / h) * 7000
    base = np.clip(base, 0, 60000)[..., None]
    return np.repeat(base, 3, axis=2)


def draw_pass(exp: int, truth: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Simulate one raw colour pass at USB-exposure ``exp`` (Poisson-Gaussian noise)."""
    rr = exp / EXP_SHORT
    sig = np.sqrt(ALPHA * (truth * rr) + BETA)
    noisy = truth * rr + rng.normal(0.0, sig.mean(), truth.shape)
    return np.clip(noisy, 0, 65535).astype(np.uint16)


def shadow_rmse(plane: np.ndarray, truth: np.ndarray, frac: float = 0.30) -> float:
    """RMSE versus truth over the darkest ``frac`` of the plane (dense-film shadows)."""
    plane = plane.astype(np.float64)
    lum = plane.mean(axis=2)
    mask = lum <= np.percentile(lum, frac * 100)
    return float(np.sqrt(np.mean((plane[mask] - truth[mask]) ** 2)))


# --- model helpers ------------------------------------------------------------


def test_exposure_ladder_two_is_classic_me():
    assert MODEL.exposure_ladder(2) == (EXP_SHORT, EXP_LONG)


@pytest.mark.parametrize(
    "n, n_short, n_long",
    [(3, 2, 1), (4, 2, 2), (5, 3, 2), (6, 3, 3), (8, 4, 4), (9, 5, 4)],
)
def test_exposure_ladder_split(n: int, n_short: int, n_long: int):
    ladder = MODEL.exposure_ladder(n)
    assert len(ladder) == n
    assert ladder.count(EXP_SHORT) == n_short
    assert ladder.count(EXP_LONG) == n_long
    # Every value is one of the two *captured* exposure bins (no unvalidated clock).
    assert set(ladder) <= {EXP_SHORT, EXP_LONG}
    # For N >= 2 there is always at least one long pass.
    assert EXP_LONG in ladder


def test_exposure_ladder_clamps_to_model_bounds():
    # Below/ above the model's min/max clamp into [2, 9].
    assert len(MODEL.exposure_ladder(1)) <= 9
    assert len(MODEL.exposure_ladder(2)) == 2
    assert len(MODEL.exposure_ladder(99)) == MODEL.max_passes


def test_validate_exposures_accepts_valid_and_rejects_out_of_range():
    assert MODEL.validate_exposures([EXP_SHORT, EXP_LONG, EXP_SHORT, EXP_LONG]) == (
        EXP_SHORT,
        EXP_LONG,
        EXP_SHORT,
        EXP_LONG,
    )
    with pytest.raises(ScanError):
        MODEL.validate_exposures([EXP_SHORT, EXP_LONG + 1])  # above the validated ceiling
    with pytest.raises(ScanError):
        MODEL.validate_exposures([0])
    with pytest.raises(ScanError):
        MODEL.validate_exposures([])


# --- dispatch / parity --------------------------------------------------------


def test_reduce_passes_single_is_passthrough_uint16():
    truth = make_truth()
    rng = np.random.default_rng(11)
    plane = draw_pass(EXP_SHORT, truth, rng)
    res = reduce_passes([plane], [EXP_SHORT])
    assert res.rgb.dtype == np.uint16
    # Single pass must be returned unmodified (values preserved).
    assert np.array_equal(res.rgb, plane)
    assert res.base_exposure == EXP_SHORT
    assert res.per_pass_mean_weight == (1.0,)
    assert res.mean_gate == 1.0
    assert res.zero_weight_fraction == 0.0


def test_reduce_passes_two_is_byte_identical_to_classic_me():
    """N=2 must produce the *exact* classic ME frame (forward and reversed order)."""
    truth = make_truth()
    rng = np.random.default_rng(12)
    short = draw_pass(EXP_SHORT, truth, rng)
    long = draw_pass(EXP_LONG, truth, rng)

    via_reduce = reduce_passes([short, long], [EXP_SHORT, EXP_LONG]).rgb
    classic = merge_exposures_result(short, long).rgb  # same self-estimate shift path
    assert np.array_equal(via_reduce, classic), "N=2 must be byte-identical to classic ME"

    # Reversed plane/exposure order must route long correctly and match the classic.
    via_reversed = reduce_passes([long, short], [EXP_LONG, EXP_SHORT]).rgb
    assert np.array_equal(via_reversed, classic)


def test_reduce_passes_two_weights_sum_to_one_and_favor_long_in_midtone():
    truth = make_truth(32, 32)
    rng = np.random.default_rng(13)
    short = draw_pass(EXP_SHORT, truth, rng)
    long = draw_pass(EXP_LONG, truth, rng)
    res = reduce_passes([short, long], [EXP_SHORT, EXP_LONG])
    total = sum(res.per_pass_mean_weight)
    assert abs(total - 1.0) < 1e-9
    # Midtones: long carries a lower model variance → larger share of the IVW weight.
    assert res.per_pass_mean_weight[1] > res.per_pass_mean_weight[0]


def test_reduce_passes_three_plus_gives_uint16_frame():
    truth = make_truth()
    rng = np.random.default_rng(14)
    ladder = list(MODEL.exposure_ladder(4))
    planes = [draw_pass(e, truth, rng) for e in ladder]
    res = reduce_passes(planes, ladder)
    assert res.rgb.shape == planes[0].shape
    assert res.rgb.dtype == np.uint16
    assert res.base_exposure == EXP_SHORT
    assert len(res.per_pass_mean_weight) == len(ladder)
    assert abs(sum(res.per_pass_mean_weight) - 1.0) < 1e-9
    assert res.exposures == tuple(ladder)


def test_reduce_passes_rejects_mismatched_lengths():
    plane = make_truth(8, 8).astype(np.uint16)
    with pytest.raises(ValueError):
        reduce_passes([plane, plane], [EXP_SHORT, EXP_LONG, EXP_SHORT])
    with pytest.raises(ValueError):
        reduce_passes([], [])


# --- noise-reduction: the whole point of multi-pass ---------------------------


@pytest.mark.parametrize("n_passes, label", [(1, "single"), (4, "n4"), (6, "n6")])
def _fused(plane_truth, n_passes, seed):
    rng = np.random.default_rng(seed)
    ladder = [EXP_SHORT] if n_passes == 1 else list(MODEL.exposure_ladder(n_passes))
    planes = [draw_pass(e, plane_truth, rng) for e in ladder]
    return reduce_passes(planes, ladder).rgb


def test_noise_reduces_monotonically_with_more_passes():
    """Shadow RMSE must strictly improve as the pass budget grows (N=1 > N=2 > N=4 > N=6)."""
    truth = make_truth()
    seed = 100
    r1 = _fused(truth, 1, seed)
    r2 = _fused(truth, 2, seed)
    r4 = _fused(truth, 4, seed)
    r6 = _fused(truth, 6, seed)
    e1 = shadow_rmse(r1, truth)
    e2 = shadow_rmse(r2, truth)
    e4 = shadow_rmse(r4, truth)
    e6 = shadow_rmse(r6, truth)
    # Each stage is measurably cleaner in the shadows (the dense-film use case).
    assert e1 > e2 > e4 > e6, (e1, e2, e4, e6)
    # Sanity: multi-pass is a real win over the noisy single pass, not a washout.
    assert e6 < e1 * 0.8
    # And it tracks truth (not crushed toward black).
    assert float(r6.astype(np.float64).mean()) > 0.5 * float(truth.mean())


def test_n_pass_beats_n2_for_same_budget():
    """Replicating the bracket for extra per-side SNR must not be worse in the shadows
    than a single 2-pass ME drawn from the same scene/noise model (and a real 2-pass
    is better than a single pass)."""
    truth = make_truth()
    single = shadow_rmse(_fused(truth, 1, 200), truth)
    me2 = shadow_rmse(_fused(truth, 2, 200), truth)
    n4 = shadow_rmse(_fused(truth, 4, 200), truth)
    # 4-pass must beat the plain single pass, and not trail the 2-pass ME meaningfully.
    assert n4 < single
    assert n4 <= me2 + 1e-6


# --- dispersion gate (N >= 3 path only) ---------------------------------------


def test_dispersion_gate_suppresses_disagreed_pixels():
    """2/3 passes agree (4000) but 1 is wildly off (32000) in the right half → the
    gate must suppress the disagreed region while keeping the agreed region."""
    h = w = 16
    p1 = np.full((h, w, 3), 4000, np.uint16)
    p2 = np.full((h, w, 3), 4000, np.uint16)
    p3 = np.full((h, w, 3), 4000, np.uint16)
    p3[:, w // 2 :] = 32000  # only the right half disagrees
    res = reduce_passes([p1, p2, p3], [EXP_SHORT, EXP_SHORT, EXP_SHORT])
    out = res.rgb.astype(np.float64)
    left = float(out[:, : w // 2].mean())
    right = float(out[:, w // 2 :].mean())
    assert abs(left - 4000.0) < 1.0  # agreed region preserved
    assert right < left  # disagreed region pulled down (gate fired)
    assert res.mean_gate < 1.0  # the gate must actually have engaged
    # The outlier pass should carry a lower share of the IVW weight.
    wts = res.per_pass_mean_weight
    assert wts.index(min(wts)) == 2


def test_dispersion_gate_clean_field_full_weight():
    """Three consistent passes → gate is full open and output equals the common level."""
    h = w = 16
    plane = np.full((h, w, 3), 4000, np.uint16)
    res = reduce_passes([plane.copy() for _ in range(3)], [EXP_SHORT] * 3)
    assert abs(float(res.rgb.mean()) - 4000.0) < 1.0
    assert res.mean_gate == 1.0


# --- NPassDebug parity with the MeScanDebug contract --------------------------


def test_npassdebug_field_parity():
    short = np.zeros((4, 4, 3), np.uint16)
    long = np.full((4, 4, 3), 65535, np.uint16)
    dbg = NPassDebug(planes=(short, long, short), exposures=(EXP_SHORT, EXP_LONG, EXP_SHORT))
    assert dbg.rgb_short is short
    assert dbg.rgb_long is long
    assert dbg.exposure_short == EXP_SHORT
    assert dbg.exposure_long == EXP_LONG
    # A 3-pass diag still exposes the classic two fields for existing consumers.
    assert len(dbg.planes) == 3


# --- public API surface guards -----------------------------------------------


def test_scanner_scan_exposes_passes_and_exposures():
    """The public Scanner.scan() must accept the multi-pass API (no hardware needed)."""
    from pyopticfilm.scanner import Scanner

    sig = inspect.signature(Scanner.scan)
    params = set(sig.parameters)
    assert "multi_exposure" in params
    assert "passes" in params
    assert "exposures" in params
    # Both new params default to None (back-compatible: classic path untouched).
    assert sig.parameters["passes"].default is None
    assert sig.parameters["exposures"].default is None


def test_model_ir_capability_flags():
    from pyopticfilm.device.model_8100_v2 import MODEL_8100_V2

    assert MODEL.supports_infrared is True
    assert MODEL_8100_V2.supports_infrared is False  # 8100 V2 has no IR channel


def test_8100v2_inherits_me_constants():
    from pyopticfilm.device.model_8100_v2 import MODEL_8100_V2

    assert MODEL_8100_V2.exposure_short == EXP_SHORT
    assert MODEL_8100_V2.exposure_long == EXP_LONG
    assert MODEL_8100_V2.max_exposure == EXP_LONG
    # Inherited helpers work off the SE capture-validated exposures.
    assert set(MODEL_8100_V2.exposure_ladder(4)) <= {EXP_SHORT, EXP_LONG}
