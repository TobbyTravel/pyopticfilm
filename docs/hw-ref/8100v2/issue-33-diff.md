# Issue #33 diff — scan-to-scan Y jitter, first-scan positioning

Compares the reference driver's positioning-feed sequence (from capture 3's
4 acquisition cycles, plus the 7 single-pass captures) against
pyopticfilm's feed/home/prime path. Observation, interpretation, and
recommendation kept separate.

## The reference driver's positioning-feed sequence (observed)

Every acquisition cycle in every capture follows the same two-feed pattern
before the image pass starts (see `phases/*.md`, `findings.md` F-007/F-008):

1. `FEEDL = 28292` written (reference feed from home — matches
   `Model8200iSE.feed_to_reference_steps=28292` exactly, C-017 now
   effectively confirmed by repetition across 8 acquisitions in this
   dataset).
2. ~1.0-1.05s later, a second `FEEDL` write: either **13486** (default crop,
   6/8 acquisitions), **13200** (full-sensor/no-crop, 3 acquisitions), or
   **13128** (session-first acquisition only, 1 occurrence — F-007/F-008).
3. ~1.4-1.5s after the second `FEEDL` write, the image-pass `wIndex=0x08`
   buffer preamble appears (image acquisition starts).

**Status-poll cadence during both feeds** (capture 3, cycle 1, packet
indices 2913-3879 — `docs/hw-ref/8100v2/ledgers/3.events.json`): register
`0x101` (status) is polled roughly every 15-16ms throughout both feed
operations (90 polls across the ~965ms window). Decoded bit pattern during
the first feed: `0xf5` → `PWRBIT|BUFEMPTY|FEEDFSH|SCANFSH|LAMPSTS|MOTORENB`
set, `HOMESNR` clear (not at home) — i.e. the reference driver polls a
combination of bits including `FEEDFSH` and `HOMESNR`, not a single flag.
The transition into the second feed shows `FEEDFSH` clearing (bit 0x20
drops from `0xf5`/`0xf4` to `0xd5`) — consistent with each new feed op
clearing the "feed finished" flag until it completes again.

**Does the reference driver re-home before every pass?** Not observed —
capture 3's cycles 2-4 (repeat passes of the same 3-pass request) go
straight from the previous pass's park state into the reference feed
(28292) without a separate distinguishable home-seeking step visible in the
register/status log. The 28292 reference feed itself may *be* the
positioning-to-home-equivalent step (its value is constant across all 4
cycles, suggesting it always starts from a known/parked position), but this
capture data cannot distinguish "re-homes then feeds 28292" from "feeds
28292 directly because the prior pass already parked at a known origin."

## pyopticfilm's feed/home/prime path (from code, not yet cross-run against these captures)

- `Gl128._begin_scan()` / `session_gl128.py:_configure()`: two
  capture-constant feeds from home — `feed_to_reference_steps` (28292) then
  `feed_to_scan_steps_for_area()` — matching the reference driver's
  two-feed structure exactly in shape. The *second* feed's value is
  computed from a `y1`-fraction formula rather than the reference driver's
  observed discrete clusters (13128/13200/13486) — F-008 already flags this
  formula needs re-deriving against the corrected clusters.
- Feed polling: `gl128.py` polls a vendor probe (`_FEED_PROBE_INDEX=0x21`,
  looking for `_FEED_PROBE_DONE=0x04`) rather than register `0x101`'s
  `FEEDFSH` bit directly. **Cross-checked against the captures (F-014):
  the reference driver never polls `wIndex=0x21` at all.** It uses this
  same vendor-probe wire pattern (`bRequest=REQUEST_REGISTER`) but at
  `wIndex=0x20` (constant response 0x55) and, less often, `wIndex=0x18`
  (2 or 18) — both cluster tightly around the two positioning `FEEDL`
  writes, so *something* feed-related is being probed, just not what
  pyopticfilm currently polls. `0x20`'s constant value doesn't look like a
  busy→done transition flag the way `0x21→0x04` is documented to, but
  every sample could simply already be post-transition (feeds may complete
  faster than the polling interval reliably catches mid-transition).
  Meaning of both indices is unresolved — see F-014. **Follow-up**: these
  probes turned out not to be feed-specific at all (queried constantly from
  device open onward, not just around feeds), which reads more like a
  generic engine-busy check than a completion signal. **Applied**: V2 now
  skips the `0x21` probe entirely (`Model8100V2.feed_probe_index = None`)
  rather than querying an index confirmed unused; relies solely on the
  already-confirmed `0x101` status path (which the code already used as a
  fallback). `0x20`/`0x18` were deliberately *not* adopted as a
  replacement — their semantics are still unconfirmed.
- Slope table for positioning feeds: `Gl128.upload_tables(shading=False)`
  (pyopticfilm's default path for a non-shading operation) uploads
  `SLOPE_TABLE_FAST` content to `AHB_SLOPE_SCAN`/`AHB_SLOPE_FAST` — the same
  fast ramp issue #33 describes the reference driver always using for
  positioning feeds. **This appears to already match** pyopticfilm's
  default behavior (both use the fast ramp for positioning), consistent
  with issue #33's own finding that feed-ramp speed doesn't explain the
  jitter (already ruled out on real hardware in the issue body) — table
  *content* itself is not decodable from captures (never touch bulk
  payload), so this is a structural match (same table selected), not a
  byte-for-byte content match.
- Priming: pyopticfilm's `Scanner.scan()` runs one discarded 600dpi
  small-crop pass (or a configurable alternative) the first time a GL128
  scanner is used, ending in the capture-proven `AGOHOME` park
  (`scanner-validation.md` "GL128 first-pass positioning"). This is a
  **different mechanism** from what F-007/F-008 found in the reference
  driver's session-first pass: pyopticfilm discards an *entire extra
  acquisition*; the reference driver's session-first pass runs at the same
  DPI/crop as the rest of the request but targets a **different absolute
  second-feed position** (13128, matching the documented "top of TA
  window") before subsequent passes settle at the crop's actual position
  (13486/13200). These are not obviously the same fix for the same problem
  — pyopticfilm's priming softens landing-position *error at a fixed
  target*; the reference driver's behavior is a *different target* on pass
  1. Whether they address a shared underlying mechanism is unknown.

## What this diff cannot show

- What `wIndex=0x20`/`0x18` actually mean (F-014) — confirmed the
  reference driver polls them near positioning feeds, not what they signal.
- Slope table *content* for either the reference feed or second feed
  specifically (by design — never decoded). Issue #33 already asks
  specifically "does the reference driver ever upload `SLOPE_TABLE_FAST`
  for positioning feeds" — answered structurally above (same table
  *selected* as pyopticfilm's default), but content-level confirmation is
  out of reach under this project's constraints.
- Anything about the rare >150px 7200dpi spike issue #33 describes — this
  dataset has only one 7200dpi multi-pass capture (capture 3, 3 real passes
  + 1 anomalous first pass) and it does not show an obvious large-spike
  event in the register/timing log (cycle 1's total duration is ~4.5s
  longer than cycles 2-4, not an order-of-magnitude anomaly). Would need
  many more repeat captures at 7200dpi to characterize an event the issue
  itself says occurs roughly 1 session in 4-5.

---

## Draft issue comment (`drafts/issue-33-comment.md` has the postable version)

Posted 2026-08-30: https://github.com/jboneng/pyopticfilm/issues/33#issuecomment-5464019501
