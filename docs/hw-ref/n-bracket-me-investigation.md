# N-bracket ME investigation (PR #52 / issue #50)

Status tracker for validating [jboneng/pyopticfilm#52](https://github.com/jboneng/pyopticfilm/pull/52)
(N-bracket ME, draft — real-hardware faults reported on the 8200i SE) and
[#50](https://github.com/jboneng/pyopticfilm/issues/50) (8100 V2 N-bracket
color-balance shift) against the 2026-09 multi-exposure captures. See
`docs/hw-ref/8100v2/2026-09-recapture/claims-reconciliation.md` (session 07)
and `docs/hw-ref/8200i-se/2026-09-multi-exposure/findings.md` (session 14)
for the full capture-level detail this summarizes.

## Phase 1 — capture validation: done

Both models' vendor drivers do an identical, fixed 2-bracket ME scheme
(`EXPOSURE=14000` then `42000`, exact 3:1 ratio) at every DPI captured, never
adaptive, never model- or DPI-dependent. **`n_brackets > 2` and adaptive
per-frame exposure selection have zero vendor-driver precedent on either
scanner** — both are pyopticfilm inventions built on top of this fixed
scheme, not reverse-engineered driver behavior. This reframes what "hardware
validated" can mean for the N-bracket path: no capture will ever confirm or
refute it directly, because the vendor never generates that traffic shape.

The shared primitives N-bracket is built on — `REG_EXPOSURE` encoding, the
per-bracket full independent shading→image cycle, and the short/long
pixel-clock split (`0xA5`/`0xAB`) — are now confirmed on **both** models
independently (previously only V2 had a fresh 2026-09 capture; the SE side
was re-derived from scratch here rather than trusted from an old comment).

## Phase 2 — code audit: done

- **Confirmed, matches issue #50's own theory exactly**: `fixed_long_exposure()`
  (`src/pyopticfilm/scan/me_exposure.py:221-248`) always pins the top bracket
  to `exposure_long` (42000 on V2) regardless of predicted clipping — there is
  no adaptive override in `"fixed"` mode by design. Combined with
  `exposure_merge.py`'s per-channel IVW confidence ramp starting at 80% full
  scale (`_SNR_CLIP_START`), any frame where one channel clips near 42000
  loses IVW weight on that channel specifically, which is a color shift, not
  a brightness one — exactly #50's mechanism. Nothing new to add beyond
  confirming the existing hypothesis is code-accurate.
- **New, concrete hypothesis for jboneng's PR #52 hardware reports**: every
  non-short N-bracket pass gets `long_pass=True` unconditionally
  (`session_gl128.py:699`), which drives the *same* short/long pixel-clock
  switch (`gl128_common.py:521`, `pixel_clock_for_image`) regardless of how
  close that bracket's actual exposure is to the short baseline. That binary
  switch has only ever been observed at its two vendor-confirmed endpoints
  (14000→short-clock, 42000→long-clock, both models, Phase 1). Every
  intermediate `n_brackets>2` schedule value (`session_gl128.py:684-688`,
  geometric spacing between short and long) is extrapolation on a switch
  that's untested anywhere in between. This is a plausible mechanism for
  "the first bracket pass drives the motor too fast to achieve that low
  exposure" (an intermediate bracket much closer to 14000 than 42000 still
  gets the slower "long" clock config) — plausible, not confirmed; only a
  real-hardware trial (Phase 3) can settle it.
- **Dead code removed**: `Model8100V2.me_n_bracket_long_exposure_ceiling()`
  was never called anywhere and was a no-op even if it were (V2's ordinary
  `me_long_exposure_ceiling_default` is already 42000 flat) — its docstring
  was also stale, misattributing the SE's 85000 fallback to V2. Removed;
  no behavior change (see the dead-code-removal commit).
- **Terminology gap, flagged not resolved**: issue #50 says "color-balance
  shift"; no code comment anywhere uses that phrase. The closest documented
  real-hardware symptom in the codebase is "ghosting" (luma/chroma
  misalignment, already fixed via banded alignment + a luma-only gate,
  regression-tested in `tests/test_me.py`) — a different symptom in
  principle (misregistration vs. per-channel exposure/clip weighting), but
  worth confirming with whoever characterized #50 that these aren't the same
  underlying issue described two ways.

## Phase 3 — real V2 hardware trial: not started, needs the user

Nothing in Phase 1/2 substitutes for this. Only the 8100 V2 side can be
tested here; the original PR #52 reports were on the 8200i SE, so a fix (or
a "V2 doesn't reproduce this" finding) doesn't close the SE side of the
report without jboneng's own follow-up.

Suggested matrix, recorded with Scan Lab's Forensic tab (built in PR #54) so
the actual register sequence is captured for comparison against the Phase 1
baselines:

1. **7200ppi, `n_brackets=3`, fixed mode** — mirrors the SE ASIC-error report
   (communication lost, yellow power LED, before the last colour pass).
2. **1800ppi and 900ppi, `n_brackets>2`** (e.g. `n_brackets=3` and `5`) —
   mirrors the SE "unhealthy motor sound on the first bracket" report.
3. **`n_brackets=2` at the same DPIs** as a known-good baseline (the
   "existing, already-validated" path) for A/B comparison against 1-2.
4. Optional but useful: one setting run through both NegPy and Scan Lab, to
   check TobbyTravel's earlier observation that Scan Lab showed more errors
   than NegPy for the same feature — if real, that points at a Scan-Lab-side
   calling difference rather than the ME code itself.

For each run, check the Forensic-tab-recorded `REG_EXPOSURE`/pixel-clock/
slope-table sequence against: (a) the Phase 1 vendor baselines, (b) whether
the flagged intermediate-bracket pixel-clock hypothesis above actually shows
up as a problem in practice.

## Phase 4 — fixes / PR: pending Phase 3

No further code change is planned until Phase 3 produces evidence either
way. If the pixel-clock hypothesis is confirmed, the fix and its model-lock
oracle update will cite the specific Forensic-tab recording, the same way
the slope-table fix (issue #56 / PR #58) cited specific capture frames.
