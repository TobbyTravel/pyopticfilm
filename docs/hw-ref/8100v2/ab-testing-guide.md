# 8100 V2 hardware A/B testing guide — issues #33 and #35

## RESOLVED 2026-08-30 — read this first, then see PROGRESS.md's final entries

The mechanical fault this whole document was written to isolate is now
believed fixed. Root cause: `Gl128._upload_fast_slopes()` always used the
aggressive `SLOPE_TABLE_FAST` motor ramp for both feeds in
`position_for_full_frame_scan()`; two independent real captures show the
second (final positioning) feed should use `SLOPE_TABLE_SLOW` instead —
applied in `02a0d4f`, confirmed clean on real hardware 5-for-5 in an
isolated test and once more on the actual working branch. `Model8100V2`
also now defaults priming off (`default_gl128_prime=False`), precautionary
rather than proven necessary on its own. Full story, including the wrong
turns taken before finding this:
`docs/hw-ref/8100v2/PROGRESS.md`'s entries from "MECHANICAL FAULT
REPRODUCED" through the final "Confirmed" entry near the end of that file.

None of the isolation flags below are implicated in the actual root cause
— they were this session's first (unsuccessful) hypothesis space. Kept
below for historical reference, not as active guidance.

## UPDATE 2026-08-30 — `POF_GL128_V2_FIX_FEED_PROBE` is a real, separate
## hazard and has been retired from the codebase entirely

Re-examined after a retest under the now-fixed slope table also faulted.
Filtering every real-hardware trial by probe state and separating pre-fix
from post-fix (the pre-fix "confirmed broken" correlation below was itself
confounded by the still-active slope-table bug at the time):

- Probe active (default), **post-slope-fix only**: 5/5 clean.
- Probe disabled (`FIX_FEED_PROBE=1`), **every trial ever run, pre- and
  post-fix**: 3/3 fault, zero clean.

This is a real, distinct hazard from the slope-table bug — not an
artifact of it. `POF_GL128_V2_FIX_FEED_PROBE` has been **removed from the
codebase**, not just left off by default: `Model8100V2` no longer
overrides `feed_probe_index` at all, so it unconditionally inherits the
SE's `0x21`, and the env var is now a no-op (regression-tested). Do not
reintroduce a way to disable this probe on V2 without a real fault
capture confirming the mechanism first — see `gl128.py`'s comment near
`_FEED_PROBE_INDEX` for the leading (unproven) hypothesis. Full detail:
`PROGRESS.md`'s entries from "PR #10 description synced" through the
retirement.

## 2026-08-30 incident — read this first

Applying all four capture-derived corrections at once and testing on real
8100 V2 hardware produced a serious mechanical fault (a high-pitched sound
followed by a thunk, consistent with a motor overspeed/hard-stop event) on
two separate attempts, each requiring a hard power-off to recover. A third
attempt on a clean, unmodified `main` checkout (via Scan Lab, GUI-driven)
ran cleanly with no fault. Root cause has **not** been isolated to a single
change — full incident log in `docs/hw-ref/8100v2/PROGRESS.md`.

**Every one of the five corrections is now its own opt-in, env-var-gated
flag, defaulting off.** With nothing set, `Model8100V2` behaves identically
to the pre-session (`main`) values that just tested clean. This lets you
test exactly one correction at a time and find the one responsible, without
a code change between attempts — see "Per-fix isolation flags" below.

**Every hardware attempt from here on:**
- Be physically present and watching the carriage, hand near the power
  switch, for the entire scan — not something to run unsupervised.
- Cut power immediately at the first unusual sound. Don't wait to see if it
  resolves on its own.
- One flag at a time. Don't combine flags until each has been individually
  cleared.
- If clean `main` (all flags off) ever produces the fault again, stop
  immediately — that would mean the fault isn't caused by any of these
  changes at all, and continued isolation testing along these lines
  wouldn't be productive.

## Two separate issues -- don't conflate them (2026-08-30 update)

Isolation testing so far has surfaced **two different problems** at
7200dpi on real hardware. Keep them separate:

1. **The mechanical fault** (high-pitched sound / thunk, requires hard
   power-off) -- the subject of this whole document. As of the latest
   session, `FIX_LINCNT`-alone, `FIX_DEPTH_A`-alone, and the all-flags-off
   baseline have all been tested mechanically clean (no sound). Not yet
   reproduced since isolation testing began.
