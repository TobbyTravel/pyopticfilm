# OpticFilm 8100 V2 hardware reference (capture-derived)

This document is a capture-evidenced reference for the OpticFilm 8100 (V2)
(`07b3:1824`, GL128 ASIC), built from 10 USB captures (USBPcap, Windows) of
a reference driver, taken 2026-08-30. Every number here cites a capture file
and a packet index or index range so it can be re-checked against the raw
data. It supersedes nothing automatically — where it disagrees with existing
model tables or PR #30, both are stated and the disagreement is left open
unless explicitly marked resolved.

**2026-09-05:** the confirmed register-meaning conclusions here (LPERIOD,
DEPTH_A/DEPTH_B, dummy, the 0x21 feed-probe, and the hardware-incident
safety lessons) have been re-derived, in their own words with their own
citations, into a proper confidence-tagged register catalog upstream at
[jboneng/pyopticfilm#54](https://github.com/jboneng/pyopticfilm/pull/54).
This branch/PR is no longer a merge candidate — it's a running scratchpad
of the remaining open/low-confidence questions, kept to feed future
debugging sessions.

Full working detail, evidence trails, and open questions live in
`docs/hw-ref/8100v2/`: `findings.md` (numbered findings F-001 through F-013),
`claims-inventory.md` (every pre-existing claim, checked against the
captures), `capture-inventory.md`, `register-program-by-dpi.md`,
`phases/<n>.md`. This document is a synthesis of that material, not a
replacement for it — follow the citations back to `findings.md` for the
full observation/interpretation/recommendation breakdown behind any claim
here.

**Provenance caveat that shapes everything below:** the existing V2 model
constants (`model_8100_v2.py`, from PR #30) and the SE tables they inherit
were **not** derived from this capture set. PR #30 cites a different
capture file (`04_color_7200.pcapng`) not present in this dataset or
recoverable from the project's captures repository; the SE tables are
explicitly attributed to a different reference driver session entirely.
This document's data is an independent re-derivation from a third, distinct
capture session — agreement with prior work is evidence, not a given.

## Capture inventory

| # | resolved DPI | description | packets | duration | bulk-IN total |
|---|---|---|---|---|---|
| 1 | n/a | power on/off, driver open, no scan reaches shading/image | 2,407 | 30.1s | 0 |
| 2 | n/a | power on/off, no software open (pure enumeration) | 15 | 6.4s | 0 |
| 3 | 7200 | 3-pass request → 4 acquisition cycles (see below) | 119,872 | 420.1s | 1.76GB |
| 4 | 7200 | single pass, default crop, exposure locked 1.0 (nominal) | 31,364 | 97.7s | 397MB |
| 6 | 1200 | single pass, default crop | 8,456 | 19.7s | 13.4MB |
| 7 | 600 | single pass, default crop (DPISET=100 shared floor; 300/150 not fully excluded) | 6,195 | 15.4s | 4.1MB |
| 8 | 1800 | single pass, default crop | 10,908 | 32.2s | 24.8MB |
| 9 | 2400 | single pass, full sensor / no crop | 13,492 | 36.1s | 47.0MB |
| 10 | 3600 | single pass, full sensor / no crop | 18,341 | 43.3s | 105.7MB |
| 11 | 7200 | single pass, full sensor / no crop | 32,252 | 96.0s | 423MB |

File 5 (readme: 1200dpi, exposure locked 2.0) is **missing from the
dataset** — no exposure-register variation is observable in this document
as a result (§ Exposure). Resolved DPI is derived from the `DPISET`
register, not the file's original label — file 11's label ("3600dpi") was
wrong; see `findings.md` F-001.

## USB framing

- Vendor requests: `bmRequestType=0xC0`(IN)/`0x40`(OUT), `bRequest=0x04`
  (`REQUEST_BUFFER`). Register writes: `wValue=0x83` (`VALUE_SET_REGISTER`,
  `|0x100` for addresses above `0xFF`), payload = `(addr, value)` byte
  pairs. Register reads: `wValue=0x8E` (`VALUE_GET_REGISTER`),
  `wIndex = 0x22 + (addr << 8)`, 2-byte response `(value, link_status)`.
  Buffer preambles (announcing an upcoming bulk transfer):
  `wValue=0x82` (`VALUE_BUFFER`), payload = 8 bytes,
  `(bulk_addr: u32 LE, bulk_size: u32 LE)`. All consistent with
  `src/pyopticfilm/usb/protocol.py`'s existing constants — **confirmed**,
  not a new finding.
- **Capture-level framing detail** (not previously documented): every
  control transfer in these captures appears as exactly two USBPcap
  packets — a "submit" (stage 0) carrying the full 8-byte setup plus, for
  an OUT request, the payload already concatenated; and a "complete"
  (stage 3) carrying the IN response bytes, or nothing for OUT. There is no
  separate DATA-stage packet. Multiple transfers interleave in the packet
  stream (matched by the USBPcap IRP pointer, not by adjacency) — see
  `tools/capture_ledger.py` docstring and PROGRESS.md's 2026-08-30 Phase 1
  entry.
- Preamble `wIndex` meaning (from `Gl128Registers`, confirmed against every
  capture — F-003): `0x00` = RAM/calibration reads (shading, AFE probes),
  `0x01` = AHB table uploads (motor slope, per-channel exposure), `0x08` =
  image stream.
- **Image bulk chunking (contradicts the existing decoder's assumption,
  F-004):** `tools/scanlab/capture_pcap.py` assumes a single fixed
  65,508-byte URB per line at 7200dpi (a different reference driver's
  convention). This dataset's reference driver instead chunks the image
  stream into blocks of tens of kilobytes that scale with DPI (600dpi:
  ~4.6KB; 1200: ~9.7KB; 1800: ~14.8KB; 2400: ~20KB; 3600: ~30KB; 7200:
  ~60-62KB), each DPI showing two dominant sizes 512 bytes apart (a status
  word occasionally glued onto the last chunk — matches the decoder's own
  `image_chunk_mode` heuristic, just not its fixed 7200 constant). See
  `findings.md` F-004 for the full per-file table.
- **Verified image-bulk-size formula (F-012):**
  `announced_bulk_size = LINCNT_register × width_px × 3(channels) ×
  2(16-bit)`, `width_px = round((ENDPIXEL−STRPIXEL) × dpi / 7200)` —
  exact, zero-byte-difference match on all 7 single-pass captures across 5
  DPIs. Notably, `LINCNT_register` is the **output line count directly**
  here — no ×4 multiplier and no /2 "buffer rows" halving, contradicting
  the SE model's `image_lincnt_per_line=4` / `usb_image_lincnt_half_lines=
  True` (both SE/original-reference-driver-specific conventions that do not
  carry over to V2 under this dataset's driver — see `findings.md` F-012).

## Register map (GL128, as used by V2)

Addresses from `src/pyopticfilm/asic/registers.py::Gl128Registers`,
cross-checked against every capture in this dataset:

| addr | name | width | meaning | status this session |
|---|---|---|---|---|
| 0x01 | scan control | 8-bit | `SCAN`(0x01)\|`SHDAREA`(0x02)\|`STAGGER`(0x10)\|`DVDSET`(0x20) | confirmed (bits match code) |
| 0x02 | motor | 8-bit | `MTRREV`(0x04)\|`FASTFED`(0x08)\|`MTRPWR`(0x10)\|`AGOHOME`(0x20) | confirmed — every captured image pass shows `0x30` (MTRPWR\|AGOHOME) |
| 0x03 | lamp | 8-bit | `LAMPPWR`(0x10)\|`XPASEL`(0x20)\|`AVEENB`(0x40) | confirmed — every captured image pass shows `0x30` (XPASEL\|LAMPPWR) |
| 0x25-27 | LINCNT | 24-bit BE | image line count | **semantics corrected for V2** — see F-012 above |
| 0x28-2A | LPERIOD | 24-bit BE | per-line pixel-clock budget | **contradicted at 7200dpi, not applied** — captures show 15914/15999, not the model's 16035; matches closely (±18) at 600-3600dpi. Not corrected: the two captures disagree with each other too. See F-009 |
| 0x2B | dummy | 8-bit | per-line pixel-clock padding | **corrected at 7200dpi, opt-in only (`POF_GL128_V2_FIX_DUMMY`, default off)**: 15 vs. the default 23/0x17, matching the second session's captures; matches exactly at 600-3600dpi. Reverted from default-on after a 2026-08-30 hardware fault, cause not yet isolated — see F-013 and PROGRESS.md |
| 0x2C-2D | DPISET | 16-bit BE | `dpi/6` at ≥600dpi, floors at 100 below | **confirmed exactly** at every checked DPI |
| 0x33 | DEPTH_A | 8-bit | `0x04`=16-bit, `0x1F`=8-bit | **corrected, opt-in only (`POF_GL128_V2_FIX_DEPTH_A`, default off → unset/0x1F)**: 0x04 when enabled — every capture shows this during the real image pass. Reverted from default-on after a 2026-08-30 hardware fault, cause not yet isolated — see F-013 and PROGRESS.md |
| 0x37 | IR LED | 8-bit | bit 2 enables IR (n/a — V2 has no IR) | not applicable to this model |
| 0x3D-3F | FEEDL | 24-bit BE | feed distance, steps | see § Positioning below |
| 0x7D-7F | EXPOSURE | 24-bit BE | base exposure | confirmed `14000` in every capture (no non-nominal exposure capture available — capture 5 missing) |
| 0x82-84 | STRPIXEL | 24-bit BE | native (7200dpi) window start | confirmed native-unit, resolution-independent per crop (F-012 formula depends on this) |
| 0x85-87 | ENDPIXEL | 24-bit BE | native (7200dpi) window end | same as STRPIXEL; ~0.27-0.28mm narrower total span than the model's `x_size_ta_mm` implies at every crop tested — small, unresolved, see `claims-inventory.md` C-021 |
| 0xA5, 0xAB | pixel clock | 8-bit | per-line clock rate | confirmed `1`/`1` at 7200dpi, `2`/`2` elsewhere, matching the model exactly |
| 0xAF | DEPTH_B | 8-bit | paired with 0x33 | confirmed `0xFF` (DEPTH8_B) — but paired with 0x33=`0x04`, a mix the model never programs |
| 0x101 | status | 8-bit (high addr) | `PWRBIT`\|`BUFEMPTY`\|`FEEDFSH`\|`SCANFSH`\|`HOMESNR`\|`LAMPSTS`\|`FEBUSY`\|`MOTORENB` | confirmed bit layout via direct decode during positioning feeds (capture 3) — see § Timing |

## Per-phase sequence (single-pass capture)

Every single-pass capture (4, 6, 7, 8, 9, 10, 11) follows the same
structure (`phases/<n>.md`, generated by `tools/phase_segment.py`):

1. Lamp strobe (0x03 toggled off/on/off/on/off), ~0-0.1s.
2. 5 `wIndex=0x01` AHB-upload preambles (motor slope, exposure tables).
3. AFE probe reads (`wIndex=0x00`, ~3KB), dark shading strip
   (`wIndex=0x00`, ~62KB stationary probe + ~7.8MB full dark strip).
4. Lamp on, more AHB uploads, white shading strip (`wIndex=0x00`, DVDSET
   set, motorized).
5. More AHB uploads, then `FEEDL=28292` (reference feed from home).
6. ~1.0-1.05s later, second `FEEDL` write — value depends on scan area
   (see § Positioning).
7. ~1.4-1.5s later, the image pass: one `wIndex=0x08` preamble, then the
   chunked bulk-IN stream (§ USB framing).
8. Lamp off/on/off sequence marking end-of-pass / park.

Captures 1 (driver open, repeated power-cycling) and 2 (no software, power
on/off) never reach step 3 — capture 1 shows only steps 1-2 repeated per
power cycle (lamp strobe + 2 AHB uploads, no RAM/calib or image traffic at
all); capture 2 shows zero vendor register traffic, pure USB enumeration.
Neither answers "what does the driver do beyond enumeration on open" with a
full register program — it appears the device never gets far enough into
init before being power-cycled again in capture 1's test. See `findings.md`
(Phase 2 entry) / `PROGRESS.md`.

### Capture 3 (3-pass request) — 4 acquisition cycles, not 3

Capture 3 repeats the above sequence **4 times**, not the 3 the requested
pass count implies. Cycles 2-4 are identical in structure and timing
(~76.7s each, image-pass-start to next-cycle-lamp-off). Cycle 1 differs:
its second `FEEDL` is **13128** instead of the 13486 every other cycle (and
every other single-pass capture) uses, and its total cycle time is ~4.5s
longer (~81.3s). Capture 3 is confirmed (by pcapng timestamp) to be this
session's first-ever real scan — captures 1 and 2, which ran earlier, never
reach an image pass. Whether the 13128-vs-13486 difference is a genuine
"first scan of a session" behavior or specific to a multi-pass request is
**not resolved** by this dataset (the two are confounded: capture 3 is
both). See `findings.md` F-005/F-007/F-008 for the full evidence and both
open hypotheses.

## Positioning (FEEDL)

Two feeds precede every image pass:

1. `FEEDL=28292` — constant across every capture and cycle in this
   dataset (8 occurrences). Matches `feed_to_reference_steps` unchanged.
2. Second `FEEDL`, clustering into three distinct values by scan area:
   - **~13484-13488** (default crop — captures 3 cycles 2-4, 4, 6, 7, 8):
     matches none of the model's current constants (SE default 13704, V2
     override 13128).
   - **~13198-13200** (full-sensor/no-crop — captures 9, 10, 11).
   - **13128** (capture 3, cycle 1 only): matches
     `Model8200iSE.feed_to_scan_top_steps` ("top of TA window") exactly,
     and PR #30's V2 override value exactly — but does not match ordinary
     full-frame scans in this dataset.

**This contradicts PR #30's `feed_to_scan_steps=13128`** for ordinary
full-frame scans: 7 independent captures across 5 DPIs all use ~13486, not
13128. See `findings.md` F-007/F-008.

Status-register polling during both feeds runs at roughly 15-16ms
intervals, watching a bit combination that includes `FEEDFSH` and
`HOMESNR` (decoded directly from capture 3, packet range 2913-3879 —
see `issue-33-diff.md`).

## Motor slope tables (2026-08-30, real-hardware fault root cause)

`_upload_fast_slopes()` loads a 512-byte, 256-entry `u16` little-endian
table into two AHB windows (`AHB_SLOPE_SCAN`/`AHB_SLOPE_FAST`,
`0x1000C000`/`0x10010000`) before every feed — this governs the motor's
accel/decel ramp for that feed. `pyopticfilm` always loaded
`SLOPE_TABLE_FAST` for both feeds in `position_for_full_frame_scan()`,
unconditionally.

**Two independent real captures contradict this.** Extracting the actual
AHB upload payload (wIndex=0x1 preamble, matching bulk address/size,
followed bulk-OUT) from every single-pass capture in this dataset with a
real scan (4, 6, 7, 8, 9, 10, 11) and the multi-cycle capture 3, plus the
originally-cited vendor capture (`04_color_7200.pcapng`, recovered
2026-08-30 — see `PROGRESS.md`'s "v1.3.0 confirmation run" entry), all
agree exactly: every positioning feed pair uploads `SLOPE_TABLE_FAST` for
the **first** (reference) feed but `SLOPE_TABLE_SLOW` for the **second**
(final positioning) feed — byte-for-byte exact match, every occurrence, no
exceptions. `SLOPE_TABLE_FAST` and `SLOPE_TABLE_SLOW` are the exact table
constants already in `tables_8200i_se.py`; nothing new was decoded, this
just confirms which one goes where.

**Scoped to the 8100 V2 only, not the shared `Gl128` code path.** `Gl128`
is the same ASIC driver class for both the V2 and the 8200i SE, but every
capture behind this fix is V2-only — the raw `SLOPE_TABLE_FAST`/
`SLOPE_TABLE_SLOW` values are SE-session-derived, but whether the SE's
real driver also uses fast-then-slow for its two positioning feeds has
not been verified. `Model.use_slow_final_positioning_feed` (default
`False`, unchanged SE behavior) is `True` only on `Model8100V2`.

**This was the root cause of a serious real-hardware mechanical fault**
(motor overspeed/hard-stop sound, several hard power-offs) discovered
during 2026-08-30 hardware testing of this document's other corrections.
`Gl128._upload_fast_slopes()` gained a `use_slow` parameter and
`position_for_full_frame_scan()`'s second feed now passes
`use_slow_slope=True` — the first feed and the general single-feed
`feed()` API are unaffected, matching exactly what both captures show.
Verified clean on real hardware across 6+ consecutive full scans at
1200/1800/3600/7200dpi after the fix, following repeated faults before it
under identical conditions. Upstream: `jboneng/pyopticfilm#39` (issue),
`#40` (fix).

Full incident and investigation log, including the wrong turns taken
before finding this (feed-completion debounce timing, the vendor probe,
priming): `docs/hw-ref/8100v2/PROGRESS.md`, entries from "HARDWARE
INCIDENT" through the final "Confirmed" entry.

## Per-DPI register program

Full table with both captured and `compute_geometry()`-predicted values:
`docs/hw-ref/8100v2/register-program-by-dpi.md`. Summary:

| DPI | DPISET | LPERIOD | dummy (0x2B) | pixel clock | EXPOSURE |
|---|---|---|---|---|---|
| 600 | 100 (confirmed) | 11062 (≈model 11064) | 1 (confirmed) | 6/6 | 14000 |
| 1200 | 200 (confirmed) | 11273 (≈model 11277) | 2 (confirmed) | 2/2 | 14000 |
| 1800 | 300 (confirmed) | 11484 (≈model 11490) | 2 (confirmed) | 2/2 | 14000 |
| 2400 | 400 (confirmed) | 11709 (≈model 11703) | 3 (confirmed) | 2/2 | 14000 |
| 3600 | 600 (confirmed) | 13425 (≈model 13407) | 4 (confirmed) | 2/2 | 14000 |
| 7200 (file 4) | 1200 (confirmed) | **15914** (model: 16035) | **15** (model: 23) | 1/1 | 14000 |
| 7200 (file 11) | 1200 (confirmed) | **15999** (model: 16035) | **16** (model: 23) | 1/1 | 14000 |

DPISET, pixel clock, and EXPOSURE match the current model exactly at every
DPI. LPERIOD and dummy match closely (noise-sized deviations) at 600-3600
and diverge meaningfully only at 7200 — the one DPI issue #35 reports the
jaggedness symptom on. See `issue-35-diff.md` for the ranked candidate
analysis.

## Exposure

`REG_EXPOSURE` (0x7D) is `14000` in every captured image pass, including
the "exposure locked, RGB 1.0" capture (file 4) — consistent with 1.0
being the driver's nominal multiplier, not a contradiction. **No capture in
this dataset exercises a non-nominal exposure value** — the one capture
that would (1200dpi, RGB exposure 2.0) is missing (file 5). The relationship
between the driver's exposure UI control and `REG_EXPOSURE` / `LPERIOD` /
the AHB exposure-table content remains untested.

## Unknowns / open questions

Two of these (1 and 4) now have opt-in experimental overrides for hardware
A/B testing via Scan Lab — see `docs/hw-ref/8100v2/ab-testing-guide.md`.

1. Is the 13128-vs-13486 first-scan feed difference (§ Positioning)
   specific to "first scan of a session" or "first pass of a multi-pass
   request"? This dataset cannot distinguish them (confounded in capture
   3). Needs a capture that decouples the two.
2. Is `LPERIOD`'s 7200dpi variance (15914 vs. 15999, files 4 vs. 11) crop-
   dependent, session-dependent, or something else? Needs a same-crop,
   repeated 7200dpi capture set.
3. File 7's exact DPI (600 vs. 300 vs. 150) — `DPISET=100` is shared by all
   three; needs output pixel dimensions, not recorded for this capture.
4. Whether correcting `LPERIOD` and/or dummy (0x2B) at 7200dpi to the
   captured values changes issue #35's jaggedness — still untested on real
   hardware (see `issue-35-diff.md`). **Not the same question as the
   2026-08-30 mechanical fault** (see § Motor slope tables) — that fault's
   root cause (the slope-table selection) is unrelated to LPERIOD/dummy.
   Four of the five originally-suspected `POF_GL128_V2_FIX_*` flags
   (`FEED_STEPS`, `DUMMY`, `LINCNT`, `DEPTH_A`) were ultimately not
   implicated in the mechanical fault; the fifth, `FEED_PROBE`, turned out
   to be a real, *separate* hazard (see item 5) — their correctness for the
   *jaggedness* symptom specifically remains exactly as open as before.
5. **Confirmed hazardous, retired from the codebase (F-014)**:
   `pyopticfilm`'s `0x21` vendor-probe feed-completion polling does **not**
   match this driver — it never polls `wIndex=0x21` at all, in any
   context, so its *response* carries no real signal for V2. Disabling the
   query entirely was tried twice as a "fix" (briefly the default, then an
   opt-in `POF_GL128_V2_FIX_FEED_PROBE` flag) and **faulted real hardware
   every single time it was tried, 3-for-3, including once more under the
   already-fixed slope table** — a real, separate hazard from the
   mechanical-fault bug, not an artifact of it. The flag has been removed
   from the codebase entirely; `Model8100V2` unconditionally inherits the
   SE's `feed_probe_index=0x21` with no override, and the env var is now a
   no-op. Leading (unproven) hypothesis: the real driver polls a
   *different* vendor probe (`wIndex=0x20`, constant response 0x55, and
   `wIndex=0x18`, 2 or 18) continuously from device-open onward — not
   feed-specific, more likely a generic engine-busy/heartbeat check than a
   completion signal — and pyopticfilm's own `0x21` poll may be
   incidentally serving an equivalent "keep the ASIC fed with vendor-
   request traffic" role during feeds, despite its response being
   meaningless. `0x20`/`0x18`'s actual meaning remains unresolved and was
   deliberately not adopted as a replacement probe. Do not reintroduce a
   way to disable `0x21` on V2 without a real fault capture confirming the
   mechanism first.
6. The small (~0.27-0.28mm) STRPIXEL/ENDPIXEL X-window gap vs. the model's
   `x_size_ta_mm` — real, consistent across every crop tested, not chased
   to a root cause.
7. PR #30's own cited `LINCNT=29012` value is inconsistent with this
   session's verified ×1 LINCNT formula (F-012) — it only makes sense under
   the *old* ×4 convention. Whether V2 genuinely uses different LINCNT
   semantics at the much larger full-TA-window travel PR #30 was testing
   (untested regime in this dataset) is unresolved.
8. Capture 5 (1200dpi, exposure 2.0 locked) is missing — blocks any real
   exposure-register comparison.

## Change log against prior documentation

- `scanner-validation.md`'s GL128 priming rationale describes pyopticfilm's
  own discarded-pass mechanism; nothing in this dataset contradicts it
  directly, but § Positioning above documents a *different* first-scan
  phenomenon in the reference driver that isn't the same mechanism — see
  open question 1. **2026-08-30 update**: `Model8100V2.default_gl128_prime`
  is now `False` (SE unchanged at `True`) — every real-hardware trial that
  confirmed the slope-table fix (§ Motor slope tables) also had priming
  off, so this is precautionary, not a claim that priming itself was ever
  hazardous; priming ON with the slope fix has not been separately
  isolated. Fully overridable via `Scanner.scan(gl128_prime=True)` or Scan
  Lab's existing checkbox.
- **Attempted, then reverted after a real-hardware fault (2026-08-30):**
  `feed_to_scan_steps` 13128→13486 (§ Positioning); `dummy_by_dpi[7200]`
  23→15 (§ Per-DPI register program); `image_lincnt_per_line` 4→1 and
  `usb_image_lincnt_half_lines` True→False (§ USB framing, F-012); new
  `image_depth_a=0x04` override (§ Register map, F-013); new
  `feed_probe_index=None` override (F-014). All four were applied as
  defaults and hardware-tested, producing a serious mechanical fault
  (motor overspeed/hard-stop, two hard power-offs) not reproduced on clean
  `main`. **Every one is now an opt-in `POF_GL128_V2_FIX_*` env var,
  default off** — see `docs/hw-ref/8100v2/ab-testing-guide.md`.
- **Root cause found and fixed (2026-08-30), after extensive isolation
  testing of the five flags above found none of them individually
  responsible.** See § Motor slope tables above. None of the five flags
  were the cause of *this* mechanical fault. `FEED_STEPS`, `DUMMY`,
  `LINCNT`, `DEPTH_A` remain opt-in/default-off, unrelated to this fix.
  Whether their underlying *values* are still worth applying for other
  reasons (issue #35's jaggedness) is a separate, still-open question —
  see Unknowns item 4.
- **`FEED_PROBE` retested under the fixed platform and retired
  (2026-08-30, same day):** unlike the other four, `feed_probe_index=None`
  faulted real hardware again even with the slope-table fix in place —
  3-for-3 fault, zero clean, the whole project. A real, separate hazard,
  not an artifact of the fixed bug. Removed from the codebase entirely
  (see Unknowns item 5) rather than left as an opt-in.
- **Deliberately left unresolved, never applied at all (unrelated to the
  incident above):** `max_image_lincnt_by_feed2={13128: 29012}` and
  `ladder_feed2_steps=13128` (open question 7 — not exercised by this
  dataset) and `lperiod_by_dpi[7200]=16035` unchanged (this dataset's own
  two 7200dpi captures disagree with each other, 15914 vs. 15999 — issue
  #35's leading suspect, picking either without resolving that is a guess
  this project won't ship without a hardware check first;
  `POF_GL128_V2_LPERIOD_7200` overrides it for A/B testing). The
  white-shading dummy override (`0x10` at 7200dpi) was not re-checked this
  session.
