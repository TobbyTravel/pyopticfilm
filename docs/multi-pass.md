# Multi-pass ME on GL128 (N = 3..9)

A **multi-pass multi-exposure scan** is the classic short+long bracket, *repeated*
for extra per-side SNR. Each colour pass is taken at one of the two *capture-
validated* exposures (`exposure_short=14000` or `exposure_long=42000` — the two
bins the device pixel-clock table was actually measured against). The merge is a
streaming inverse-variance (IVW) fusion over the shortest-exposure radiometric
scale, plus a cross-pass *dispersion gate* that softly suppresses pixels where
the passes disagree.

`multi_exposure=True` is a back-compat shortcut for **N = 2**; the multi-pass
feature generalises to any N in `[min_passes, max_passes]` (default 2..9).

## Public API

```python
scanner.scan(
    resolution=1800,
    mode="color",
    multi_exposure=True,             # N=2  (back-compat; classic bracket)
)
scanner.scan(
    resolution=1800,
    mode="color",
    passes=4,                        # N=4  (2 short + 2 long)
)
scanner.scan(
    resolution=1800,
    mode="color",
    exposures=[14000, 14000, 42000], # explicit ladder (validated against max_exposure)
)
```

Precedence if more than one is supplied: `exposures` > `passes` > `multi_exposure`.
All three route to the same code path in `Gl128ScanSession`.

`Scanner.last_me_debug` holds either a classic `MeScanDebug` (N=2) or an
`NPassDebug` (N>=3). `NPassDebug` exposes the full plane list (`planes`) and the
classic `rgb_short` / `rgb_long` / `exposure_short` / `exposure_long` parity
fields (the first pass at each exposure class) so existing consumers keep working.

## Exposure ladder / hardware validation

```python
model.exposure_ladder(n_passes)   # validated ladder for N
model.max_exposure                # ceiling, above this raises ScanError
model.validate_exposures([...])   # user-supplied ladder within [1, max_exposure]
```

The ladder is **short-majority**: `n_short = (n + 1) // 2`, remainder long.
This keeps the *short* exposure count (which dominates IVW weight at midtones,
because the short-pass confidence and Poisson noise are lower) at or above the
long count. Every entry is exactly one of the two capture-validated exposures —
no new pixel-clock bin is ever reached, so the hardware risk surface is
unchanged versus the classic 2-pass bracket.

For N=2 the ladder is exactly `(short, long)`, and the merge calls the
byte-identical classic path (`merge_exposures_result`).

## Merge math (N >= 3)

`NPassMerger` in `pyopticfilm/scan/exposure_merge.py` fuses aligned passes.
Let `base = min(exposures)`, `r_i = exposure_i / base`:

* **Per-pass normalisation to base-exposure scale**

  `x_i = raw_i / r_i`.

* **Per-pass confidence + variance model** — the *same* Poisson–Gaussian model
  the 2-pass path uses (`_SNR_ALPHA`, `_SNR_BETA`, soft confidence ramp with
  early clip before the CCD knee):

  `conf_i = smooth_confidence(raw_i)`
  `var_i  = (alpha * raw_i + beta) / r_i^2`
  `w_i    = conf_i / var_i`

* **IVW**

  `fused = sum_i (w_i * x_i) / sum_i (w_i)`

  which is a convex combination of the per-pass base-scale values, so it is
  numerically bounded (no overflow) and reduces to the classic 2-pass result
  at N=2 (weights, not the *entire* output — see below).

