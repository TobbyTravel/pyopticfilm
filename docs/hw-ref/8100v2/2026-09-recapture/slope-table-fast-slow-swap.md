# 8100 V2: real vendor driver's slope-table assignment is the opposite of what's documented/shipped

**Status: OPEN INVESTIGATION — no code change proposed or made.**

## Summary

While reviewing `feat/forensic-timing-ui` (PR #12)'s slope-table detection logic —
matching an uploaded AHB motor slope table's raw bytes against `SLOPE_TABLE_FAST`/
`SLOPE_TABLE_SLOW` — the same byte-comparison approach was applied to a fresh,
independent 8100 V2 capture set (`TobbyTravel/pyopticfilm_captures` branch
`add-8100-v2-captures`, 2026-09-05, vendor SilverFast driver). The result contradicts
what's currently documented and shipped.

| Feed | Steps | Documented (`gl128.py` docstring, `register_reference.py` safety_note) | Currently shipped (`use_slow_final_positioning_feed=True`) | **What this capture set shows** |
|---|---|---|---|---|
| First (reference) | 28292 | FAST | FAST (implicit default) | **SLOW** |
| Second (final positioning) | 13128 | SLOW | SLOW (explicit override) | **FAST** |

This is a byte-exact full-512-byte match (not approximate), and it's **reproduced
identically in two separate, independent capture sessions**:

- `04_color_7200.pcapng`: first-feed uploads at frames 2849/2857 == `SLOPE_TABLE_SLOW`
  exactly; second-feed uploads at frames 3023/3031 == `SLOPE_TABLE_FAST` exactly.
- `06_ppi_ladder.pcapng`: first-feed uploads at frames 3103/3111 == `SLOPE_TABLE_SLOW`
  exactly; second-feed uploads at frames 3277/3285 == `SLOPE_TABLE_FAST` exactly.

Verified programmatically (byte-for-byte comparison against
`pyopticfilm.device.tables_8200i_se.SLOPE_TABLE_FAST`/`SLOPE_TABLE_SLOW`), not by eye,
and cross-checked against the raw `tshark -x` hex dump for each cited frame.

FEEDL register writes confirming which feed is which: `04_color_7200.pcapng` frame 2837
(FEEDL=28292) and frame 3015 (FEEDL=13128); `06_ppi_ladder.pcapng` frame 3091
(FEEDL=28292) and frame 3269 (FEEDL=13128).

## Why this needs care, not a quick fix

This is exactly the category of change that caused the real mechanical fault already on
record in `register_reference.py`'s safety note for the FEEDL register (motor overspeed,
two hard power-offs, from applying an untested slope-table assumption as a new default).
That incident's actual root cause turned out to be unrelated (a different bug in
`_upload_fast_slopes()` applying FAST to both feeds), but it's the same class of risk —
and the existing `use_slow_final_positioning_feed` fix was itself built on the *other*
direction of this exact FAST/SLOW claim, the one this new evidence now contradicts.

**No code change is proposed or made here.** This needs the project's own
hardware-validation protocol before anything changes:

- An opt-in flag, never a new default.
- Forensic-tab-recorded real trials on actual V2 hardware (`tools/scanlab/cli.py scan
  --ai-report`), not a bare pass/fail.
- Multiple repeated trials, not a single run.
- Explicit visual/mechanical inspection of the outcome, not just "no exception, scan
  completed" — the last real fault here produced a corrupted image with no exception and
  no audible warning.

## Evidence / methodology

Full per-session register extraction: `tools/extract_v2_registers.py` and
`docs/hw-ref/8100v2/2026-09-recapture/ledgers/{04_color_7200,06_ppi_ladder}.registers.json`
(same branch as this document). Cross-reference:
`docs/hw-ref/8100v2/2026-09-recapture/claims-reconciliation.md`.

## Next steps (not started)

1. Corroborate further against `07_multi_exposure.pcapng`, which has two more
   independent full-frame 7200dpi passes.
2. Design a safe, opt-in-only real-hardware trial protocol before touching
   `_upload_fast_slopes()` / `Model8100V2.use_slow_final_positioning_feed`.
3. Update `gl128.py`'s docstring and `register_reference.py`'s safety note only once/if
   a hardware trial confirms which assignment is actually correct on real hardware — do
   not update documentation to match this finding until it's been physically validated,
   since "the capture shows X" was also true of the claim this one contradicts.
