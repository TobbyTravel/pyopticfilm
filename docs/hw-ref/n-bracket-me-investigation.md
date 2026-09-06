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
- **Hypothesis for jboneng's PR #52 hardware reports — confirmed as fact,
  not just a code-reading, by the Phase 3 recordings**: every non-short
  N-bracket pass gets `long_pass=True` unconditionally
  (`session_gl128.py:699`), which drives the *same* short/long pixel-clock
  switch (`gl128_common.py:521`, `pixel_clock_for_image`) regardless of how
  close that bracket's actual exposure is to the short baseline. That binary
  switch has only ever been observed at its two vendor-confirmed endpoints
  (14000→short-clock, 42000→long-clock, both models, Phase 1). The real V2
  recordings (`p3-1800-n3`, `p3-7200-n3`) show the middle `n_brackets=3`
  bracket (`EXPOSURE=24249`, closer to 14000 than 42000) gets
  `0xA5/0xAB=(1,1)` — the full "long" clock — immediately, at both DPIs
  (`decoded_events.jsonl` idx 4102-4103 at 1800dpi, idx 45416-45417 at
  7200dpi). **This pairing is real and does happen**, and it is a
  combination no vendor capture has ever produced. On the 8100 V2, at
  n_brackets=3, 1800/900/7200dpi, it did not cause a fault (see Phase 3) —
  so it remains a plausible mechanism for jboneng's SE reports specifically,
  not a confirmed one; V2 not reproducing it doesn't rule it out on the SE.
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

## Phase 3 — real V2 hardware trial: done, no fault reproduced

Run 2026-09-06 on the user's real 8100 V2, via `tools/scanlab/cli.py scan
--real` (new `--multi-exposure`/`--n-brackets`/`--me-exposure-mode`/
`--save-tiff-dir` flags added for this), each recorded with the Forensic
tab's `ForensicRun` and visually reviewed as a 16-bit TIFF. Ordered
safest-to-riskiest, user present throughout, watching/listening:

| Run | Config | Outcome | Y align shift (long bracket) | Notes |
|---|---|---|---|---|
| `p3-00-sanity-prescan(-v2)` | 1200dpi prescan, no ME | success | n/a | Baseline sanity — clean image |
| `p3-1800-n2-baseline` | 1800dpi, `n_brackets=2` | success | -44.1px | Known-good path, clean image |
| `p3-1800-n3` | 1800dpi, `n_brackets=3` | success | -45.2px | Middle bracket (24249) gets long pixel-clock — see Phase 2 update. Clean image |
| `p3-900-n3` | 900dpi, `n_brackets=3` | success | -2.0px | Mirrors the SE "unhealthy motor sound" report. No fault, no unusual sound |
| `p3-7200-n3` | 7200dpi, `n_brackets=3` | success | -170.9px | Mirrors the SE ASIC-error report (comm lost, yellow LED). No fault, no comm loss, no unusual sound. Longest run (~5.5 min motor-enabled on the final bracket) |

**Neither of jboneng's two 8200i SE hardware faults reproduces on the 8100
V2** at the equivalent settings. All five runs completed successfully;
merged output visually clean at every DPI (no ghosting, no visible
color-balance shift, no banding); Y-axis alignment shift scales with DPI/pass
length as expected from the known #33 jitter mechanism and was fully
corrected by the existing banded-alignment path in every case.

**What this does and doesn't close out**:
- Confirms the N-bracket ME code path is safe to use on the 8100 V2 at
  3 brackets across the DPI range, including the exact configs that faulted
  the SE.
- Does **not** close the SE side of PR #52 — those faults were never claimed
  to be V2 issues, and a V2 non-reproduction doesn't tell us why the SE
  faulted. That needs jboneng's own follow-up on his hardware.
- The pixel-clock hypothesis (Phase 2) is now confirmed as real, observed
  behavior, but not confirmed as *harmful* — it didn't fault V2. Worth
  flagging to jboneng as a specific thing to check if he re-tests: whether
  the SE fault correlates with which bracket (does it happen on the first
  non-short bracket specifically, where the exposure is *most* mismatched
  from both the short baseline and the long-clock's usual 42000 pairing?).
- Not tested here: `n_brackets` above 3, `me_exposure_mode="adaptive"` on
  V2's N-bracket path (V2 defaults to `"fixed"`), and the NegPy-vs-Scan-Lab
  comparison TobbyTravel's earlier PR #52 comment raised. None were needed
  to answer the two specific fault reports, but remain open if useful later.

Full JSON results, anomaly reports, and raw Forensic recordings are under
`tools/scanlab/runs/p3-*/` (git-ignored, local-only — not part of this repo's
history, same as every other Scan Lab run).

## Phase 4 — fixes / PR

No code fix is needed as a result of Phase 3 — nothing failed on the
hardware that could be fixed. Next steps are process, not code:

- Rebase this work's home branch onto current `upstream/main` (it and PR
  #52's own `feat/n-brackets-on-main` both drifted 6 commits behind after
  PR #54 — see the branch-hygiene note in the PR body once opened).
- Post these Phase 1-3 findings to PR #52 / issue #50 so jboneng has the
  V2-side non-reproduction on record, and the concrete pixel-clock detail
  to check for if/when he re-tests the SE.
- The `tools/scanlab/cli.py` ME automation (`--multi-exposure`,
  `--n-brackets`, `--me-exposure-mode`, `--save-tiff-dir`) is a
  reusable-going-forward addition, independent of whatever PR #52 itself
  decides — worth keeping regardless of that PR's outcome.
