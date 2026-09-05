# 8100 V2 capture findings

Numbered findings. Each: **claim** → **evidence** (capture #, packet/event
index, register/value) → **status** (confirmed / contradicted / new /
unverified) → **affects** (file:line, doc section, issue #).

Observation, interpretation, and recommendation are kept separate. Two
disagreeing captures are both recorded, not averaged away.

**Note (2026-09-05):** the confirmed register-meaning content from F-009,
F-013, and F-014 (plus the hardware-incident safety lessons from
`PROGRESS.md`) has been re-derived, in its own words with its own
citations, into a proper confidence-tagged register catalog upstream at
[jboneng/pyopticfilm#54](https://github.com/jboneng/pyopticfilm/pull/54).
This file no longer needs to carry that as a standalone claim — everything
below is either specific to this capture set (not general register
meaning) or still an open question. This PR is now a running scratchpad of
that open/low-confidence material, meant to feed future debugging
sessions, not a merge candidate itself.

---

### F-001 — File 11 is 7200 dpi, not 3600 dpi as its readme.txt label claims

**Observation:** `DPISET` register (0x2C hi / 0x2D lo, 16-bit BE) sampled
immediately before file 11's largest `VALUE_BUFFER` preamble (wIndex=0x08,
announced 443,613,456 bytes) reads `0x04B0` = 1200. `dpi = dpiset × 6 = 7200`.
Source: `tools/capture_ledger.py` → `docs/hw-ref/8100v2/ledgers/
11.events.json`, register-write events near index of the largest preamble;
aggregated in `docs/hw-ref/8100v2/capture-inventory.json` (`file: 11,
dpiset_register: 1200, resolved_dpi: 7200`).

**Interpretation:** the readme.txt label ("3600dpi... Output 10368×7200
36.6×25.4mm") was already internally inconsistent — 10368 px / 36.6 mm ≈
7200 dpi, not 3600 — and the register confirms 7200. This matches the user's
own correction mid-session.

**Status:** confirmed (register-derived, independent of the user's claim).

**Affects:** `docs/hw-ref/8100v2/claims-inventory.md` G-003 (resolved); any
future per-DPI table must treat file 11 as a **second** 7200dpi capture
(alongside files 3 and 4), not a 3600dpi one. There is now no capture in
this dataset that is unambiguously 3600dpi by user's original count — file
10 fills that role (see F-002).

---

### F-002 — Files 6, 8, 9, 10 resolve to their readme labels; file 7 is ambiguous but consistent

**Observation:** DPISET-derived DPI for file 6 = 1200 (label: 1200, match),
file 8 = 1800 (label: 1800, match), file 9 = 2400 (label: 2400, match),
file 10 = 3600 (label: 3600, match). File 7 samples `DPISET = 100`, which is
the shared floor value the ASIC programs for 150/300/600 dpi alike
(`model_8200i_se.py` `_REGISTER_DPISET_SE`) — the register alone cannot
distinguish those three; 100 is at least consistent with the 600dpi label
(not contradicted). Source: same as F-001, all 10 files in
`capture-inventory.json`.

**Status:** confirmed for files 6/8/9/10; unverified-but-consistent for
file 7 (needs the Phase 3 output-pixel-dimension cross-check to fully
confirm 600 vs 300 vs 150).

**Affects:** only file 11's numbering was actually wrong; files 6-10 can be
used at face value for Phase 2/3, with file 7 flagged for one more check.

---

### F-003 — the reference driver uses `wIndex=0x08` for the main image-pass buffer preamble, matching the SE model's original reference driver convention baked into `capture_pcap.py`

**Observation:** every single-pass capture with a real scan (4, 6, 7, 8, 9,
10, 11) shows exactly the same preamble wIndex histogram: `0x0×7, 0x1×17,
0x8×1` — one `wIndex=0x08` preamble per capture, always the largest by a
wide margin (megabytes vs. small `0x0`/`0x1` preambles, which are
shading/calib RAM reads). Capture 3 (3-pass) shows `0x0×28, 0x1×68, 0x8×4` —
four `0x08` preambles for three passes plus one extra (see F-005). Source:
`docs/hw-ref/8100v2/capture-inventory.md` "Preamble wIndex histogram per
file" table.

**Status:** confirmed. `_IMAGE_BUFFER_WINDEX = 0x08` in
`tools/scanlab/capture_pcap.py:37` holds for the reference driver, not just the SE model's original reference driver.

**Affects:** `claims-inventory.md` C-025 (partially confirmed — see F-004 for
the part that does NOT hold).

---

### F-004 — the reference driver's 7200dpi image bulk is NOT one fixed 65508-byte URB per line; it is chunked in ~60KB blocks that scale with DPI, contradicting the decoder's hardcoded assumption

**Observation:** bulk-IN size histograms (`docs/hw-ref/8100v2/capture-
inventory.md` "Top bulk-IN sizes per file") show, per resolved DPI:

| DPI | file | dominant chunk sizes (bytes) |
|---|---|---|
| 600 | 7 | 4608, 4096 |
| 1200 | 6 | 9728, 9216 |
| 1800 | 8 | 14848, 14336 |
| 2400 | 9 | 19968, 20480 |
| 3600 | 10 | 30208, 30720 |
| 7200 | 4 | 60416, 59904 |
| 7200 | 11 | 61440, 60928 |
| 7200 (pass 1-3) | 3 | 60416, 61952, 59904 |

Two things: (1) chunk size scales roughly linearly with DPI, consistent with
one-or-more-lines per URB, not a fixed 65508-byte constant claimed for 7200
in `tools/scanlab/capture_pcap.py:343-345` ("7200 → 65508"); (2) at every
DPI there are two dominant sizes 512 bytes apart (e.g. 9728/9216,
14848/14336), matching the decoder's own `image_chunk_mode` comment that
"USB sometimes glues the status word onto the image URB" — that specific
heuristic looks right, but the fixed 65508 constant for 7200 does not.

**Interpretation:** the reference driver appears to batch multiple ASIC output lines per
USB bulk transfer, with the batch size scaling with per-line byte width and
capped somewhere near a USB transfer ceiling (~60-62KB, close to but not
identical to pyopticfilm's own `BULK_MAX_SIZE=0xF000=61440` — likely
coincidental, both bumping into similar USB/host-controller transfer
limits, not evidence the reference driver uses the same constant). The exact
lines-per-chunk ratio at each DPI needs the Phase 3 register cross-check
(LINCNT, line_bytes) to pin down precisely — not computed here since it
requires touching bulk payload structure, which Phase 3 will do via sizes/
counts only, never content.

**Status:** contradicted (the specific "7200 → 65508 single URB per line"
claim). The general wIndex=0x08 framing (F-003) and the ±512 glue heuristic
are not contradicted.

**Affects:** `tools/scanlab/capture_pcap.py:343-345,408-412` (the 7200dpi
special-case in `_image_urb_line_bytes`/`carve_bulk_after_preamble` needs
re-deriving from the reference driver captures, not assumed from the SE model's original reference driver); `claims-
inventory.md` C-025 (contradicted, this part); Phase 3 register-program
work should compute expected line_bytes per DPI and compare directly against
these chunk sizes.

---

### F-005 — Capture 3 (3-pass) has 4 `wIndex=0x08` preambles, not 3

**Observation:** file 3's preamble wIndex histogram shows `0x8×4`, one more
than the 3 passes described in the readme ("A 7200dpi scan on the reference driver.
Option Number of passes: 3"). Source: `capture-inventory.md` preamble
histogram table.

**Status:** new. Not yet interpreted — could be a prescan/preview pass (the
mission brief's "unknown whether autocrop adds an extra pass" question,
`readme.txt` line 1) plus 3 real passes, or could be a false positive (a
small-but->1MB shading/calib buffer that happens to also use wIndex=0x08).
Needs Phase 2 phase segmentation (register state + timing around each of the
4 preambles) to resolve — this is squarely the "does autocrop add an extra
pass" question from the mission brief and directly relevant to issue #33
(repositioning/homing count between passes).

**Affects:** Phase 2 plan item "determine whether autocrop adds a prescan
pass in every capture"; issue #33 diff (Phase 4).

---

### F-006 — The existing `capture_pcap.py` parser succeeds (register/preamble/bulk-count extraction) on all 10 the reference driver captures, including the 1.77GB file 3, with no exceptions and no excessive time/memory cost observed

**Observation:** `analyze_usbpcap()` ran on all 10 files (register-writes/
preambles/bulk-IN-count extraction only, no image decode) without error;
slowest was file 3 at 2.54s. Its counts (register_writes, buffer_preambles,
bulk_ins) match `tools/capture_ledger.py`'s independent counts exactly on
every file (e.g. file 3: 2848 register writes / 100 preambles / 53765
bulk-INs on both). Source: this session's interactive test run (not
persisted as a script — reproducible via `analyze_usbpcap(Path(...))` on any
file).

**Status:** confirmed. The parsing layer (register/preamble/bulk-count
extraction) is sound and cross-validated by two independent implementations
of the pcapng/USBPcap framing; the *problem* identified in F-004 is specific
to the image-decode-time DPI-dependent line-size assumption, not the base
parser.

**Affects:** decision for Phase 1 tooling item 3 ("extend vs rewrite"): the
base parsing in `capture_pcap.py` does not need replacing. The new
`tools/capture_ledger.py` written this session is complementary — it adds
timestamps (which `capture_pcap.py` drops entirely) and register-read/
status-poll decoding (which `capture_pcap.py` never implemented, only
writes) — both needed for Phase 2-4's timing analysis (#33/#35) and were
not achievable by reusing `capture_pcap.py` alone.

---

### F-007 — Capture 3 is 4 full acquisition cycles, not 3; the extra cycle is the session's first-ever scan, and it uses a different second-feed value than every other capture in this dataset

**Observation:** `docs/hw-ref/8100v2/phases/3.md` (produced by
`tools/phase_segment.py` from the timestamp-preserving ledger) shows 4
identical cycles (dark shading → white shading → `FEEDL=28292` → second
`FEEDL` write → `wIndex=0x08` image preamble), not the 3 the readme.txt
label describes ("Number of passes: 3"). The second `FEEDL` value differs:
cycle 1 uses **13128**; cycles 2, 3, 4 use **13486**. Every other capture in
this dataset that reaches an image pass (files 4, 6, 7, 8, 9, 10, 11 — all
single-pass, all chronologically *after* capture 3 in the same the reference driver
session, confirmed by pcapng timestamps: capture 3 runs 1788019652-1788020072,
capture 4 starts 1788020196) uses **13486** directly, with no 13128 step at
all. Capture 3 is also confirmed the session's first real scan: capture 1
(power-cycling) has zero RAM/calib or image buffer preambles at all (its 6
preambles are all `wIndex=0x1` AHB uploads — no dark/white shading, no image
pass ever ran), and capture 2 is pure enumeration (zero vendor register
traffic). So the very first ASIC acquisition in the whole session is capture
3's cycle 1, at feed2=13128; every subsequent acquisition in the dataset
(cycles 2-4 of capture 3, and all of captures 4/6/7/8/9/10/11) uses 13486.

**Interpretation:** two competing hypotheses, neither confirmed:
1. the reference driver (or the GL128 firmware) programs a different, shorter second-feed
   target specifically for the first acquisition of a session — a
   deliberate settle/reference position distinct from ordinary scans. This
   would be a genuinely new mechanism for issue #33's "first scan lands
   differently" story (`scanner-validation.md`'s C-034/C-035, about
   pyopticfilm's own priming), except here it manifests as the reference driver itself
   targeting a different register value, not passive positioning error at a
   fixed target.
2. Cycle 1 is specifically tied to the "3 passes" multi-pass request (e.g. a
   reference/metering pass the reference driver runs before a multi-pass sequence) and
   would not appear in a single-pass session regardless of session order.
   This dataset cannot distinguish hypothesis 1 from 2 because capture 3 is
   both "first in session" and "the only multi-pass capture" — confounded.
   Resolving this needs a capture where a multi-pass scan is NOT the
   session's first scan, or a single-pass scan that IS the session's first
   scan (neither exists in this dataset).

**Status:** new (observation solid; interpretation open — recorded as a
question, not answered).

**Affects:**
- **`claims-inventory.md` C-001, C-005, C-007, C-008 — contradicted.**
  PR #30 claims `feed_to_scan_steps=13128` for "the V2's full-frame colour
  scan" generally (`model_8100_v2.py:90`, citing a different, unavailable
  capture file `04_color_7200.pcapng` frame 2999). Our the reference driver captures show
  13128 used **only** on what looks like a first-scan-of-session special
  case, and **13486** — a value that does not appear in ANY existing
  pyopticfilm table (SE `feed_to_scan_steps=13704`, SE
  `feed_to_scan_top_steps=13128`, V2 override `13128`, SE
  `ladder_feed2_steps=13560`, V2 override `13128`) — for every ordinary
  full-frame scan, at every DPI, single-pass or repeat-pass alike. If
  pyopticfilm's `feed_to_scan_steps=13128` is applied on every scan (not
  just session-first), it is now contradicted by 7 independent the reference driver
  captures across 5 different DPIs (1200/1800/2400/3600/7200).
- `C-004` (`max_image_lincnt_by_feed2 = {13128: 29012}`) is affected
  indirectly: the fixture key itself (13128) is the session-first-only
  value per this finding, not the steady-state one; a fixture entry for
  13486 is missing.
- Issue #33 diff (Phase 4): this is now the strongest concrete lead in the
  dataset for "why does the first scan of a session land differently" —
  more specific than generic positioning jitter, because it is a **different
  commanded target**, not just error around the same target.

---

### F-008 — Second-feed (FEEDL2) clusters into three distinct values by scan area, and capture 3's odd first-pass value (F-007) exactly matches the SE's documented "top of TA window" constant

**Observation:** second-`FEEDL` values across all captures with a real image
pass:

| capture | dpi (resolved) | readme area | feed2 |
|---|---|---|---|
| 3 (cycle 1 only) | 7200 | "36x24.2mm" (default crop) | **13128** |
| 3 (cycles 2-4) | 7200 | "36x24.2mm" (default crop) | 13486 |
| 4 | 7200 | "36x24.2mm" (default crop) | 13486 |
| 6 | 1200 | (unstated, default crop assumed) | 13488 |
| 7 | 600 | (unstated, default crop assumed) | 13484 |
| 8 | 1800 | (unstated, default crop assumed) | 13488 |
| 9 | 2400 | "maximal sensor area, no crop" | 13200 |
| 10 | 3600 | "maximal sensor area, no crop" | 13200 |
| 11 | 7200 | "maximal sensor area, no crop" | 13198 |

Two clean clusters by stated scan area (13484-13488 for default-crop scans,
13198-13200 for explicit full-sensor/no-crop scans — the ±4-step spread
within each cluster is consistent with per-DPI rounding in a
fractional-position formula, not a meaningful difference), plus capture 3's
outlier first pass at exactly 13128. That value is not a rounding artifact —
it matches `Model8200iSE.feed_to_scan_top_steps = 13128`
(`model_8200i_se.py:429-430`, documented as "top of the TA window") **and**
the V2 override PR #30 shipped for `feed_to_scan_steps`, exactly.

**Interpretation:** the existing code's `feed_to_scan_steps_for_area()`
design (feed2 varies with crop position, full sensor starts higher / smaller
feed2 than a default crop) is directionally validated by this data — full
sensor (13200) < default crop (13486), which is the right ordering. What's
wrong is the specific constants: neither cluster matches any current table
value (SE full-frame 13704, V2 override 13128, SE top-of-window 13128, SE
ladder 13560). The most defensible interpretation of F-007's capture-3
oddity now is that the reference driver's first-ever acquisition of the session
specifically drives to the documented top-of-window position (13128) —
whether as a deliberate reference/settle move or an artifact of some
first-run init path — before settling into ordinary crop-relative feeds for
every later scan. This still does not distinguish "first-of-session" from
"first-of-multi-pass-sequence" (same confounding caveat as F-007).

**Status:** new — observation solid (three clusters, clean separation);
interpretation partially supported (13128=top-of-window is now a directly
confirmed value, not just cited from an inaccessible capture) but the
first-pass mechanism question from F-007 remains open.

**Affects:** Phase 3 should compute `feed_to_scan_steps_for_area()`'s
implied `y1` for the default-crop cluster (~13486) and full-sensor cluster
(~13200) against `scan_window_end_steps=27636` and compare to the model's
current area-fraction formula. Strengthens the F-007 recommendation to
re-derive `feed_to_scan_steps` (steady-state) as ~13486 rather than 13128,
while keeping 13128 documented as a real, reproducible (if not yet
understood) value rather than discarding it.

---

### F-009 — LPERIOD at 7200dpi differs between two the reference driver captures of the same DPI, and neither matches PR #30's claimed value

**Observation:** `docs/hw-ref/8100v2/register-program-by-dpi.md`, built by
`tools/register_program.py` from the register snapshot at each capture's
image-pass start: captured `LPERIOD` (reg 0x28, 24-bit BE) at 7200dpi is
**15914** in file 4 and **15999** in file 11 — an 85-unit difference between
two same-DPI the reference driver captures in the same session. Neither matches
`Model8100V2.lperiod_by_dpi[7200] = 16035` (`model_8100_v2.py:67`, PR #30's
claimed V2-confirmed value, cited from the unavailable
`04_color_7200.pcapng`). At every other DPI the captured value is close to
but not exactly the SE-inherited table value (600: 11062 vs 11064; 1200:
11273 vs 11277; 1800: 11484 vs 11490; 2400: 11709 vs 11703; 3600: 13425 vs
13407) — small (2-18 unit) deviations in both directions, plausibly capture
noise/rounding, but the 7200dpi pair's 85-unit spread between two the reference driver
captures is an order of magnitude larger and does not look like noise.

**Interpretation:** none yet — recorded as observation only. Candidates:
LPERIOD could vary with something not controlled between files 4 and 11
(crop area — file 4 is a default crop, file 11 is full-sensor/no-crop, per
F-008's clusters; or session timing/thermal state), or the single-snapshot
extraction method could be sampling a transitional value if the reference driver
reprograms LPERIOD more than once around the image-pass preamble (not yet
checked — `tools/register_program.py` only takes the last write before the
preamble index, which should be final, but has not been cross-checked
against the full write history for these two files).

**Status:** new / unverified interpretation. The observation itself
(15914 ≠ 15999 ≠ 16035) is solid. **Migrated**: re-derived (own words, own
citations) as a `SUSPECTED` entry in the register catalog at
[jboneng/pyopticfilm#54](https://github.com/jboneng/pyopticfilm/pull/54)
(`tools/register_reference.py`, `REG_LPERIOD`) — the underlying capture
comparison and open question stay here too since they're specific to this
capture set.

**Affects:** `claims-inventory.md` C-002 — **contradicted**: PR #30's single
V2-wide `lperiod_by_dpi[7200]=16035` claim does not hold against either of
our two independent 7200dpi the reference driver captures. `model_8100_v2.py:65-68` needs
re-deriving, and needs to establish whether LPERIOD is crop-dependent before
picking a replacement constant.

---

### F-010 — Captured STRPIXEL/ENDPIXEL/LINCNT and announced image-bulk size do not match `compute_geometry()`'s full-frame prediction at any DPI, by a roughly consistent pattern

**Observation:** in the same register-program-by-dpi table, at every DPI:
- `STRPIXEL` (captured) is ~38-50 native units **higher** than
  `compute_geometry(dpi, model=MODEL_8100_V2).pixel_startx` (e.g. 600dpi:
  290 captured vs 240 computed; 7200dpi file 4: 288 vs 241).
- `ENDPIXEL` (captured) is ~34-190 native units **lower** than
  `pixel_endx` (e.g. 600dpi: 10418 vs 10608; 7200dpi file 4: 10414 vs
  10609) — i.e. the captured X window is narrower on both sides than the
  model's computed full-TA-window span, at every single-pass capture
  **including files 9/10/11, which the readme labels "maximal sensor area,
  no crop."**
- `LINCNT` (captured) is consistently **~4.2x smaller** than the model's
  computed `lincnt_register` at every DPI (e.g. 600dpi: 572 vs 2416; 7200dpi
  file 4: 6851 vs 29012; ratios 4.22-4.24 across all 7 files — unusually
  tight for something that should vary by DPI/crop if it were a real
  content difference).
- Announced image-bulk size (the `wIndex=0x08` preamble's declared byte
  count) is correspondingly far below `compute_geometry()`'s
  `total_bytes` at every DPI (e.g. 7200dpi file 4: 416,239,356 announced vs
  902,389,248 computed — ratio 0.461, which does not equal the LINCNT ratio
  0.236, i.e. the two mismatches are not simply the same mismatch appearing
  twice).

**Interpretation:** not resolved. The very consistent ~4.2x LINCNT ratio
across every DPI looks structural rather than crop-dependent (a genuine
per-DPI content difference would be expected to vary the ratio with the
X-window mismatch above, not track flatly across 600-7200dpi) — plausibly
an artifact of `ScanGeometry.total_bytes`/`lincnt_register` semantics
(`image_lincnt_per_line=4`, "LINCNT/2 buffer rows per output line") not
being applied the same way this extraction script computes vs. how
`compute_geometry()` intends them, rather than a real 4.2x error in the
captures. This needs a careful re-read of `ScanGeometry.total_bytes` /
`Model8200iSE.image_lincnt_per_line` semantics against the announced
preamble size directly (bytes-first, not via `lincnt_register`) before
concluding anything about correctness of either side. Recorded as an open
question, explicitly **not** claimed as a bug in the model or the capture.

**Status:** new / unverified — flagged as the top open item for continued
Phase 3 work, not resolved this session.

**Affects:** blocks a confident per-DPI table entry for LINCNT/STR/END/total
bytes in `claims-inventory.md` (C-004, C-006, C-008, C-013, C-021, C-023,
C-024) until resolved. Do not use the raw numbers in this session's
`register-program-by-dpi.md` as corrected replacement constants without
resolving this first — they are captured-but-not-yet-reconciled data.

---

### F-011 — `REG_EXPOSURE` is 14000 in every captured image pass, including the "exposure locked RGB 1.0" file; this is expected (1.0 = nominal), not a contradiction, but the dataset cannot show exposure varying with the register at all

**Observation:** every row in `register-program-by-dpi.md` shows captured
`EXPOSURE` (reg 0x7D) = 14000, including file 4 (readme: "exposure locked
option enabled, Value RGB exposure: 1.0 (nominal)") and every unlocked-
exposure file (6/7/8/9/10/11). 14000 matches `Model8100V2.exposure_short`
(inherited from SE, `model_8200i_se.py:391`).

**Interpretation:** not a contradiction — 1.0 is documented as the reference driver's
*nominal* exposure multiplier, so an unchanged register at 1.0-locked vs.
unlocked-default is exactly what should happen if the reference driver's UI multiplier
scales linearly from a 1.0 baseline. The capture that would actually
exercise a non-default value — capture 5, 1200dpi with "RGB exposure: 2.0"
— is the one missing from this dataset (G-001). No exposure-register
variation is observable at all in the current 10-file set.

**Status:** confirmed (14000 baseline, as expected), but the underlying
question ("does the reference driver's exposure multiplier scale REG_EXPOSURE linearly,
or LPERIOD, or the AHB exposure-table content?") remains **untestable**
without capture 5 or an equivalent non-1.0 capture.

**Affects:** `claims-inventory.md` C-019 partially confirmed (14000 baseline
only); the originally-planned Phase 3 exposure diff (captures 3-pass-1
unlocked / 4 locked-1.0 / 5 locked-2.0 / 6 unlocked) is reduced to a
3-file comparison with no actual exposure delta to observe — effectively a
no-op until capture 5 (or a substitute) is supplied.

---

### F-012 — F-010 resolved: `image_lincnt_per_line=4` does not apply to V2 image passes; LINCNT is the output line count directly, and the announced bulk size follows exactly

**Observation:** re-deriving the image-bulk size from captured registers
with the formula `bulk_size = LINCNT_register × width_px × 3channels ×
2bytes`, where `width_px = round((ENDPIXEL − STRPIXEL) × dpi / 7200)`,
reproduces the actual announced `wIndex=0x08` preamble size **exactly**
(zero-byte difference) on all 7 single-pass files across 5 DPIs
(600/1200/1800/2400/3600/7200×2) — see `register-program-by-dpi.md`
"LINCNT formula verification". This requires `LINCNT_register` to be the
**output line count directly** — no ×4 multiplier, no /2 "buffer rows"
step. Independently, dividing captured LINCNT by `dpi/25.4` reproduces the
readme-recorded physical crop heights almost exactly (24.17-24.21mm for the
4 default-crop files vs. readme's 24.2mm; 25.32-25.33mm for the 3
full-sensor files vs. readme's 25.4mm for file 11) — two independent checks
agreeing.

**Interpretation:** `Model8200iSE.image_lincnt_per_line = 4`
(`model_8200i_se.py:352`, documented as coming from the SE model's original reference driver 9-PPI
ladder session 13, where "one output line is four LINCNT units and two
buffer rows" for a specific 3:2 35mm-frame ladder crop) and
`usb_image_lincnt_half_lines = True` (`model_8200i_se.py:280`, the
LINCNT/2 wire-row halving) are conventions specific to the SE model's
original reference driver that do
**not** carry over to the 8100 V2 under the reference driver. `compute_geometry()`
multiplies the physical line count by this factor-of-4 (via
`_geometry_from_mm`'s `lincnt_per_line = model.image_lincnt_per_line`),
which is exactly why F-010 saw computed LINCNT ~4x too high, compounded by
`compute_geometry(dpi, model=..., area=None)` also assuming the model's
*full* TA window height (`y_size_ta_mm=25.59mm`) rather than the reference driver's
actual (smaller) default-crop or full-sensor height — the two effects
multiply to the ~4.04-4.24x ratio F-010 observed (4 × 25.59/actual_height).
The residual small STRPIXEL/ENDPIXEL X-window gap noted in F-010 (captured
width ~0.27-0.28mm narrower than the model's `x_size_ta_mm` implies, at
every crop tested) is **not** resolved by this finding — it's a much
smaller, separate, still-open discrepancy, likely a minor calibration
constant rather than a formula error.

**Status:** confirmed (LINCNT formula), with high confidence (exact
zero-diff match, 7/7 files, cross-validated by a second independent
mm-height method).

**Affects:** **`claims-inventory.md` C-013 — contradicted for V2**: V2's
image pass does not use `image_lincnt_per_line=4`; the correct value for
`Model8100V2` (as a per-model override, not a shared SE default) is
effectively 1. **C-014 — contradicted for V2**: `usb_image_lincnt_half_lines
=True` does not apply either — there is no LINCNT/2 wire-row step; LINCNT
is the wire/output line count directly. This is a concrete, well-evidenced
candidate for a `Model8100V2` override PR (adding
`image_lincnt_per_line=1` and `usb_image_lincnt_half_lines=False`
overrides analogous to PR #30's pattern), which would also fix
`compute_geometry()`'s `total_bytes`/`lincnt_register` predictions used
throughout Phase 3-5 and by `tools/scanlab/capture_pcap.py`'s image-decode
path (once that's revisited per F-004).

---

### F-013 — The reference driver's real image pass writes a DEPTH_A/DEPTH_B pair (0x04/0xFF) that matches neither of pyopticfilm's two programmed states; the dummy register (0x2B) at 7200dpi is also ~35% lower than the model's table value

**Observation:** at every captured image pass (all 7 single-pass files
checked), the *last* write to `REG_DEPTH_A` (0x33) before the image
preamble is **0x04** (`Gl128Registers.DEPTH16_A`), while `REG_DEPTH_B`
(0xAF) is **0xFF** (`Gl128Registers.DEPTH8_B`) — a mixed pair, verified
directly against the raw register-write event log for file 7 (0x33 writes:
23→31→**4**; 0xAF writes: 70→127→**255**→255). `pyopticfilm`'s own
`Gl128ScanSession._configure()` (`session_gl128.py:185-190`) programs one of
two *matched* pairs: `DEPTH8_A/DEPTH8_B` (0x1F/0xFF) for an image pass, or
`DEPTH16_A/DEPTH16_B` (0x04/0x46) for shading — never the 0x04/0xFF mix the
reference driver actually uses. Generated via
`tools/trace_8100v2_python.py` (mock-hardware trace, no real device) and
compared directly against `register-program-by-dpi.md`'s captured dump.

Separately, the dummy register (0x2B) captured at 7200dpi is **15** (file 4)
/ **16** (file 11), vs. `Model8200iSE.dummy_by_dpi[7200] = 0x17` (23) that
`Model8100V2` inherits unchanged — a ~35% difference, and specifically at
the one DPI where the SE's dummy table jumps far above every other entry
(all other DPIs are 1-4; 7200 is 23) — see `model_8200i_se.py:215-227`.

**Interpretation:** neither difference is yet tied to a mechanism for issue
#35's jagged-line symptom — both are recorded as observations. But both are
plausible candidates worth ranking: DEPTH_A controls how the ASIC packs
depth/bit-width for the wire (a register-level framing detail, direct route
to per-line encoding artifacts), and dummy clocks control per-line
pixel-clock padding at the boundary the model's own comments already flag as
sensitive (`optical_end_inactive_native`, C-015). Both are also *safe*
candidates for a driver-side experiment (write the observed capture values
instead of the assumed ones) — unlike the LPERIOD or slope-table questions,
which need more care.

**Status:** new. The register-value observations are solid (directly
verified against raw event logs, not just the derived table); the causal
link to #35 is a ranked hypothesis, not a claim. **Migrated**: re-derived
as `SUSPECTED` entries in the register catalog at
[jboneng/pyopticfilm#54](https://github.com/jboneng/pyopticfilm/pull/54)
(`REG_DEPTH_A`/`REG_DEPTH_B`, `0x2B`) — the #35 causal-link question stays
open here.

**Affects:** `claims-inventory.md` C-012 (dummy — contradicted at 7200dpi
for V2) and C-014 (DEPTH_A — contradicted; the model's image-pass DEPTH8_A
assumption does not hold for V2 under the reference driver). Feeds directly
into `issue-35-diff.md`'s ranked candidate list.

---

### F-014 — The reference driver does not poll pyopticfilm's `0x21` feed-completion probe; it polls `wIndex=0x20` (constant 0x55) and `0x18` (2 or 18) instead

**Observation:** extended `tools/capture_ledger.py` to decode the
`bRequest=REQUEST_REGISTER (0x0C)` vendor-probe read pattern (distinct from
the ordinary ASIC register-read path, `bRequest=REQUEST_BUFFER (0x04)`) —
this is the wire format `gl128.py`'s `read_request_register()` /
`_FEED_PROBE_INDEX=0x21` uses. Across every capture with a real scan, the
reference driver issues this probe pattern (85-1060 times per capture) but
**never once at `wIndex=0x21`**. Instead it polls `wIndex=0x20`
(overwhelmingly the dominant one — 239/265 probes in a typical single-pass
capture) and, less often, `wIndex=0x18`. `wIndex=0x20` always returns
**0x55** in every capture checked — the same byte value as
`REGISTER_LINK_OK` (the link-status sentinel from the *other* register-read
path), though this is a single-byte probe response, not that path's
`(value, status)` pair, so the resemblance may be coincidental.
`wIndex=0x18` returns 2 in most captures, 18 in one. Both probes cluster
tightly around the two positioning `FEEDL` writes (file 4: indices
3018-3097, right at the 28292/13486 feed pair) — timing-wise, in the right
place to be *some* kind of feed-related probe, just not the one
pyopticfilm currently polls.

**Interpretation:** not resolved. `wIndex=0x20`'s constant 0x55 value
doesn't look like a completion-transition flag the way pyopticfilm's
`0x21→0x04` pattern is documented to work (a value that changes from
not-done to done) — every observed sample is already 0x55, which could
mean the capture never caught it mid-transition (feeds complete faster
than the ~1-2ms polling interval usually gives at least one busy sample),
or it could mean `0x20` isn't a completion flag at all and is something
else (a heartbeat/link check) that happens to get polled during feeds
incidentally. `0x18`'s meaning is unidentified. **Follow-up check**: these
two probes are not feed-specific at all — `wIndex=0x20`/`0x18` reads start
at the very beginning of the capture (device open, before any feed) and
continue at a similar rate throughout init/calibration/positioning, not
just clustered around `FEEDL` writes as first thought. This weakens the
"maybe it's a feed-completion signal we just never caught mid-transition"
reading — a probe queried constantly throughout the whole session looks
more like a generic engine-busy/heartbeat check than something specific to
feed completion.

**Status:** confirmed (the absence of `0x21` traffic) and **applied**. What
`0x20`/`0x18` actually mean remains unresolved and was deliberately *not*
guessed at. **Migrated**: re-derived as a `CONFIRMED` entry (plus the
hardware-incident safety lessons this branch learned the hard way) in the
register catalog at
[jboneng/pyopticfilm#54](https://github.com/jboneng/pyopticfilm/pull/54)
(`REG_0x21`) — `0x20`/`0x18`'s actual meaning is still open and stays here.

**Affects:** `issue-33-diff.md`'s open question about whether the
reference driver's feed-completion wait matches pyopticfilm's `0x21`
probe — answered "no, at least not via that wIndex, in any context."
**Applied**: `gl128.py`/`model_8200i_se.py`/`model_8100_v2.py` — added a
per-model `feed_probe_index` attribute (SE keeps `0x21`, unchanged and
unverified either way for SE; V2 set to `None`, which skips the probe read
entirely rather than querying an index we now know isn't meaningful for
V2). Feed completion for V2 relies solely on the already-confirmed `0x101`
status register path, which the code already ORs in as a fallback — so
this is a low-risk change: it removes an unverified, evidently-unused
check for V2, without introducing a new unverified one (`0x20`/`0x18`) in
its place. Verified with a direct unit test
(`tests/test_gl128_feed_probe.py`) that no USB call is made for V2, not
just that the constant differs.

---

### F-015 — Bulk-IN read cadence during the image pass is smooth, with no stalls, at ~11.2ms median interval (7200dpi)

**Observation:** extended `capture_ledger.py` with an opt-in
`track_bulk_timing` mode (per-bulk-IN-packet `(timestamp, size)`, no
payload bytes) and ran it on file 4 (7200dpi). Restricting to the
>50KB chunks *after* the image-pass preamble (excluding the dark/white
shading-strip bulk transfers earlier in the same capture, which are also
large): 6796 chunks, inter-chunk interval mean/median **11.2ms**, min
4.16ms, max 18.27ms, stdev 3.76ms — a tight, well-behaved distribution
with **zero gaps over 50ms** anywhere in the image pass. (The unfiltered
capture *does* have three gaps up to 3.02s, but all three occur in the
first ~5 seconds — during shading/calibration, not the image stream.)

**Interpretation:** the host-side USB read cadence during the actual image
transfer does not show stalling or irregular pacing that would obviously
explain issue #35's jaggedness — reinforces that a register-level cause
(LPERIOD, dummy — F-009, F-013) is more likely than a read-pacing/USB-side
cause. Not proof either way, just one plausible category of explanation
ruled less likely.

**Status:** new, single-capture (file 4 only — not yet cross-checked
against other DPIs or the full-sensor 7200dpi capture, file 11).

**Affects:** `issue-35-diff.md`'s "what this diff cannot show" section —
partially resolved (cadence is now characterized for one 7200dpi capture);
strengthens candidates #1 (LPERIOD) and #2 (dummy) over a hypothetical
pacing-based explanation, without ruling either register cause in or out
on its own.

---

*Phase 1, 2026-08-30. Add new findings below as Phase 2-4 proceed; do not
renumber existing entries.*