2. **A mid-scan image corruption/stall** (black frame with vertical
   color-channel streaks over most of the width, a valid strip only in one
   region, USB log shows a long `0x101` FEEDFSH/HOMESNR poll loop at the
   end) -- confirmed to reproduce with `FIX_LINCNT` alone, `FIX_DEPTH_A`
   alone, **and** with all 5 flags off (main-equivalent). This means it is
   **not caused by any of the 5 applied fixes** -- it's a separate,
   pre-existing pyopticfilm issue at 7200dpi on this hardware, undetected
   by the original incident's clean-`main` check (which only listened for
   the mechanical fault, never inspected image content). See
   `PROGRESS.md`'s "Baseline ... also reproduces the corruption/mid-scan
   stall" entry for full detail.

Do not treat a corrupted/stalled image as a mechanical-fault signal, and do
not treat a visually-correct image as clearing a flag mechanically --
they're independent checks. Root-causing the corruption/stall bug is
explicitly deferred until the mechanical-fault isolation below is complete.

## STOP: the fault is likely intermittent, not isolated to any tested flag (2026-08-30, final update)

A confirmation run of the "fix" below (feed_probe_index hardcoded to
0x21, no flags set) **also faulted** -- the identical configuration that
had already run clean three times earlier the same session. This breaks
the clean correlation the fix was based on. **No hardware trial, clean or
faulted, can currently be trusted to characterize a configuration's
safety.** Do not run further one-flag-at-a-time isolation attempts on real
hardware based on this document's method until there's a better plan than
"try a config once and see." Full detail: `PROGRESS.md`'s "CONFIRMATION RUN
ALSO FAULTED" entry.

The section below is kept for the history of what was tried, not as a
resolved conclusion.

**FURTHER UPDATE:** `FIX_FEED_STEPS` (the one previously-untested flag) was
also tested and also faulted. Every configuration tried this session has
now faulted at least once except `FIX_LINCNT`-alone and `FIX_DEPTH_A`-alone
(one trial each). **The one-flag-at-a-time method has run its course --
stop using this document's procedure to isolate a software cause.** See
`PROGRESS.md`'s "FEED_STEPS also faulted" entry for the full tally and
recommendation (physical/mechanical inspection, not more code-flag trials).

## (Superseded) RESULT (2026-08-30): `POF_GL128_V2_FIX_FEED_PROBE` reproduces the mechanical fault -- do not enable on real hardware

Isolation testing found it: `POF_GL128_V2_FIX_FEED_PROBE=1` alone (every
other flag off, the same state that had just passed three clean mechanical
tests in a row) reproduced the original high-pitched-sound mechanical
fault, at 1200dpi Prescan, during positioning/feed, before any image pass.
User cut power immediately, no damage reported. Full detail in
`PROGRESS.md`'s "MECHANICAL FAULT REPRODUCED" entry.

**Do not set `POF_GL128_V2_FIX_FEED_PROBE=1` on real hardware again** until
the underlying mechanism in `Gl128._wait_feed_probe_done()` /
`_read_feed_probe()` is understood and fixed (not yet done -- next session's
job, code-only, no hardware). `FEED_STEPS` is the only flag never tested
this session -- no conclusion either way about it, and there is no reason
to expect it shares FEED_PROBE's mechanism, but treat it as unproven, not
cleared.

## Per-fix isolation flags (2026-08-30 incident isolation)

All default **off** (matches the pre-session / `main` value). Set exactly
one before launching Scan Lab or `tools/hw_ab_capture.py`:

| Flag | Effect | Finding |
|---|---|---|
| `POF_GL128_V2_FIX_FEED_STEPS=1` | `feed_to_scan_steps`: 13128 → 13486 | F-007/F-008 |
| `POF_GL128_V2_FIX_DUMMY=1` | `dummy_by_dpi[7200]`: 23 → 15 | F-013 |
| `POF_GL128_V2_FIX_DEPTH_A=1` | new `image_depth_a` override: unset → 0x04 | F-013 |
| `POF_GL128_V2_FIX_LINCNT=1` | `image_lincnt_per_line`: 4→1, `usb_image_lincnt_half_lines`: True→False | F-012 |
| ~~`POF_GL128_V2_FIX_FEED_PROBE=1`~~ | **retired 2026-08-30 — removed from code, no longer exists.** 3-for-3 real fault, zero clean, across the whole project. Do not reintroduce. | F-014 |