* **Cross-pass dispersion gate**

  Compute `xbar_i` and `var(x_i)` across the N passes per-pixel. Suppress the
  fused pixel with a smooth function of the normalised variance
  `rel = var/mean^2`:

  `gate = 1` if `rel <= disp_lo` (0.10)
  `gate = 0` if `rel >= disp_hi` (0.60)
  `gate = smooth decay` between.

  The gate keeps the fused value honest where the passes genuinely disagree
  (misregistration, a bad frame, or a non-linear response that the per-pass
  IVW alone can't resolve).

### What it does *not* do

* A **single bad pass** among N>=3 is **partially** suppressed: the dispersion
  gate is a *soft* cross-pass agreement term, not a hard frame-reject. To reject
  a single pass outright you'd need an outlier-detection pass over the N
  passes (e.g. per-pass median absolute deviation) before the IVW. If that
  matters in your workflow, it's a good follow-up to add.
* The N-pass path does **not** inherit the 2-pass "z-median / prefer
  fallback / misregistered-edge guard" — those are 2-pass heuristics specific
  to a short+long pair. The dispersion gate is the N-pass generalisation.
* The N-pass path computes in `float64` (the 2-pass path uses `float32`).
  Numerically equivalent for the IVW weights, but the *final* fused pixel may
  differ by ~1 DN from what the 2-pass path produced for the *same* short/long
  pair. At N=2 we keep the byte-identical 2-pass path, so a real N=2 caller
  sees no difference.

## Memory / time

Per pixel, `NPassMerger` holds 5 float64 accumulator planes
(`num, den, sumx, sumx2` + one uint16 output) — *independent of N*. N=99 costs
99x the pass time and no extra memory, so the ceiling of
`Model8200iSE.max_passes = 9` is a *time* limit, not a memory one.

## Verification

* `tests/test_multi_pass.py` — 22 pure/synthetic, deterministic tests covering
  the ladder, `validate_exposures`, `reduce_passes` dispatch for N=1/2/3/4/6,
  **byte-identity of N=2 vs the classic path**, monotonic shadow RMSE
  improvement N=1 > N=2 > N=4 > N=6, the dispersion gate, and the
  `NPassDebug` parity fields.
* `tools/multipass_compatibility.py` — renders a side-by-side PNG + metrics CSV
  into `--outdir` (default `/home/tobby/Pictures`). Uses the same scene and
  noise model as the tests, with deliberately visible grain so a human can
  eyeball the single vs 2-pass vs N-pass difference in the dense-shadow region.
* `tools/multipass_real_compare.py` — the same idea on **real hardware**: scans
  the film currently loaded in the scanner twice (once N=1, once N=N) at a given
  DPI and area, then writes 16-bit TIFFs, a side-by-side PNG, a full-resolution
  centre-crop PNG, and a metrics CSV. Noise is reported as a high-frequency RMS
  proxy in DN (per-pixel residual against a 9x9 box blur, so it measures grain /
  shot noise rather than scene contrast).

To re-generate the validation images:

```bash
env -u PYTHONPATH uv run --all-groups --with pillow \
    python -m tools.multipass_compatibility \
    --outdir /home/tobby/Pictures --seed 20260824
```

### Measured on real hardware

OpticFilm 8100 (V2), 7200 dpi, central 20% of the scan area (3196x4636 px),
colour negative loaded in the holder, N=1 vs N=4:

| panel | N | exposure ladder | scan time | HF-RMS full | HF-RMS crop | mean DN |
|---|---|---|---|---|---|---|
| single | 1 | 14000 | 65.2 s | 849.31 | 686.60 | 17255.2 |
| N-pass | 4 | 14000+14000+42000+42000 | 443.3 s | 441.92 | 344.95 | 15764.8 |

Noise drops **1.92x** over the full area and **1.99x** on the centre crop.
Ideal shot-noise averaging over 4 passes predicts `sqrt(4) = 2.00x`, so the
merge is doing real statistical averaging rather than smoothing. The full-frame
figure lands slightly under ideal because the frame edges include the holder
gate, where inter-pass registration is weakest. The lower mean DN is the
short+long ladder recovering shadow density instead of clipping it.

The cost is the expected `sqrt(N)` trade: ~6.8x the wall-clock time for ~2x the
SNR.

Reproduce with:

```bash
env -u PYTHONPATH uv run --all-groups --with pillow \
    python -m tools.multipass_real_compare \
    --dpi 7200 --n 4 --area-pct 20 --outdir /home/tobby/Pictures
```

## Upstream notes

* All new code is **additive** — the 2-pass path (`merge_exposures_result`,
  `_merge_snr`, the classic `Gl128ScanSession.run` flow for N=2) is untouched
  except for being refactored into a common `_run_multi_pass` entry that the
  N-pass branch also calls. No existing test behaviour changes.
* No new public package dependencies.
* `me_debug.py`'s `NPassDebug.rgb_short/long` are now **exposure-class**
  (min/max) rather than positional, so the parity fields are robust to pass
  order.
* `Model8100V2` (added in the same feature set) has `supports_infrared=False`
  — a `ScanError` guard in `Gl128ScanSession` protects the IR path for it.
  The multi-pass path works off the same capture-validated exposures and
  inherits the SE behaviour.