```powershell
$env:POF_GL128_V2_FIX_DUMMY = "1"
uv run python -m tools.scanlab
```

To go back to baseline in the same shell:

```powershell
Remove-Item Env:\POF_GL128_V2_FIX_DUMMY
```

**Suggested order** (least to most structurally different from `main`,
purely a starting guess — reorder freely):
1. `POF_GL128_V2_FIX_FEED_STEPS` — a single positioning-feed constant, no
   register-count or data-path change.
2. `POF_GL128_V2_FIX_DUMMY` — a single pixel-clock padding register.
3. `POF_GL128_V2_FIX_DEPTH_A` — a data-width register pair change.
4. `POF_GL128_V2_FIX_FEED_PROBE` — host-side polling only, writes no new
   ASIC registers, but was present in every failed attempt so far.
5. `POF_GL128_V2_FIX_LINCNT` — **UPDATE 2026-08-30: now tested in both
   directions.** As the only *reverted* change (others still applied, PR
   #10 incident attempt 2): fault still occurred — didn't clear it alone.
   As the only *applied* change (others at default, isolation session):
   **mechanically clean** — no sound, no pipe error — but produced a badly
   corrupted 7200dpi image (vertical color-channel streaks over ~80% of the
   frame), likely from LINCNT's byte-count formula being verified together
   with `FIX_DEPTH_A` in the source captures, not independently. LINCNT
   itself is cleared as a *mechanical* fault trigger in this direction; the
   image bug is a separate, unfixed software issue — don't judge output
   quality when testing the other flags until DEPTH_A is tested too. See
   `PROGRESS.md`'s 2026-08-30 "Isolation testing begins" entry for detail.

Note capture 1's crash and capture 2's crash (`PROGRESS.md`) both had
`FIX_DUMMY`, `FIX_DEPTH_A`, and `FIX_FEED_PROBE` simultaneously active
(alongside `FIX_LINCNT` in attempt 1 only) — `FIX_FEED_STEPS` was never
actually exercised in either failed attempt (both used explicit crop
areas, which don't use `feed_to_scan_steps`). So flag 1 is the least likely
suspect of the five; consider testing it first specifically to clear it
quickly, or last since it's lowest-priority to find guilty.

## Issue #35 — LPERIOD at 7200dpi (separate from the above)

This is a genuinely unresolved *value*, not a reverted-then-isolated fix —
neither candidate was ever applied as a default.

```powershell
$env:POF_GL128_V2_LPERIOD_7200 = "15914"   # default-crop capture's value
uv run python -m tools.scanlab
```

Try `"15914"`, then `"15999"` (the full-sensor capture's value), then unset
entirely (today's default, 16035 — the pre-session value, confirmed not to
match either real capture but also confirmed not to crash hardware on its
own). Compare for the jagged/stepped-line symptom. No effect at any DPI but
7200.

## Issue #33 — first-scan positioning (separate from the above)

Also a genuinely unresolved hypothesis, never applied as a default.

```powershell
$env:POF_GL128_V2_FIRST_SCAN_FEED2 = "1"
uv run python -m tools.scanlab
```

The **first** scan run against a given session targets second-feed
position 13128 ("top of TA window") instead of the normal ~13486; every
scan after that first one in the same session uses the normal value. Note:
13128 is also `Model8100V2`'s own `feed_to_scan_steps` default (unrelated
to `FIX_FEED_STEPS` above, which only affects the *default full-frame*
value — this experimental override targets the *first-scan* case
specifically, matching what real captures showed regardless of the
`FIX_FEED_STEPS` flag state). Compare positioning consistency between scan
1→2 with the override on vs. off (unset).

This doesn't decouple "first scan of session" from "first pass of a
multi-pass request" — Scan Lab's single-pass scans only exercise the
session-first case. Not something to prioritize until the current hardware
fault is resolved.

## What "worked" looks like

None of the seven flags in this document (five isolation flags + two
experimental overrides) are confirmed fixes. A useful outcome from
isolation testing is finding which single flag reproduces the fault when
enabled alone — at that point, stop, don't try to fix it in the same
session, and report back (including exactly which flag and what the
symptom looked like) before deciding next steps. A useful outcome from the
LPERIOD/first-scan tests (once isolation testing is resolved and it's safe
to move on to them) is the same as before: report results including null
ones, don't decide defaults from a single hardware session.
