# 8100 V2 capture analysis — progress log

Append one dated entry per phase/session. Re-read this file plus `PLAN.md`
at the start of every session and after any context compaction.

---

## 2026-08-30 — Phase 0: orientation

**Setup / decisions made with the user:**
- Working repo chosen: `/c/dev/pyopticfilm-src` (clean clone of upstream
  `jboneng/pyopticfilm`), not the two local `TobbyTravel/pyopticfilm` forks
  or the `pyopticfilm_captures` repo (also present locally). New branch
  `hw-ref/8100v2-capture-analysis` created off `main` (`75fcf8c`).
- Capture set: `C:\dev\morecapture\{1..4,6..11}.pcapng` — **file 5 is
  missing** (readme.txt describes it as 1200dpi, RGB exposure 2.0 locked).
  User chose to proceed without it; the exposure-2.0 comparison leg of
  Phase 3 is deferred.
- **User correction mid-session:** the readme.txt numbering for files 6-11
  may not be reliable. User confirmed file 11 is actually 7200dpi, which
  contradicts its own readme label ("3600dpi... 10368x7200 36.6x25.4mm" —
  that math is ~7200dpi, so the label was already internally inconsistent).
  Phase 1 must derive DPISET from registers for files 6-11 before building
  any per-DPI table; do not trust the label.

**What was read:** `docs/scanner-validation.md`, `docs/sane-opticfilm.md`,
issue #33, issue #35, PR #30 (all via `gh`), `src/pyopticfilm/device/
model_8100_v2.py` (full), `src/pyopticfilm/device/model_8200i_se.py` (full),
`src/pyopticfilm/asic/gl128.py` (partial, ~1200/1572 lines — boot, AFE,
shading, lamp; motor/home/park section beyond line 1222 not yet read),
`src/pyopticfilm/scan/session_gl128.py` (full), `src/pyopticfilm/usb/
device.py` (partial — PIDs, BULK_MAX_SIZE), grepped `tools/scanlab/
capture_pcap.py` framing assumptions and `src/pyopticfilm/usb/protocol.py`
vendor-request constants.

**Key finding (provenance gap, see claims-inventory.md header):** the
existing V2 constants (PR #30) and the SE tables they inherit are **not**
derived from our capture set. PR #30 cites `04_color_7200.pcapng` /
"capture session Aug 2026" — a file not found in `C:\dev\morecapture` nor in
the `pyopticfilm_captures` repo's git history (checked, no `8100` path).
The SE tables are explicitly the SE model's original reference driver-derived (`captures/8200i-se/`
sessions 03-14). Our new captures are confirmed the reference driver (readme.txt,
autocrop on). This means Phase 3 is a genuine independent re-derivation from
different capture software, not a repeat of prior work — treat every
existing V2/SE register value as an unverified hypothesis, per the mission
brief's instruction not to assume docs/code are correct.

**Also confirmed:** `tools/scanlab/capture_pcap.py` assumes the SE model's original reference driver image
framing (`VALUE_BUFFER`/wIndex=0x08, `bulk_size = LINCNT×width×3`, one
65508-byte URB/line at 7200dpi) — must be checked against the reference driver captures
before reuse, per the mission brief's warning (now C-025 in
claims-inventory.md). Separately, pyopticfilm's own driver does NOT replay
that one-preamble-per-image framing — it chunks announces to
`BULK_MAX_SIZE=0xF000` because a single full-image preamble was "louder on
real GL128 hardware" (session_gl128.py comment) — an unverified claim in its
own right (C-026), and a reminder that pyopticfilm's own bulk framing choice
is also not capture-derived from the reference driver specifically.

**Files produced:** `docs/hw-ref/8100v2/PLAN.md`,
`docs/hw-ref/8100v2/PROGRESS.md` (this file),
`docs/hw-ref/8100v2/claims-inventory.md` (38 claims C-001..C-038, 3 gaps
G-001..G-003).

**Open questions carried into Phase 1:**
1. What is the true DPI (from DPISET register, not label) for each of files
   6-11?
2. Is `04_color_7200.pcapng` recoverable anywhere, or is PR #30 permanently
   unverifiable except by independent re-derivation?
3. Does the reference driver use the SE model's original reference driver-style single full-image preamble, or
   something closer to pyopticfilm's own chunked announces, or a third
   pattern entirely?

**Not yet started:** Phase 1 capture inventory itself (no pcap parsing run
yet this session).

---

## 2026-08-30 — Phase 1: capture inventory and tooling check

**Tooling decision:** wrote a new lightweight tool,
`tools/capture_ledger.py`, rather than extending `capture_pcap.py` in place.
Reason: `capture_pcap.py` drops pcap timestamps entirely and never decodes
register *reads* (only writes) — both needed for Phase 2-4 timing analysis
(#33/#35) and not a small patch given its existing structure. The new tool
streams pcapng blocks from disk (nothing proportional to file size is
retained — bulk payload bytes are counted, never stored), preserves
per-packet timestamps (via Interface Description Block `if_tsresol`,
default microsecond), and pairs control-transfer submit/complete packets by
the USBPcap IRP pointer rather than assuming sequential SETUP→DATA stages —
these captures collapse every control transfer to exactly two packets
(stage 0 = submit, carrying the full setup + any OUT payload already
concatenated; stage 3 = complete, carrying the IN response or nothing), and
multiple transfers interleave in the packet stream, so naive adjacency
pairing silently produced zero register reads until fixed. No unit tests
written yet against a synthetic capture (mission brief's Phase 1 tooling
requirement) — flagged as follow-up, not blocking further phases since the
tool's counts cross-validate against the existing decoder (see below).

**Ran both parsers on all 10 files** (`tools/capture_ledger.py` +
`tools/hw_ref_inventory.py` for aggregation, and the existing
`capture_pcap.analyze_usbpcap()` for a cross-check, parse-only, no image
decode). All 10 succeed on both, including the 1.77GB file 3 (2.54s,
existing decoder; 0.71s, new streaming tool). Counts match exactly between
the two independent implementations on every file (e.g. file 3: 2848
register writes / 100 preambles / 53765 bulk-INs, both tools agree) — strong
cross-validation of the base parsing layer.

**Key findings (full detail in `findings.md`):**
- **F-001/F-002**: DPISET register resolves file 11 to 7200dpi (readme
  label "3600dpi" was wrong, confirming the user's correction); files
  6/8/9/10 match their labels exactly; file 7 is consistent with 600dpi but
  not fully distinguishable from 150/300 by DPISET alone.
- **F-003**: the reference driver uses `wIndex=0x08` for the image buffer preamble, same
  as the SE model's original reference driver convention `capture_pcap.py` assumes — confirmed.
- **F-004**: the reference driver's 7200dpi image bulk is NOT a fixed 65508-byte URB per
  line as `capture_pcap.py` hardcodes — it's ~60-62KB multi-line chunks that
  scale with DPI (600dpi ~4.6KB, 1200 ~9.7KB, 1800 ~14.8KB, 2400 ~20KB, 3600
  ~30KB, 7200 ~60-62KB), with a consistent ±512-byte glued-status-word
  variant at every DPI (matching an existing decoder heuristic, just not the
  fixed-constant part). Needs Phase 3's register cross-check to pin down the
  exact lines-per-chunk ratio.
- **F-005**: capture 3 (3-pass) has **4** `wIndex=0x08` preambles, not 3 —
  open question for Phase 2 (autocrop extra-pass question from `readme.txt`,
  and directly relevant to issue #33's repositioning-count evidence).
- **F-006**: the existing decoder's base parsing (not its image-decode path)
  is sound on the reference driver captures at every file size in this dataset.

**Files produced:** `tools/capture_ledger.py`, `tools/hw_ref_inventory.py`,
`docs/hw-ref/8100v2/ledgers/{1,2,3,4,6,7,8,9,10,11}.{summary,events}.json`,
`docs/hw-ref/8100v2/capture-inventory.md` + `.json`, `docs/hw-ref/8100v2/
findings.md` (6 findings, F-001..F-006). Updated `claims-inventory.md`
(C-025 split confirmed/contradicted, G-003 resolved).

**Not done from the Phase 1 plan:** synthetic-capture unit tests for the new
ledger tool (item 3's "test against a tiny synthetic capture" — deferred,
tool is cross-validated against the existing decoder instead for now).

**Open questions carried into Phase 2:**
1. What are the 4 `wIndex=0x08` preambles in capture 3 (F-005) — 1 extra
   pass from autocrop, or something else? Does every single-pass capture
   also have a hidden extra pass not visible as a *second* `0x08` preamble
   (e.g. folded into the one large preamble)?
2. Exact lines-per-chunk ratio at each DPI for the reference driver's bulk framing
   (F-004) — needed before any image-bulk byte-count math in Phase 3.
3. File 7's true DPI (600 vs 300 vs 150) — needs output pixel dimensions,
   not just DPISET.
4. What do captures 1 and 2 show about the reference driver's open/power-on sequence
   beyond plain enumeration? (Not yet analyzed — only aggregate counts
   pulled so far: file 1 has 447 register writes and 12 status polls across
   30s of power-cycling; file 2, the no-software baseline, has zero vendor
   register traffic at all — pure USB enumeration.)

---

## 2026-08-30 — Phase 2: phase segmentation

**Tool:** wrote `tools/phase_segment.py`, which reads the Phase 1 ledgers and
produces a milestone timeline per capture (buffer preambles classified by
wIndex + register state, positioning `FEEDL` writes, lamp transitions) in
`docs/hw-ref/8100v2/phases/<n>.md` + `.timeline.json`. Ran on all 10 files.

**F-005 resolved, with a significant new finding attached (F-007, F-008):**
capture 3 is 4 full acquisition cycles (dark shading → white shading →
FEEDL=28292 → FEEDL2 → image), not the 3 its readme label describes. Cycle
1 uses FEEDL2=13128; cycles 2-4 use 13486. Checking every other capture's
single acquisition cycle against this: default-crop scans (captures 3
cycles 2-4, 4, 6, 7, 8) cluster at FEEDL2≈13484-13488; explicit
full-sensor/no-crop scans (9, 10, 11) cluster at FEEDL2≈13198-13200. Capture
3's cycle 1 (13128) matches neither cluster — it exactly matches
`Model8200iSE.feed_to_scan_top_steps` (documented "top of TA window") and PR
#30's V2 override value. Timestamps confirm capture 3 is the session's
first-ever real scan (captures 1/2, which run earlier, never reach shading
or an image pass at all — capture 1 is power-cycling with only lamp
strobes + AHB uploads, no RAM/calib or image traffic; capture 2 is pure USB
enumeration with zero vendor register traffic). This **contradicts PR #30's
`feed_to_scan_steps=13128`** for ordinary full-frame scans — 7 independent
the reference driver full-frame scans across 5 DPIs all use ~13486, not 13128. Whether
13128 is specifically a first-scan-of-session behavior or specific to the
multi-pass request is not resolved by this dataset (confounded: capture 3
is both). Full detail and the two open hypotheses: `findings.md` F-005 (via
timeline evidence), F-007, F-008. `claims-inventory.md` C-001/C-004/C-005/
C-007/C-008 updated to reflect this.

**Autocrop extra-pass question (from `readme.txt` line 1) — answered for
single-pass captures:** no, autocrop does not add a second full acquisition
cycle. File 4 (single pass, autocrop on) shows exactly one dark/white
shading + one image pass, going straight to FEEDL2=13486 with no 13128
preview step. The extra cycle is specific to capture 3 and is better
explained by F-007/F-008 (first-scan-of-session or multi-pass-specific),
not by autocrop in general.

**Captures 1 & 2 (open/power-on) analyzed:** capture 1 (the reference driver open,
repeated power on/off) shows, per power-cycle, only a lamp strobe sequence
(0x03 toggled off/on/off/on/off) followed by 2 `wIndex=0x1` AHB-upload
preambles (slope/exposure table writes) — never a RAM/calib or image
preamble. the reference driver appears to attempt its normal init handshake on each
power-up but never reaches shading/scan, consistent with the device
disappearing again before that point (power-cycling test). Capture 2
(no software open, power on/off) shows zero vendor register traffic at
all — pure USB enumeration (GET_DESCRIPTOR etc.), confirming the "no
software" baseline is exactly that.

**Not yet done from the Phase 2 plan:** a full phase-boundary table
(enumeration/init/lamp/home/shading/prescan/image/park/close) with explicit
named phase rows — the milestone timelines in `phases/<n>.md` contain the
same information but are not yet abstracted into that named-phase format.
Considered sufficient for Phase 3/4's needs so far; can be added later if a
named-phase table turns out to matter for the reference doc (Phase 5).

**Files produced:** `tools/phase_segment.py`,
`docs/hw-ref/8100v2/phases/{1,2,3,4,6,7,8,9,10,11}.md` + `.timeline.json`.
Updated `findings.md` (F-007, F-008), `claims-inventory.md` (C-001, C-004,
C-005, C-007, C-008).

**Open questions carried into Phase 3 (top 5, per the mission's DoD):**
1. Is capture 3's FEEDL2=13128 first-pass value a session-first phenomenon
   or a multi-pass-specific phenomenon? Needs a capture that decouples the
   two (single-pass-first-of-session, or multi-pass-not-first-of-session).
2. Exact lines-per-URB-chunk ratio per DPI for the reference driver's bulk framing
   (F-004) — needed before trusting any image-bulk byte-count math.
3. File 7's true DPI (600 vs 300 vs 150 — DPISET=100 is ambiguous) — needs
   output pixel dimensions.
4. Capture 3 pass timing: cycles 2-4 each take ~76.7s image-acquisition to
   next-cycle-lamp-off, but cycle 1 takes ~81.3s — is this a real
   session-first-pass timing difference (relevant to #33) or an artifact of
   cycle 1's different feed2/area?
5. Why do full-sensor-no-crop scans (9/10/11, feed2≈13200) and default-crop
   scans (4/6/7/8, feed2≈13486) differ by ~286 steps (~0.5mm) — does this
   match `feed_to_scan_steps_for_area()`'s current formula when given the
   right `y1`, or does that formula also need correction?

---

## 2026-08-30 — Phase 3: register program per DPI + claim verification

**Tool:** `tools/register_program.py` — register snapshot at each capture's
image-pass-start (largest `wIndex=0x08` preamble), compared against
`compute_geometry(dpi, model=MODEL_8100_V2)`. Ran on the 7 single-pass files
with a resolved DPI (4, 6, 7, 8, 9, 10, 11). Output:
`docs/hw-ref/8100v2/register-program-by-dpi.md` (+ `.json`, full register
dump per file).

**DPISET fully confirmed:** captured DPISET matches `compute_geometry()`'s
prediction exactly at every one of the 7 files (`C-010` confirmed).

**LPERIOD contradicts PR #30 (F-009):** captured 7200dpi LPERIOD is 15914
(file 4) and 15999 (file 11) — two different values from two different
the reference driver captures at the same DPI, and neither matches PR #30's
`lperiod_by_dpi[7200]=16035`. `claims-inventory.md` C-002 marked
contradicted. Not yet interpreted (crop-dependence vs. something else) —
recorded as open.

**STRPIXEL/ENDPIXEL/LINCNT/announced-bulk-size do not match
`compute_geometry()`'s full-frame prediction at any DPI (F-010), by a
pattern not yet understood:** X window (STR/END) is narrower than computed
at every DPI, including the "maximal sensor, no crop" files (9/10/11);
LINCNT is a strikingly consistent ~4.2x smaller than computed across every
DPI (600 through 7200) — too flat across DPIs to obviously be a real
per-scan content difference, more likely a semantics mismatch in how this
session's extraction script or `ScanGeometry.total_bytes`/
`image_lincnt_per_line` should be interpreted. **Explicitly not resolved
this session** — flagged as the top blocker before trusting any of this
session's captured LINCNT/STR/END numbers as replacement constants.

**Exposure diff reduced to a non-result (F-011):** `REG_EXPOSURE=14000` in
every captured image pass, including the "exposure locked RGB 1.0" file —
expected, since 1.0 is documented as the reference driver's nominal multiplier, not a
contradiction. But this means the dataset (without capture 5) cannot show
the exposure register actually varying at all; the originally-planned
Phase 3 exposure diff has no delta to observe with the files on hand.

**Files produced:** `tools/register_program.py`,
`docs/hw-ref/8100v2/register-program-by-dpi.md` + `.json`. Updated
`findings.md` (F-009, F-010, F-011), `claims-inventory.md` (C-002, C-010,
C-013, C-019).

**Not done from the Phase 3 plan:** resolving F-010 (the LINCNT/STR/END
mismatch) before trusting any corrected constant; the capture-11 "7200 vs
3600" question was already resolved in Phase 1 (F-001), not repeated here.
Slope-table content and MAXWD were not extracted — slope tables are AHB
uploads (bulk payload, out of scope per the "never decode bulk" rule beyond
counting them; `capture-inventory.md`'s wIndex histograms already give
per-file AHB-upload counts), and no `MAXWD`-equivalent register was found
in `Gl128Registers` (GL128 does not appear to use a GL845-style MAXWD line
buffer register — worth confirming explicitly in a future session rather
than assumed).

**Open questions carried into Phase 4 (top 5):**
1. **F-010 is the most urgent**: what is the correct way to compute
   expected image-bulk byte count / LINCNT from `ScanGeometry` for the
   GL128 image path, and does resolving it change the ~4.2x observation?
2. Is LPERIOD crop-dependent (F-009), explaining the file 4 vs 11 spread?
   Needs a same-crop, repeated-capture comparison this dataset doesn't have
   (or careful reuse of capture 3's 3 same-crop 7200dpi passes, not yet
   checked against each other for LPERIOD).
3. Same F-007/F-008 open question from Phase 2 (first-scan-of-session vs.
   multi-pass-specific 13128 feed2).
4. File 7's true DPI (600 vs 300 vs 150) still unresolved — needs output
   pixel dimensions, which Phase 3's register data alone doesn't give
   (readme.txt did not record file 7's output size).
5. Whether capture 3's 3 same-crop, same-DPI passes (cycles 2-4) show any
   LPERIOD/LINCNT/STR/END variation **from each other** — not yet checked,
   and would be a cleaner same-session, same-crop comparison than the
   cross-capture file 4 vs 11 comparison F-009 currently relies on.

---

## 2026-08-30 — F-010 resolved (same session, before starting Phase 4)

At the user's request, resolved F-010 before moving to Phase 4 rather than
carrying it forward as an open blocker. Root cause found: `compute_geometry()`
multiplies physical line count by `Model8200iSE.image_lincnt_per_line = 4`
(an SE-model, PPI-ladder-specific convention — "one output line is four
LINCNT units", from a specific 3:2 35mm-frame ladder crop session), which
does not apply to V2 image passes under the reference driver.

**Verification:** `bulk_size = LINCNT_register × width_px × 3channels ×
2bytes(16-bit)`, `width_px = round((ENDPIXEL−STRPIXEL) × dpi / 7200)`,
reproduces the actual announced image-preamble byte count **exactly** (zero
difference) on all 7 single-pass files across 5 DPIs. This requires
`LINCNT_register` to be the output line count directly — no ×4, no /2
"buffer rows" halving. Cross-checked a second way (captured LINCNT ÷
`dpi/25.4` reproduces the readme-recorded crop heights to within rounding)
and it agrees. Full verification table added to
`register-program-by-dpi.md`; write-up in `findings.md` F-012.

**Practical effect:** `claims-inventory.md` C-013 and C-014 (the SE's
`image_lincnt_per_line=4` and `usb_image_lincnt_half_lines=True`) are now
**contradicted for V2** — a concrete, evidence-backed candidate for a
`Model8100V2` override PR (`image_lincnt_per_line=1`,
`usb_image_lincnt_half_lines=False`), which would also correct
`compute_geometry()`'s LINCNT/total_bytes predictions for V2 throughout the
rest of this analysis. C-024 (`strpixel_native_units=True`) is now
**confirmed** — the formula's X-window term depends on it and matched
exactly. C-004/C-008 (PR #30's own LINCNT/bulk-size citations) are now in
tension with this finding: PR #30's raw cited LINCNT=29012 only makes sense
under the *old* ×4 convention, not the ×1 convention this session
established — raising the open question of whether PR #30's source capture
(`04_color_7200.pcapng`, not in our dataset) used different LINCNT
semantics than the reference driver, or whether V2 genuinely behaves differently at a
much larger, untested travel distance (full TA window, not a crop). Not
resolved — noted as a gap for a future capture.

A smaller, separate discrepancy remains open and unresolved: captured X
window (STRPIXEL/ENDPIXEL span) is consistently ~0.27-0.28mm narrower than
`x_size_ta_mm=36.58` implies, at every crop tested. This is real but much
smaller than the LINCNT issue and was not chased further this session
(`claims-inventory.md` C-021, noted as "partially contradicted, minor").

**Files touched:** `register-program-by-dpi.md` (added F-010-resolution
note + verification table), `findings.md` (F-012), `claims-inventory.md`
(C-004, C-008, C-013, C-014, C-021, C-024).

---

## 2026-08-30 — Mid-session correction: no software names in docs/PRs

User instruction, unprompted: never reference the two capture-software
names by name in any PR description, issue comment, or documentation for
this project — use generic terms instead ("the reference driver" for our
new capture set's software; "the SE model's original reference driver" when
distinguishing the 8200i SE's differently-sourced original captures).
Applied retroactively: bulk-substituted all existing `docs/hw-ref/8100v2/*`
files and the `tools/*.py` scripts written this session (81 occurrences
across 10 files), then manually cleaned up a handful of awkward compound
phrases the mechanical substitution produced (e.g. "driver-on-8200i-SE",
double "the the"). Saved as a standing feedback memory
(`feedback_no_software_names_in_docs`) so future sessions apply this from
the start rather than needing the correction again. Does not apply to
pre-existing repository files that already name the software in their own
comments (e.g. `model_8200i_se.py`) — only to content authored for this
project going forward.

---

## 2026-08-30 — Phase 4: issue-targeted diffs (#33, #35)

**Tool:** `tools/trace_8100v2_python.py` — drives a real `Gl128ScanSession`
against `MODEL_8100_V2` through the mock-hardware fake-USB transport (no
real device touched), at 7200dpi with a small crop (register *program*
doesn't depend on crop size; keeps the mock acquire loop's byte count
small). Produced `docs/hw-ref/8100v2/pyopticfilm-7200-trace.json` (359
transactions) for direct comparison against the captured register program.

**New finding (F-013):** the reference driver's real image pass writes
`REG_DEPTH_A` (0x33) = `0x04` (a DEPTH16 constant) paired with `REG_DEPTH_B`
(0xAF) = `0xFF` (a DEPTH8 constant) — verified directly against the raw
register-write event log (file 7: 0x33 writes 23→31→**4**; 0xAF writes
70→127→**255**). pyopticfilm's own code only ever programs *matched* pairs
(DEPTH8/DEPTH8 for image, DEPTH16/DEPTH16 for shading) — this mixed pair is
new and previously undocumented. Present at every DPI (600-7200), not
7200-specific. Also folded in: the dummy register (0x2B) at 7200dpi is
15-16 in captures vs. the model's inherited 23 — matches exactly at every
other DPI, contradicted only at 7200 (the one DPI where the SE's dummy
table is an outlier). `claims-inventory.md` C-012 and C-014 updated.

**Issue #35 diff written** (`issue-35-diff.md` + draft comment in
`drafts/issue-35-comment.md`): ruled out motor mode, lamp, pixel clock,
DPISET, EXPOSURE (all match pyopticfilm's own trace exactly) and
priming/quiet-drain (already ruled out by the issue reporter on real
hardware). Ranked remaining candidates: **LPERIOD** (F-009, highest —
7200-specific, direct per-line timing mechanism) and **dummy 0x2B**
(F-013, high — also 7200-specific) as the two most promising untested,
low-risk register experiments; **DEPTH_A** (F-013, moderate — present at
every DPI, so fails the "why only 7200" test on its own, but a genuine
protocol correction worth making regardless); **slope table content**
(the issue's own next planned experiment, `image_slope_slow` — confirmed
un-inspectable from captures by this project's own constraints, genuinely
needs the hardware test already planned).

**Issue #33 diff written** (`issue-33-diff.md` + draft comment in
`drafts/issue-33-comment.md`): documented the reference driver's two-feed
positioning sequence (28292 reference feed, ~1.0-1.05s later a second feed,
~1.4-1.5s after that the image pass starts) and its ~15-16ms status-poll
cadence during feeds (register 0x101, watching a bit combination including
FEEDFSH and HOMESNR — decoded and shown explicitly, not just described).
Compared structurally against pyopticfilm's own feed/slope/prime code:
positioning-feed slope table selection appears to already match (both
default to the fast ramp) — consistent with the issue's own finding that
feed-ramp speed wasn't the answer. Flagged F-007/F-008's first-scan-of-
session behavior as a genuinely different mechanism from pyopticfilm's own
priming (different target position vs. discarded settle pass) — offered as
a data point, not a fix, with the honest caveat that this dataset can't
distinguish "first scan of session" from "first pass of a multi-pass
request" (still open from Phase 2/3). Explicitly could NOT address the
rare >150px spike (dataset has only one 7200dpi multi-pass capture) or
pyopticfilm's own `0x21` vendor-probe polling vs. the reference driver's
(not yet decoded from captures — open item).

**Files produced:** `tools/trace_8100v2_python.py`,
`docs/hw-ref/8100v2/pyopticfilm-7200-trace.json`, `issue-35-diff.md`,
`issue-33-diff.md`, `drafts/issue-35-comment.md`, `drafts/issue-33-comment.md`.
Updated `findings.md` (F-013), `claims-inventory.md` (C-012, C-014).

**Not done from the Phase 4 plan:** decoding pyopticfilm's `0x21` vendor
probe against the captures (would need extending `capture_ledger.py` to
classify that specific control-read pattern — not done this session);
per-packet bulk-IN timing/cadence comparison (ledger currently only
histograms sizes, not a timestamp series per bulk packet).

**Top 5 open questions for a future capture/hardware session (mission's
requested list, consolidated across all phases):**
1. Is F-007/F-008's 13128 feed2 value session-first-specific or
   multi-pass-specific? Needs a single-pass-first-of-session capture, or a
   multi-pass-not-first-of-session capture, to decouple them.
2. Does correcting LPERIOD (F-009, ~15914-15999 not 16035) or dummy 0x2B
   (F-013, ~15-16 not 23) at 7200dpi change issue #35's jaggedness on real
   hardware? (The two ranked, low-risk experiments from this session.)
3. Is LPERIOD crop-dependent (explaining file 4 vs. 11's 15914 vs. 15999),
   or session/thermal-dependent? Needs a same-crop, repeated 7200dpi
   capture set.
4. What is capture 5 (1200dpi, exposure 2.0 locked) actually — still
   missing from the dataset, blocking any real exposure-register diff.
5. Does the reference driver's status-poll pattern during feeds also use
   the `0x21` vendor-probe pyopticfilm's own code polls, or only register
   0x101 (as directly observed this session)? Needs `capture_ledger.py`
   extended to decode that specific control-read pattern.

---

## 2026-08-30 — Environment bug found and fixed: stale editable install

While starting Phase 5, discovered this machine's venv resolves `import
pyopticfilm` to `C:\Users\Tobby\claude\pyopticfilm` — a different local
clone (fork `TobbyTravel/pyopticfilm`, ahead by 2 commits not in this
branch) — not `C:\dev\pyopticfilm-src` (this branch). This means every
`tools/register_program.py` and `tools/trace_8100v2_python.py` run earlier
in Phase 3/4 executed against that other clone's code, not the branch
being analyzed.

**Impact assessment:** diffed the two clones' relevant source files.
`scan/geometry.py`, `scan/session_gl128.py`, `device/model_8200i_se.py`,
`scan/session.py` differ (the other clone lacks this branch's
`strpixel_native_units`/`usb_end_drop` support and has newer multi-pass/
manual-exposure features this branch doesn't). `device/model_8100_v2.py`,
`usb/protocol.py`, `usb/device.py`, `asic/registers.py`, `asic/gl128.py`,
`device/select.py` are identical (only line-ending differences).

Re-ran both affected tools after fixing (`sys.path.insert` forcing this
repo's `src/` first, added to `capture_ledger.py`, `register_program.py`,
`trace_8100v2_python.py` — same pattern `tools/dump_python_setup_trace.py`
already used). Practical effect was small: `register_program.py`'s
`compute_geometry()` "computed" STR/ENDPIXEL column shifted by 1-2 native
units (240→242, 10608/10609→10610) — well within the noise already
described in F-010/`claims-inventory.md` C-021, no conclusion changes.
`trace_8100v2_python.py`'s output was **byte-identical** on the registers
that matter for F-013 (DEPTH_A=0x1F, DEPTH_B=0xFF, motor 0x30, lamp 0x30,
dummy 0x17, LPERIOD 16035) — `session_gl128.py`'s DEPTH-register logic
happened to be unchanged between the two clones. **F-009, F-012, and F-013
are unaffected** — F-012 in particular was derived directly from captured
registers with no `compute_geometry()` dependency at all. Regenerated
`register-program-by-dpi.md`/`.json` and `pyopticfilm-7200-trace.json` with
the corrected import for reproducibility going forward.

Not fixed: the venv's `.pth` file itself (`_editable_impl_pyopticfilm.pth`)
still points at the other clone — out of scope to change shared
environment config without being asked; the `sys.path.insert` guard in the
three affected tools is sufficient for this project's own reproducibility.
(Note: the pre-existing repo test suite (`pytest` at repo root) also hits
this same stale-import issue on 2 unrelated test files — not caused by
this session's work, not fixed, flagged here for visibility only.)

---

## 2026-08-30 — Phase 5: reference doc, fixtures, PR drafts

**`docs/hardware-reference-8100v2.md`** written — the top-level synthesis:
USB framing (including the submit/complete IRP-pairing detail found in
Phase 1), register map with per-register status, per-phase sequence,
capture 3's 4-cycle anomaly, positioning, per-DPI register table, exposure
section (with the capture-5-missing caveat), an explicit 8-item unknowns
list, and a change-log section against prior documentation. Every number
cites a capture file and packet index/range.

**`docs/scanner-validation.md`** updated with a short "8100 V2 corrections
from an independent capture set" section summarizing the 5 contradicted/
new findings (F-007/F-008 feed2, F-009 LPERIOD, F-012 LINCNT, F-013 dummy/
DEPTH_A) and linking to the full detail — explicitly not a changelog of
applied fixes, since none have been applied to `model_8100_v2.py` yet.

**Fixtures**: `tools/export_capture_fixtures.py` → 3 register-only golden
traces in `tests/traces/capture/8100_v2/` (1200dpi default-crop, 7200dpi
default-crop, 7200dpi full-sensor), following the existing
`tests/traces/sane/*.registers.json` convention (meta + final register map,
no transactions, no image bytes). ~13KB total — no large-file concern, no
separate ask needed.

**Tests**: added `tests/test_capture_ledger.py` (deferred from Phase 1) —
a hand-built synthetic pcapng file covering pcapng timestamp decoding and
the IRP-pairing behavior specifically (an interleaved read/write pair that
breaks naive adjacency pairing). Passes. Also ran `ruff check` on all new
tooling and fixed 4 minor lint issues (unparenthesized implicit string
concatenation ×3, one dead/unused variable left over from an earlier draft
of `phase_segment.py`'s FEEDL-scan loop) — all clean now.

**PR drafts** written to `drafts/`: `pr-a-tooling.md` (the 6 new
`tools/*.py` scripts + the synthetic test), `pr-b-docs-fixtures.md` (the
reference doc, all of `docs/hw-ref/8100v2/`, the `scanner-validation.md`
edit, the 3 fixtures), `pr-c-constants.md` (**one** proposed
`Model8100V2` change — `image_lincnt_per_line=1` /
`usb_image_lincnt_half_lines=False`, the only correction with both an
exact-match verification and no open interpretive question left; the other
strong findings — LPERIOD, dummy, DEPTH_A, feed2 — are each left as
documentation-only pending their own open questions, not proposed as code
changes yet). **None of the three PRs have been pushed or opened.** The
proposed `model_8100_v2.py` diff in PR (c) has **not been applied to the
working tree** — it's a reviewable proposal, not a completed change,
consistent with this project's "ask before anything touching real
hardware behavior" rule (this model is `scan_ready=True`).

**Files produced:** `docs/hardware-reference-8100v2.md`,
`tools/export_capture_fixtures.py`, `tests/traces/capture/8100_v2/*.registers.json`
(3 files), `tests/test_capture_ledger.py`, `drafts/pr-a-tooling.md`,
`drafts/pr-b-docs-fixtures.md`, `drafts/pr-c-constants.md`. Updated
`docs/scanner-validation.md`.

**Session status: all 5 phases of the mission's plan are complete.**
Nothing has been pushed, no PR opened, no issue comment posted, no
git commit made — everything is uncommitted local working-tree changes on
branch `hw-ref/8100v2-capture-analysis`, awaiting review.

---

## 2026-08-30 — User directive: prioritize captures over inherited reference tables; apply confirmed fixes; workflow moves to the user's fork

Two changes to working method, from the user directly:

1. **Evidence priority**: our second capture session is from a known-good
   real-world driver, not a lower-confidence source — where it disagrees
   with the SE's inherited tables (themselves from a different, original
   capture session), prefer the second session's values. Corollary: be
   willing to actually apply well-evidenced corrections as code changes,
   not just document them as proposals — reserve "propose only, verify on
   hardware first" for genuinely unresolved cases (disagreement *within*
   the second session itself, e.g. LPERIOD's two different 7200dpi values).
2. **Fork-first workflow**: from now on, commits and PRs go to the user's
   fork (`https://github.com/TobbyTravel/pyopticfilm`) first; upstream PRs
   to `jboneng/pyopticfilm` happen later, manually, by the user, after
   hardware verification. Added as git remote `fork`.

**Applied to `src/pyopticfilm/device/model_8100_v2.py`** (previously only
proposed, unapplied, in `drafts/pr-c-constants.md`):
- `feed_to_scan_steps`: 13128 → **13486** (F-007/F-008 — 7 second-session
  captures across 5 DPIs cluster there; 13128 was a one-off first-scan
  value, mechanism still unresolved but the ordinary-scan value is not)
- `dummy_by_dpi[7200]`: 23 → **15** (F-013 — confirmed at every other DPI,
  only 7200 needed correcting)
- `image_depth_a`: new override, **0x04** (F-013 — required a small
  `session_gl128.py` change too: `_configure()` now reads
  `getattr(model, "image_depth_a", r.DEPTH8_A)` instead of a hardcoded
  constant; default unchanged for every other model)
- `image_lincnt_per_line=1`, `usb_image_lincnt_half_lines=False` (F-012 —
  the exact-match-verified correction, highest confidence of all four)

**Deliberately NOT applied**: `lperiod_by_dpi[7200]` stays at the SE's
15963 — the second session's own two 7200dpi captures disagree with each
other (15914 vs 15999) and neither matches the original session's 16035,
so there's no single evidence-backed value to pick yet. This is issue
#35's leading suspect; a wrong guess here is exactly the case for "verify
on hardware before shipping," per the user's own framing.

**Caught and fixed a coupling bug while applying the dummy correction**:
`Model8200iSE.shading_strip_clocks()`'s dark-strip fallback reads
`self.dummy_by_dpi` when no dark-specific table entry exists (true at
7200dpi) — so naively overriding `dummy_by_dpi[7200]` would have silently
changed the *dark shading strip's* dummy too (previously confirmed at 23),
not just the intended image-pass dummy. Fixed by pinning both dark (23)
and white (0x10, unchanged) explicitly in `Model8100V2.shading_strip_clocks`
for 7200dpi, so the two paths can't get coupled by accident.

**Updated `tests/test_multi_model.py::test_8100_v2_capture_derived_constants`**
(PR #30's own test, which was asserting the now-superseded values) to match
— all 244 tests pass (3 pre-existing skips, unrelated).

**Also fixed while verifying**: chased down an apparent 7-test regression
that turned out to be a non-reproducing artifact of an ad-hoc test-file
subset/order I picked for a quick check — the full default `pytest`
discovery order never showed it, before or after my changes. Not a real
issue; noted so a future session doesn't re-chase the same ghost.

**Git remotes**: added `fork` → `https://github.com/TobbyTravel/pyopticfilm.git`.
Fetched and confirmed `fork/main` is identical to `origin/main` (both at
`8b6e014`, 2 commits ahead of this branch's base — both AFE/calibration-only
fixes, verified to not touch anything this branch's findings depend on).
Rebasing this branch onto that before committing.

**Done since**: `drafts/pr-c-constants.md` rewritten to describe the
applied change (not a proposal); `docs/hardware-reference-8100v2.md` and
`docs/scanner-validation.md` updated to reflect applied-vs-unresolved
status. Set local git identity (`TobbyTravel <2717002+TobbyTravel@
users.noreply.github.com>`, this repo only — none was configured on this
machine, asked the user first). Committed as 3 logical commits (tooling,
docs+fixtures, applied constants), rebased cleanly onto `origin/main`
(`8b6e014`), pushed to `fork/hw-ref/8100v2-capture-analysis`, opened
https://github.com/TobbyTravel/pyopticfilm/pull/10 against the fork's
`main`. Full suite after rebase: 248 passed, 3 skipped.

---

## 2026-08-30 — Posted draft comments to issues #33 and #35

At the user's explicit request. Updated both drafts first to reference the
applied fixes (dummy_by_dpi correction, feed_to_scan_steps correction) and
link PR #10 on the fork, rather than posting the pre-fix versions. Posted:

- #33: https://github.com/jboneng/pyopticfilm/issues/33#issuecomment-5464019501
- #35: https://github.com/jboneng/pyopticfilm/issues/35#issuecomment-5464019792

Both drafts in `docs/hw-ref/8100v2/drafts/` updated with "POSTED" headers
and the comment URLs, for traceability.

---

## 2026-08-30 — Two more analyses, no more captures needed (user request: "do things you can do right now without more input")

**F-014 — decoded pyopticfilm's `0x21` vendor-probe pattern against the
captures.** Extended `capture_ledger.py` to decode the
`bRequest=REQUEST_REGISTER` probe-read wire format (distinct from the
ASIC register-read path already decoded). Result: the reference driver
**never** polls `wIndex=0x21` (what `gl128.py`'s `_FEED_PROBE_INDEX` uses)
in any capture. It polls `wIndex=0x20` (constant response 0x55) and
`wIndex=0x18` (2 or 18) instead, both clustered tightly around the two
positioning `FEEDL` writes — feed-related, but not the mechanism
pyopticfilm currently implements. Meaning of both indices unresolved.
Updated `issue-33-diff.md`.

**F-015 — characterized bulk-IN read cadence during the image pass.**
Added an opt-in `--bulk-timing` mode to `capture_ledger.py` (per-packet
`(timestamp, size)`, never payload bytes). Checked file 4 (7200dpi):
smooth cadence throughout the actual image transfer — 6796 chunks,
11.2ms median interval, zero gaps over 50ms. (Three gaps up to 3.02s exist
in the unfiltered capture but all occur during shading/calibration, before
the image preamble — a filtering trap worth remembering: don't threshold
on chunk size alone across a whole capture, calibration bulk transfers are
large too.) Rules host-side read-pacing less likely as an issue #35 cause,
reinforcing the register-level candidates (LPERIOD, dummy). Only checked
on one capture so far. Updated `issue-35-diff.md`.

**Tests**: extended `tests/test_capture_ledger.py`'s synthetic capture with
a probe-read packet and a `track_bulk_timing=True` case; both new
assertions pass. `build_ledger()`'s return signature changed from a
2-tuple to a 3-tuple (`events, summary, bulk_in_timeline`) — updated the
one other caller (`capture_ledger.py`'s own `main()`). Full suite: 249-251
passed depending on collection order (252 total tests collected both
times — the 1-vs-3-skipped variance across runs is pytest's own skip
reporting, not a regression; verified with `-rs`).

**Regenerated all 10 ledgers** with the new probe-read decoding (adds
`probe_read_events` to every `*.summary.json` and `probe_read` events to
every `*.events.json`).

**Not done, flagged for later**: F-014/F-015 are each single-capture or
qualitative findings, not yet cross-checked across DPIs or the full-sensor
7200dpi capture (file 11). No new captures were needed or requested for
this round, per the user's ask to do only what's possible "right now
without more input."

**Next**: commit and push to `fork/hw-ref/8100v2-capture-analysis` (updates
PR #10) so the user can pull it down alongside the earlier changes for
hardware testing.

---

## 2026-08-30 — Applied F-014: disabled the unverified `0x21` feed probe for V2

At the user's request: "8100 V2 support in repo was originally added but
just bulk copying values from 8200i, then updated by captures... if old
code is contradicted, let's make a PR and assume new is correct."

**Investigated before acting** (per the working method — don't just swap
in a new guess for an old one): re-checked whether `wIndex=0x20`/`0x18`
(F-014's newly-found probes) cluster specifically around feeds, as first
thought. They don't — they're queried from device-open onward at a steady
rate throughout init/calibration/positioning, which reads more like a
generic engine-busy/heartbeat check than a feed-completion signal. This
ruled out simply swapping `_FEED_PROBE_INDEX` from `0x21` to `0x20`
without evidence it means what the old code assumed `0x21` meant.

**What's actually contradicted, cleanly**: the `0x21` probe itself.
`gl128.py`'s `_FEED_PROBE_INDEX=0x21` is shared code between the SE and
V2, but every code citation for it traces back to an SE-only capture
(session 03) — never independently confirmed on V2. The second session
shows V2 never queries `wIndex=0x21`, in any context, across all 10
captures.

**The fix**: added `Model8200iSE.feed_probe_index: int | None = 0x21`
(SE keeps today's behavior, unchanged) and overrode it to `None` on
`Model8100V2`. `Gl128._read_feed_probe()` now reads this per-model and
skips the USB round-trip entirely when `None`. This is low-risk because
`_wait_feed_probe_done()` already ORs the probe check with the
already-confirmed `0x101` status-register `FEEDFSH` path as a fallback —
so V2 loses an unverified, evidently-dead check and keeps the one
mechanism actually confirmed by captures. Cleaned up docstrings/error
messages in `gl128.py` that hardcoded "wIndex=0x21" language to stay
accurate for both models.

**Tests**: new `tests/test_gl128_feed_probe.py` — direct behavioral proof
(not just constant comparison) that V2 makes zero USB calls for the probe
and SE's call is unchanged. Extended
`test_multi_model.py::test_8100_v2_capture_derived_constants` with the new
attribute. Full suite: 251 passed, 3 skipped.

**Updated**: `findings.md` F-014 (added the "not feed-specific" follow-up,
marked applied), `issue-33-diff.md`, `scanner-validation.md`,
`docs/hardware-reference-8100v2.md`'s open-questions list (now "resolved
and applied").

**Not done**: didn't touch the 8200i SE's own `0x21` behavior — no SE
captures in this project to check it against, so left exactly as it was.
`0x20`/`0x18`'s actual meaning remains unresolved and unadopted.

---

## 2026-08-30 — Experimental A/B overrides for the two remaining hardware-only items

User scoped remaining work down to exactly two items (skipping the
`0x20`/`0x18` probe meaning, file 7's DPI, and the X-window gap) and asked
for a "package deal" PR the user can A/B locally with Scan Lab against
real hardware — into the fork only, not upstream.

Built two opt-in, env-var-gated experimental overrides rather than
guessing a single default for either (both remain genuinely unresolved by
analysis alone — that's the whole reason they're on this list):

- **`POF_GL128_V2_LPERIOD_7200=<int>`** (issue #35, F-009): overrides
  `LPERIOD` at 7200dpi only. `Model8100V2.line_period_for()` now checks
  this before falling back to the unchanged SE default (15963). Lets the
  user try 15914, 15999, or anything else without a code change per
  attempt.
- **`POF_GL128_V2_FIRST_SCAN_FEED2=1`** (issue #33, F-007/F-008): the
  *first* image pass an asic instance runs targets `first_scan_feed2_steps`
  (13128, "top of TA window") for the second feed instead of the normal
  ~13486, then reverts for every scan after — mirrors the reference
  driver's own observed first-scan behavior. New `Gl128._first_scan_feed2_
  done` flag (mirrors `Scanner._gl128_primed`'s "once per instance"
  pattern) gates it in `session_gl128.py::_configure()`.

Followed the existing `POF_GL128_PRIME` env-var pattern in `scanner.py`
for consistency. Both default off — unset, behavior is unchanged from
before this session; verified directly (not just by reading the code) with
mock-hardware scans checking the actual `FEEDL`/`LPERIOD` register values
written, using an area with `y1=0.5` specifically so the "normal" and
"override" feed2 values are provably different (an earlier check with a
`y1=0` tiny-crop area coincidentally computed the same value either way,
which would have masked a bug).

**Tests**: new `tests/test_gl128_v2_ab_overrides.py` — 4 tests, covering
both overrides' on/off states via mock scans plus the `Gl128Registers`
attribute checks. Full suite: 255 passed, 3 skipped, clean lint.

**New doc**: `docs/hw-ref/8100v2/ab-testing-guide.md` — exact PowerShell
commands to run each A/B test via Scan Lab, what a useful result (positive
*or* negative) looks like for each, and an honest caveat that Scan Lab's
single-pass-only scans can't fully decouple "first scan of session" from
"first pass of multi-pass" for issue #33 — that still needs either a
capture or a Scan Lab feature this project doesn't have.

**Scope respected**: fork only (`fork` remote =
`TobbyTravel/pyopticfilm`), no upstream (`origin` = `jboneng/pyopticfilm`)
commits or pushes. Same PR #10, new commit.

---

## 2026-08-30 — HARDWARE INCIDENT: real fault during first-ever real-hardware test, two hard power-offs required

**Read this before touching the 8100 V2 with this branch again.**

### What happened, in order

1. Confirmed real 8100 V2 connected and healthy (WinUSB bound, clean
   parked status: at_home, motor/lamp off, no fault bits) before any scan
   attempt.
2. User confirmed real film loaded (for visual jaggedness assessment,
   issue #35 testing).
3. **Attempt 1** (`tools/hw_ab_capture.py`, PR branch as merged, 7200dpi,
   explicit crop area `(0,0,1,0.9)`): failed mid-image-transfer with a USB
   `Pipe error`. The subsequent motor-stop cleanup *also* failed with a
   pipe error ("carriage position unknown"). No physical damage reported,
   but a real fault, not a benign timeout.
4. User had to power-cycle the scanner to recover.
5. **Attempt 2** (same tool, `--revert-lincnt` — i.e. `image_lincnt_per_line`/
   `usb_image_lincnt_half_lines` reverted to pre-session values, the other
   3 fixes — `feed_to_scan_steps`, `dummy_by_dpi[7200]`, `image_depth_a`,
   `feed_probe_index` — still applied; small crop area `(0.3,0.3,0.5,0.5)`
   to reduce risk): failed the same way, this time with the device fully
   dropping off the USB bus (`No such device`).
6. User had to power-cycle again.
7. **Attempt 3** (Scan Lab GUI, same PR branch, user-driven): **high-pitched
   sound followed by a thunk** — consistent with a motor overspeed/
   hard-stop event, not just a communication fault. User had to power off
   the scanner (described as "the printer") to stop it.
8. Static code review (no hardware) found a pre-existing comment in
   `session_gl128.py` next to the LINCNT/travel-window check: *"Overrunning
   it is what ground the motor in the Lab"* — confirming this exact failure
   mode (grinding from a feed2+travel overrun) has a known history in this
   codebase. Numerically verified the `max_lincnt_for` guard was satisfied
   in both failed script attempts (did not itself explain the fault).
9. **Attempt 4 — controlled A/B, the one that actually isolated something**:
   clean, unmodified `main` (separate git worktree
   `../pyopticfilm-clean-main`, isolated venv), Scan Lab, user-driven: ran
   cleanly, no fault. **Attempt 5**: PR #10 branch as merged (all 4 fixes
   applied, this repo's own venv), Scan Lab, user-driven: **same
   high-pitched/thunk fault, third occurrence**, third hard power-off.

**Conclusion from attempts 4 vs. 5**: something in this branch's applied
changes causes the fault; clean `main` does not reproduce it under the same
user-driven Scan Lab workflow. This is a real, reproducible regression
introduced somewhere in this branch — not a pre-existing hardware issue,
not specific to the ad-hoc test script (attempt 5 used the actual Scan Lab
GUI workflow attempt 1-2 didn't).

**Not yet known**: which of the 4 applied fixes (or their combination) is
responsible. `feed_to_scan_steps` was never actually exercised in attempts
1-2 (both used explicit crop areas, which don't use that field — only
`area=None` full-frame requests do); attempt 2 already ruled out
`image_lincnt_per_line`/`usb_image_lincnt_half_lines` as solely
sufficient to explain the fault (reverting it alone didn't stop the fault),
though it hasn't been tested as the *only* fix applied (opposite
direction). No motor-speed/slope-control register is touched by any of the
4 fixes directly (dummy affects pixel-clock padding, DEPTH_A affects data
width, feed_probe_index is host-side polling only) — none has an obvious
mechanism for an overspeed symptom on inspection, which is part of why
static review alone couldn't pin this down.

### Response

At the user's explicit direction ("I prefer hardware testing and not
afraid of breaking it... try smaller parts until we figure out the problem
one and skip it for now") — not a full permanent revert, but converting
**all five** of this session's applied model/protocol changes into
individually opt-in `POF_GL128_V2_FIX_*` env-var flags, **all defaulting
off** (verified to reproduce `main`'s exact values field-by-field with
nothing set — see `test_8100_v2_capture_derived_constants`). One flag
change: `feed_probe_index` and `image_depth_a` are also included even
though their mechanism seems least likely, since both were present in
every failed attempt and neither has been tested absent.

**Caught and fixed a real bug while doing this rework**:
`session_gl128.py`'s `getattr(model, "image_depth_a", r.DEPTH8_A)` only
handles the attribute being *absent* — once `image_depth_a` became a real
field on `Model8100V2` (even when set to `None` for "flag off"), `getattr`
found it and returned `None`, and `int(None)` crashed. Caught by a test,
not by hardware — fixed to explicitly check `is None` rather than relying
on `getattr`'s default argument. This bug would have affected *every*
V2 scan once `image_depth_a` existed as a field at all, regardless of the
new flag's state — a good reminder that "add an optional field with a
sentinel default" needs the *reading* code updated too, not just the
field definition.

**`tools/hw_ab_capture.py` simplified**: the ad-hoc `--revert-lincnt` flag
is gone — now that the model reads all five `POF_GL128_V2_FIX_*` vars
uniformly at import time, plain `Scanner.open()` picks up whatever's set
in the shell before the process starts, same as Scan Lab.

**`docs/hw-ref/8100v2/ab-testing-guide.md`** rewritten with the incident
summary, all 5 isolation flags, a suggested (not authoritative) test
order, and explicit hardware-safety rules for every future attempt
(physical presence, hand on power, one flag at a time, stop immediately on
any unusual sound, stop entirely if clean `main` ever faults too).

**Tests**: `test_8100_v2_capture_derived_constants` reverted to assert the
pre-session (`main`) values with nothing set; new
`test_8100_v2_per_fix_isolation_flags` proves each flag changes only its
own attribute(s); `test_gl128_feed_probe.py` repurposed to prove the
opt-out *mechanism* still works via the flag rather than asserting V2's
now-reverted default; `test_gl128_v2_ab_overrides.py`'s LPERIOD
no-op-value assertion updated (16035, not 15963, since V2's own default is
restored). Full suite: 259 passed, 1-3 skipped (flaky skip *count*
reporting across runs, not a real difference — same root cause noted in
an earlier entry).

**Not done**: root cause still unknown. No further hardware testing
happened or is planned within this session — next hardware session should
follow `ab-testing-guide.md`'s one-flag-at-a-time procedure, with the user
physically present throughout.

---

## 2026-08-30 — Isolation testing begins: `FIX_LINCNT` tested alone-applied, mechanically clean but reveals a decode coupling bug

User physically present, hand on power. First isolation test, chosen out of
suggested order: `POF_GL128_V2_FIX_LINCNT=1` only (every other flag at its
default/off), via Scan Lab. This is the direction not yet tested — the
incident log's attempt 2 tested LINCNT as the only *reverted* change (others
still applied); this test is the only *applied* change (others reverted).

**Result: no mechanical fault.** Low-DPI prescan (1200dpi, Scan Lab's fixed
preview) and a full 7200dpi Scan both completed — "sounds fine," no unusual
sound, no pipe error, no dropped device. USB log tail at 7200dpi confirms a
clean run: steady `bulk_read 61440`/`768`-byte pairs throughout (the healthy
per-line-chunk pattern from F-004) followed by a normal-looking end-of-scan
`0x83` register-write sequence and `0x018e` status polls — no error banner
in Scan Lab.

**But the resulting 7200dpi image is badly corrupted (analyzed directly —
`color_short_7200dpi.tif`, 7206×10272×3 uint16):** roughly the left ~80% of
the frame is black with vertical single-pixel-wide color-channel streaks
(red/green/blue/white lines on black) — classic byte/line-width
misalignment. Only a strip on the right (~20% of width) decodes as real
content (correctly colored wood-grain/film subject matter). Low-DPI prescan
was fine (Scan Lab's 1200dpi "safe window" preview, small/simple enough not
to expose this).

**Working hypothesis, not yet verified against the source:** `image_lincnt_
per_line=1`'s byte-count formula (F-012) was exact-match-verified against
second-session captures that *also* carried the `DEPTH_A` mixed-pair
register (0x04/0xFF, F-013) — this test applied LINCNT alone, with DEPTH_A
still at its unfixed default (matched DEPTH8/DEPTH8 pair). If the ASIC's
actual output width/depth under the unfixed DEPTH8/DEPTH8 pair differs from
what it was under the captured DEPTH_A=0x04 pairing, the host's LINCNT-
derived byte-count assumption would disagree with the real bulk stream —
producing exactly this kind of line-boundary shear. Not confirmed by
re-reading `register-program-by-dpi.md`'s verification table this session;
flagged as the next thing to check before trusting LINCNT and DEPTH_A as
independently testable.

**Practical effect on today's plan:** LINCNT-alone (applied direction) is
cleared as a *mechanical* fault trigger — first flag to pass the listen
test in this direction. The image corruption is a separate, real software
bug (LINCNT/DEPTH_A coupling), not evidence toward the motor fault, and is
not being fixed mid-isolation-session per the existing "don't fix during
isolation, report and decide next steps" rule. `ab-testing-guide.md` updated
with this result.

**Next**: continue one-flag-at-a-time isolation with the remaining
untested flags (`FEED_STEPS`, `DUMMY`, `DEPTH_A`, `FEED_PROBE`).

---

## 2026-08-30 — `FIX_DEPTH_A` tested alone: also mechanically clean, same corruption signature as LINCNT

Same session, user still physically present. `POF_GL128_V2_FIX_DEPTH_A=1`
only (LINCNT unset back to default first), via Scan Lab, 7200dpi.

**Result: no mechanical fault** — "sounds fine," cropping/geometry looked
correct. **But the image is corrupted again** — user visually confirmed
both this and the LINCNT-alone tiff bug out mid-scan (black, missing image
halfway through), and — importantly — **neither shows the jagged-line
symptom issue #35 is chasing**, a useful negative result in its own right.
USB log tail was a long repeated run of `control_read val=0x018e
idx=0x0122` — decoded as register `0x101` (`FEEDFSH`/`HOMESNR`, the
documented feed-completion status bit), i.e. stuck in the end-of-scan
park/home wait loop, not an image-transfer fault.

**Revised read, per user's own observation:** two *different* single-flag
changes producing the same corruption signature suggests this isn't
specifically an artifact of either flag's own mechanism — more likely
something common to enabling any override at 7200dpi on real hardware
(possibly interacting with the still-unfixed default LPERIOD, or a
7200dpi-specific path that behaves differently from what the reference
captures/earlier analysis assumed). User's framing: worth a dedicated
investigation *after* all 5 flags are individually characterized for the
mechanical question, not now. Continuing isolation; a baseline (all flags
off, same 7200dpi Scan Lab run) is being checked next specifically to see
whether this corruption is flag-caused at all or pre-existing on this
branch/session regardless of flags.

---

## 2026-08-30 — Baseline (all 5 flags off) also reproduces the corruption/mid-scan stall — this bug predates and is unrelated to this session's changes

User power-cycled the scanner first (previous two runs both ended in the
`0x101` park/home poll loop rather than a confirmed-clean park — cycling
power removed any doubt about carried-over carriage state before this
control run). Then ran the identical 7200dpi Scan Lab scan with every
`POF_GL128_V2_FIX_*` var unset — this branch's code is verified
(`test_8100_v2_capture_derived_constants`) to reproduce `main`'s exact
register values with nothing set, so this is a legitimate main-equivalent
control, done in-repo without needing the separate clean-`main` worktree
from the original incident.

**Result: mechanically clean again** (no unusual sound — three-for-three
now across LINCNT-alone, DEPTH_A-alone, and this baseline). **But the same
corruption reproduced** — visually confirmed (`3.color_short_7200dpi.tif`,
7206x10272x3): same black-with-vertical-streaks pattern over most of the
frame, same valid strip in the same position on the right, indistinguishable
from the LINCNT-alone and DEPTH_A-alone results.

**Conclusion: the mid-scan corruption/stall bug is not caused by any of
this session's 5 applied fixes -- it reproduces on plain `main` behavior at
7200dpi on this real hardware.** The original incident's attempt 4 ("clean
main, ran cleanly, no fault") only checked for the audible mechanical fault,
never inspected image content, so this bug could have been present and
undetected then too. This is a separate, real, reproducible issue from the
motor-fault question this session is isolating -- user's plan is to fully
characterize the mechanical-fault isolation first, then investigate this
corruption/stall bug on its own once all 5 flags are individually cleared
or implicated. User also noted (their own experience, not to be treated as
a documented capture-derived claim) that this kind of failure has not been
observed with other known-good scanning software on the same hardware --
consistent with this being a real pyopticfilm-specific driver bug at
7200dpi, not a hardware or film-handling issue.

**Practical effect on the mechanical isolation plan: unaffected and still
valid.** The guide's stop-rule ("if clean main ever produces *the fault*
again, stop") refers specifically to the audible motor fault, which has not
recurred on this baseline -- isolation testing for the mechanical question
continues as planned. `ab-testing-guide.md` updated with a note
distinguishing the two issues so a future session doesn't conflate them.

**Next**: continue with `FEED_STEPS` and `FEED_PROBE`, the two remaining
untested flags. Image-corruption root-causing is explicitly deferred until
mechanical isolation is complete, per the user's own sequencing.

---

## 2026-08-30 — MECHANICAL FAULT REPRODUCED: `POF_GL128_V2_FIX_FEED_PROBE` alone is the culprit

**Isolation testing stops here.** Per `ab-testing-guide.md`'s own "what
worked looks like" rule -- found the single flag that reproduces the fault
alone, stop, don't try to fix it in the same session, report back.

**What happened:** user clicked Prescan (1200dpi, Scan Lab's fixed low-res
safe-window preview -- not even a full 7200dpi scan) with only
`POF_GL128_V2_FIX_FEED_PROBE=1` set (every other flag at default/off, the
same clean state that just passed three mechanically-silent runs in a row:
LINCNT-alone, DEPTH_A-alone, baseline). The fault happened **during
positioning/feed, before the image pass ever started** -- described as
sounding like it "tried to do normal process, but very different pitch,"
consistent with the original incident's high-pitched/thunk motor-fault
description. No error banner or warning preceded it. User cut power
immediately, scanner is now safely powered off. No injuries, no reported
physical damage beyond the earlier incidents' history.

**This flag was considered a low-probability suspect going in** --
`ab-testing-guide.md`'s suggested order ranked it #4 of 5, and
`PROGRESS.md`'s incident entry noted "no motor-speed/slope-control register
is touched by any of the 4 fixes directly... `feed_probe_index` is
host-side polling only" with "none has an obvious mechanism for an
overspeed symptom on inspection." That inspection was wrong, or the
mechanism is indirect.

**Working hypothesis for the actual mechanism (not yet verified against
code -- flagged for the next, non-hardware session):**
`POF_GL128_V2_FIX_FEED_PROBE=1` sets `Model8100V2.feed_probe_index = None`,
which makes `Gl128._read_feed_probe()` skip the `wIndex=0x21` USB
round-trip entirely (per the F-014 fix already applied unconditionally to
this flag's *code path* -- see the "Applied F-014" entry above).
`_wait_feed_probe_done()` was documented as ORing the probe check with the
`0x101` FEEDFSH status-register path as a fallback "so V2 loses an
unverified, evidently-dead check and keeps the one mechanism actually
confirmed by captures" -- but this hardware result suggests that fallback
path, when it's the *only* path (probe disabled), does not stop the feed
motor correctly during positioning, allowing an overrun into a hard
mechanical limit. This directly matches the pre-existing code comment
found during the original incident's static review: *"Overrunning it is
what ground the motor in the Lab"* (next to the LINCNT/travel-window check
in `session_gl128.py`) -- a different register (LINCNT) but the same
overrun-into-hard-stop failure mode. Whether the bug is in
`_wait_feed_probe_done()`'s fallback timing/threshold, a missing early-exit
condition that only the `0x21` probe used to provide, or something else in
the feed-completion logic, is **not yet investigated in code** -- this
entry is the hardware evidence only.

**Also notable:** this reproduced at 1200dpi Prescan, not 7200dpi -- the
fault is not 7200dpi-specific (unlike the corruption/stall bug from the
previous three entries, which was 7200dpi-only in what's been tested so
far). This is a different symptom with a different DPI footprint from the
corruption bug -- treat them as two more data points toward two genuinely
separate issues, not the same root cause.

**Status: FEED_PROBE is the prime suspect for the original hardware
incident.** Not yet proven sufficient on its own across repeated trials
(this is one occurrence), and not yet proven necessary (the original
incident's failed attempts all had `FIX_FEED_PROBE`'s equivalent behavior
implicitly active, i.e. this is consistent, not yet cross-checked against
a run where every other flag is on and only this one is off). No further
hardware testing planned this session. `ab-testing-guide.md` updated.

**Not done / explicit next steps for a future session:**
1. Read `Gl128._wait_feed_probe_done()` and `_read_feed_probe()` in
   `gl128.py` to find the actual mechanism -- why does losing the `0x21`
   probe (with the `0x101` fallback supposedly already in place) allow an
   overrun during positioning?
2. Once a code-level cause is found and fixed, do **not** re-test on
   hardware without the user's explicit go-ahead and the same safety
   protocol (physically present, hand on power, one change at a time).
3. Do not re-enable `POF_GL128_V2_FIX_FEED_PROBE` on real hardware again
   until the fix above is made and reasoned through -- this flag is now
   downgraded from "low-risk experimental" to "confirmed hazardous" in its
   current form.
4. `FEED_STEPS` remains the one never-tested flag -- explicitly not tested
   this session, no conclusion either way about it.

---

## 2026-08-30 — Code investigation of the FEED_PROBE mechanism (no hardware touched)

Read `Gl128._wait_feed_probe_done()`, `_read_feed_probe()`, `_feed_capture()`,
and the nearby `_wait_idle_at_home_for_stationary()` in `gl128.py` to find
why disabling the `0x21` probe changed real-hardware behavior, given the
`0x101` FEEDFSH fallback was believed to already be the sole mechanism V2
relies on either way.

**What the flag actually changes, precisely:** `feed_probe_index=None`
makes `_read_feed_probe()` return `-1` immediately with **no USB round-trip**
(`index is None` short-circuit). With the flag off (pre-session default,
`0x21`), every loop iteration in `_wait_feed_probe_done()` performs one
extra `read_request_register(0x21)` control transfer before its status
check. **On real V2 hardware this probe value never actually reaches
`_FEED_PROBE_DONE` (0x04) either way** (F-014 -- V2 never meaningfully
responds to `wIndex=0x21`), so completion is decided by the same condition
in both flag states: `status.is_feeding_finished and not
status.is_motor_enabled` from the `0x101` register. **The only real
difference is poll-loop cadence** -- one fewer control transfer per
iteration when the probe is skipped, tightening the loop.

**Two concrete weaknesses found in the completion-detection code itself
(present regardless of this flag, but plausibly what the cadence change
exposed):**

1. **No cross-iteration debounce on the completion sample.**
   `_wait_feed_probe_done()`'s Phase 2 check accepts `is_feeding_finished
   and not is_motor_enabled` from a single `read_status_reliable()` call
   (itself a double-read-discard-first, but only within that one call) as
   soon as `min_motion_s` has elapsed. Compare
   `_wait_idle_at_home_for_stationary()` a few hundred lines earlier in the
   same file, which requires **two consecutive** idle samples
   (`idle_hits >= 2`) before accepting an equivalent at-home/motor-off
   condition, specifically because "captures show one stale `0x101`." The
   feed-completion path has no equivalent protection against one noisy or
   transiently-stale sample.
2. **`min_motion_s` floor is very low.** `_feed_capture()` computes
   `expected_s = 1.0 * (steps / ref_steps) * 0.9` then clamps to
   `min(0.25, max(0.05, expected_s))` -- for a small positioning feed (the
   fault happened during Prescan positioning, a short move), this floor can
   be as low as **50ms**, a thin margin against accepting a premature
   completion sample.
3. **Dead code that looks like it was meant to cover exactly this:**
   `_FEED_START_TIMEOUT_S = 2.0` is defined (line 127, with a comment
   describing "how long a feed may go without reporting motion before its
   start-up state is taken at face value... a completion seen inside this
   window is the *old* move's") but **is never referenced anywhere else in
   the file** -- an apparently unfinished or abandoned safety check, not
   currently wired into `_wait_feed_probe_done()` at all.

**Hypothesis (not verified against hardware, not proven -- reasoning only):**
without the extra `0x21` round-trip, the poll loop ticks measurably faster.
If `FEEDFSH`/`MOTORENB` glitch or briefly read a stale/transient combination
near the true start or end of a short feed, the faster loop has more
opportunities to sample exactly during that window, and nothing in the code
protects against accepting it (no consecutive-sample debounce, a 50ms-floor
minimum-motion guard, and the seemingly-intended start-timeout guard never
wired up). If a false-positive completion is accepted while the carriage is
still moving at speed, the immediately-following register write
(`self._write(r.REG_0x02, r.FASTFED)`, clearing `MTRPWR`) would cut motor
power abruptly mid-motion -- a plausible mechanism for an abrupt mechanical
stop ("thunk") rather than a true overspeed event. This is a reasoned
hypothesis from static reading only; no timing instrumentation or hardware
test has been done to confirm it.

**Not done, explicitly holding for user direction:**
- No code change made yet. Candidate fixes (add consecutive-sample
  debounce matching `_wait_idle_at_home_for_stationary()`'s pattern; wire up
  or remove the dead `_FEED_START_TIMEOUT_S`; raise the `min_motion_s`
  floor) are not yet written -- deciding which to pursue, and whether to
  restore the probe as a per-model safety net instead of relying solely on
  the untested-faster-cadence status path, is the user's call.
- No hardware re-test of any kind planned or authorized. `FEED_PROBE` stays
  flagged "confirmed hazardous, do not enable" regardless of any code
  change made from this analysis, until the user explicitly authorizes
  testing again under the same physical-presence safety protocol.

---

## 2026-08-30 — Debounce fix did NOT stop the fault; second real fault this session, hypothesis revised

User tested the debounce fix (`57ef166`) with `POF_GL128_V2_FIX_FEED_PROBE=1`
at 1200dpi Prescan, same setup as the flag's first failure. **Same terrible
sound and mechanical fault. Powered off safely, no injuries, no reported
damage.** This is the fourth real hardware fault across the whole project
(2 in the original incident, 1 in this session's first FEED_PROBE test, 1
just now) and the second under this session's own isolation testing.

**The debounce fix is disproven as sufficient (possibly irrelevant
entirely).** It assumed the *response value* of a status/probe sample
mattered (a stale or transient single sample being wrongly accepted as
"done"). That assumption was already in tension with F-014's own finding
that the `0x21` probe response is semantically meaningless for V2 — there
is no real "done" signal in it to be stale or transient in the first place.

**Revised hypothesis, now backed by a clean correlation across every
documented attempt in this project's history, not just today's tests:**

| Config | `0x21` queried? | Result |
|---|---|---|
| Original incident attempts 1, 2, 3, 5 (pre-session default: V2 had no `feed_probe_index` override yet, but the *applied* F-014 fix from earlier that day had already set it to disabled going into the incident) | No | Fault (x3) |
| Original incident attempt 4 (clean `main`, pre-branch V2 default) | Yes | Clean |
| This session: LINCNT-alone, DEPTH_A-alone, baseline | Yes | Clean (x3) |
| This session: `FIX_FEED_PROBE` alone | No | Fault |
| This session: `FIX_FEED_PROBE` + debounce fix | No | Fault |

**Every single clean run in this project's real-hardware history had the
`0x21` query active. Every single fault had it disabled.** 7 for 7.

This reframes F-014 (`findings.md`): the finding that `0x21`'s *response*
is meaningless for V2 (never transitions, never matches `_FEED_PROBE_DONE`)
still appears correct and is not contradicted. What's newly implicated is
the *query itself*, independent of its response. `findings.md` F-014 already
documents that the reference driver polls a **different** vendor probe
(`wIndex=0x20`/`0x18`) continuously throughout the *entire* session — not
just around feeds — and explicitly describes this as reading "more like a
generic engine-busy/heartbeat check than something specific to feed
completion." pyopticfilm has no equivalent of that steady heartbeat
traffic anywhere in its own code; the `0x21` query, despite being the wrong
signal to *read*, may have been incidentally serving an equivalent
keep-alive role simply by keeping a baseline rate of vendor-request bus
traffic flowing to the ASIC during positioning. Removing it entirely (not
its meaning — its *existence*) is the one variable that has moved in
lockstep with every fault and every clean run so far.

**Proposed fix (not yet applied, holding for user direction): stop
`feed_probe_index` from ever being `None` on `Model8100V2`.** Concretely,
retire the disable capability entirely -- `POF_GL128_V2_FIX_FEED_PROBE`'s
effect and F-014's "applied" code change (`feed_probe_index: int | None =
None` default) both get reverted to always query `0x21`, matching every
configuration that has ever tested clean on real hardware. This requires
**no new hardware test to justify** as a default -- "probe always queried"
is already the empirically-clean condition, proven 4 times now (attempt 4 +
this session's 3 clean runs). It only needs a hardware test if the user
wants to confirm the *fix* specifically (i.e. deliberately re-run the exact
1200dpi Prescan that has now faulted twice, with the reverted code, as a
positive confirmation) -- and after two real faults in one session, that
confirmation run is the user's call on timing, not something to do
immediately.

**Also revise the debounce commit's status:** `57ef166`'s change is not
proven wrong (nothing here contradicts requiring 2 consecutive samples
being *a* good practice generally, matching the existing
`_wait_idle_at_home_for_stationary` pattern), but it's proven **not
sufficient on its own** as a fix for this specific fault. Leaving it in
place (harmless, still passes all tests) while the actual fix (below) is
what's pursued next.

**Done, same session:**
1. `feed_probe_index=None` retired -- `Model8100V2` no longer has a field
   override for it at all; unconditionally inherits
   `Model8200iSE.feed_probe_index=0x21`. `POF_GL128_V2_FIX_FEED_PROBE` no
   longer exists as an env var with any effect (regression-tested).
2. `findings.md` F-014 given a follow-up note (not a full rewrite -- the
   response-meaninglessness finding stands) linking to this entry.
3. `ab-testing-guide.md` updated with the retirement.
4. `tests/test_gl128_feed_probe.py` rewritten for the retired flag (probe
   always active now); `test_multi_model.py`'s per-fix isolation test
   updated to drop the retired case. Full suite: 260 passed, 1 skipped.
   Lint clean.

**Not done:** no further hardware testing planned or requested right now,
after two real faults in this session. This code change needs no hardware
test to justify as a default (every clean-tested configuration already had
this exact behavior) -- a confirmation run is optional, user's call on
timing. `FEED_STEPS` remains the only completely untested flag; it doesn't
touch `feed_probe_index` and there's no reason from this analysis to
suspect it shares this mechanism, but it stays formally unproven until
actually tested.

---

## 2026-08-30 — CONFIRMATION RUN ALSO FAULTED: the FEED_PROBE correlation is broken, fault is likely intermittent and not isolated to any tested flag

User ran the confirmation test at the user's own request: identical
config to the retirement fix (`90b98b6`, no env vars set,
`feed_probe_index` hardcoded to `0x21`) — the exact configuration that had
already tested clean three times this session (LINCNT-alone, DEPTH_A-alone,
baseline). **Same terrible sound, same failure, third real fault this
session, fifth total across the project.** Powered off safely, no injuries,
no reported damage. User confirmed the symptom was indistinguishable from
the two prior FEED_PROBE faults (same sound/timing) and nothing else about
the setup differed from the earlier clean runs (same film, same calibration
state, no noted warm/cold-start difference).

**This breaks the 7-for-7 correlation the retirement fix (`90b98b6`) was
based on.** The exact configuration that just faulted is the same
configuration that ran clean 3 times earlier today. That rules out
`feed_probe_index` (or any of this session's 5 flags) as a *deterministic*
single-variable explanation — whatever this is, it is not reliably
triggered or reliably avoided by any of the code changes tested so far.

**Revised read:** consistent with the user's own earlier observation
("intermittent with our driver, never experienced with [other reference
software]") — the fault looks intermittent rather than caused by a specific
register/flag. Possible explanations, none confirmed:
- A real, independent intermittent hardware/mechanical/electrical issue
  specific to this scanner unit or its connection (cable seating, power
  supply, connector wear, thermal behavior over a long session of repeated
  feed/park cycles) — unrelated to any of pyopticfilm's code.
- A timing race that any of these configs can hit at some (currently
  unknown) probability, meaning the earlier "clean" results were not proof
  of safety, just absence of an unlucky sample — plausible given this
  session ran many feed/park cycles back-to-back, which could itself matter
  (accumulated wear, thermal drift, or a race that gets more likely later
  in a long session) but is not evidenced, just not ruled out.
- Something outside the isolated flags entirely (unrelated code on this
  branch vs. clean `main`, though the earlier baseline test was meant to
  rule that out and itself ran clean once — consistent with intermittency
  rather than a reliable branch-vs-main difference).

**Recommendation: pause further hardware attempts.** With the fault
demonstrated intermittent, no single hardware trial (clean or faulted) can
be trusted to characterize any configuration's safety — the isolation
testing's core assumption (fix a config, one trial tells you if it's safe)
no longer holds. Continuing to test-and-guess on real hardware right now
has an unknown, unbounded cost. Not proposing another hardware attempt;
this is the user's call, and probably needs a different approach next
(e.g. physical inspection of cables/connectors, checking for a pattern
across the whole session's timeline rather than per-config, or trying the
same config many more times than is safe to determine an actual failure
rate) rather than another single-trial isolation attempt.

**Not done:** the retirement fix (`90b98b6`) is not reverted — it remains
a reasonable change on its own merits (matches every previously-clean
config, doesn't reintroduce the SE's original code path change), but it is
no longer credited as "the fix" for the fault, since the fault reproduced
under its exact configuration. `ab-testing-guide.md`'s "RESULT" section
needs a correction pass to stop presenting this as resolved.

---

## 2026-08-30 — FEED_STEPS also faulted: fourth real fault this session, isolation-by-flag method has run its course

At user's direction, tested the one remaining untested flag anyway despite
the broken correlation above. `POF_GL128_V2_FIX_FEED_STEPS=1` (single
positioning-feed constant, 13128→13486, no register-count or data-path
change) — same 1200dpi Prescan test. **Same bad noise. Fourth real fault
this session, sixth total across the project.** User confirmed powered off
and safe.

**Tally of every configuration tested this session, in order:**

| Config | Result |
|---|---|
| `FIX_LINCNT` alone | Clean |
| `FIX_DEPTH_A` alone | Clean |
| Baseline (all off), 1st trial | Clean |
| `FIX_FEED_PROBE` alone | **Fault** |
| `FIX_FEED_PROBE` + debounce fix | **Fault** |
| Baseline (all off) / retirement fix, 2nd trial | **Fault** |
| `FIX_FEED_STEPS` alone | **Fault** |

**Every tested config has now faulted at least once, except LINCNT-alone
and DEPTH_A-alone — each of which only got a single trial.** Combined with
baseline faulting on its second trial (identical config to its own first,
clean trial), there is no remaining basis to believe any of this session's
5 flags, individually or as a group, is a reliable predictor of the fault.
**The one-flag-at-a-time hardware isolation method has run its course for
this project.** Further single-trial tests of individual flags are very
unlikely to add diagnostic value — the pattern across 7 trials no longer
distinguishes "flag on" from "flag off" from "clean main-equivalent
baseline."

**Explicit recommendation for the user:** stop hardware isolation testing
via this method. If continuing to investigate at all, this now needs a
fundamentally different approach — most plausibly a physical/mechanical
check (cable seating, connector wear, power supply, whether faults cluster
later in a session of repeated cycles vs. early) rather than more code
changes tried one at a time on real hardware. This is the user's call; not
proposing another hardware attempt from here without a different plan than
"try the next flag."

**Fault tally, all of 2026-08-30:** original incident had 2 USB pipe-error
attempts plus 1 GUI-driven mechanical fault (attempt 3), repeated once more
on the merged branch (attempt 5) — 3 mechanical-sound faults there. This
isolation-testing session adds 4 more mechanical-sound faults (FEED_PROBE,
FEED_PROBE+debounce, baseline retest, FEED_STEPS) — 7 mechanical-sound
faults total today, plus the 2 earlier pipe-error/device-drop failures. No
injuries reported at any point; scanner powered off safely every time.

---

## 2026-08-30 — v1.3.0 confirmation run: CLEAN (both Prescan and Scan). Real code-diff investigation finds a promising, previously-unexamined lead.

At user's request, ran a genuine, unmodified checkout of tag `v1.3.0` (a
real released version, not this branch with flags off) in the pre-existing
isolated worktree `../pyopticfilm-clean-main`, with its own `.venv` synced
fresh (`uv sync --group lab`) and confirmed importing from the checkout,
not the branch (`feed_probe_index`/`image_depth_a` don't even exist as
attributes there). Mock suite: 209 passed, 1 skipped.

**Real hardware: both Prescan and a full Scan ran fine, no fault.** This is
the first fully clean confirmation on real hardware today, on a config
none of this session's env-var flags can even apply to (they don't exist
in this checkout).

**User asked the right follow-up question: was "all flags off on this
branch" ever actually equivalent to a real clean baseline, or just to
"whatever this branch's base commit is"?** Checked directly:
`git diff --stat v1.3.0..HEAD -- src/` shows **971 insertions across 14
files** — image_lincnt handling aside, this includes `pass_align.py`,
`scan/calibrate.py`, `scan/exposure_override.py`, `scan/geometry.py`,
`scan/pipeline.py`, `scan/session.py`, `scanner.py`, and substantial
changes to `asic/gl128.py` well beyond this session's 5 flags. This
branch's "baseline" (flags off) was only ever proven equivalent to
*this session's starting `main`* (`8b6e014`, tag `v1.3.1`), never to
`v1.3.0` specifically -- and `8b6e014` sits 17 commits ahead of `v1.3.0`.

**Two of those 17 commits are a strong new lead, unrelated to anything
tested today:** `5a0efb0` and `7404739` ("Fix GL128 AFE strip timeout /
0xcd hang on consecutive scans"), merged into upstream `main` on
2026-08-29 -- one day before this session started, inherited into this
branch at its very first rebase (so present for the *entire* session,
including the original incident). They add:
- `_reset_stationary_scan_engine()` -- unconditionally writes
  `REG_START=0x00`, `REG_0x01=_CANCEL_REG01`, `REG_0x02=0x00`, then polls
  up to `_STATIONARY_IDLE_WAIT_S=2.0`s (0.02s interval), re-writing
  `REG_0x01`/`REG_0x02` on every iteration that still shows SCAN/motor bits
  set, until the `0x101` status register confirms idle-at-home.
- `_wait_idle_at_home_for_stationary()` -- the "two consecutive idle
  samples" debounce pattern this session's own `_wait_feed_probe_done` fix
  (`57ef166`) was modeled on. **This pattern is not battle-tested
  pre-existing code as assumed when writing that fix -- it was added by
  the same 1-day-old commit.**
- Both are called from `_setup_stationary_shading_strip()` /
  `_setup_afe_strip` -- i.e. during shading/AFE calibration, which runs at
  the start of every real scan and Prescan (before `_configure()`'s
  positioning feed, per `session_gl128.py` -- calibration happens first in
  `_acquire_pass`, then `acquire_raw` → `_configure` does the feed).

**Why this is a stronger lead than anything tested today:** this code is
not gated by any `POF_GL128_V2_FIX_*` flag -- it ran identically in every
one of this session's 7 hardware trials, clean and faulted alike. That is
consistent with the observed fault having no correlation with any of the
5 flags. It is absent from `v1.3.0`, which just ran clean. It directly
manipulates the motor-power register (`REG_0x02`) with retried, unconditional
writes in a code path that fires on every single scan attempt, including
every attempt made today.

**Not yet established:** whether this code path can actually coincide with
an in-flight motor operation from a *previous* feed/park in a way that cuts
power mid-motion (the mechanism that would explain a "thunk"), or whether
the issue is something else about this new code entirely (e.g. the retry
loop's repeated register writes themselves causing unexpected ASIC
behavior on real hardware vs. the mock transport tests exercise). This is
static-diff-based reasoning so far, not yet traced through the actual call
chain in enough depth to claim a mechanism, and not tested on hardware in
isolation from the rest of this branch's code.

**Recommendation:** this is the next thing to actually investigate --
likely by tracing the real call order (calibration → feed → image) in
detail and/or by testing a checkout of `8b6e014` *minus* just commits
`5a0efb0`/`7404739` (isolating this pair specifically, the same way the 5
env-var flags isolated this session's own changes) rather than continuing
to treat this branch's 5 flags as the space of candidate causes. Not
proposing a hardware test yet -- this needs more code-level tracing first,
given today's track record of guessing wrong twice already (the debounce
fix, and the FEED_PROBE retirement).

---

## 2026-08-30 — Traced a specific mechanism, not just a correlation

Read the full call chain from park-after-scan through the next
Prescan/Scan's calibration start, at the user's request (code-only, no
hardware). Found a precise gap, not just "newer code exists":

1. **`wait_until_at_home()`** (line 1429, pre-existing, used to confirm the
   carriage reached home after a scan's `AGOHOME` park) accepts completion
   on a **single** `read_status_reliable()` sample showing `is_at_home and
   not is_motor_enabled` — **no cross-iteration debounce**. This is the
   exact same class of gap `57ef166` fixed today, in the wrong function
   (`_wait_feed_probe_done`, used for *forward* positioning feeds, not this
   *return-to-home* park wait). `wait_until_at_home` was never touched.
2. **`calibrate.py`'s `_ensure_at_home()`** (line 272) gates the next
   scan's calibration on `is_at_home()` alone — a single status read that
   checks *only* the HOME sensor bit, not whether the motor is still
   enabled. `home()` (gl128.py line 1712) does not wait or seek at all by
   design ("no standalone home seek... previously caused grinding when
   invented" — its own docstring) — it is an instant check-or-raise.
3. **`_reset_stationary_scan_engine()`** (yesterday's new code, called
   immediately after `_ensure_at_home()` passes, as part of every
   Prescan/Scan's shading/AFE calibration) **unconditionally writes
   `REG_0x02 = 0x00`** as its very first action, with no prior check of
   whether the motor is currently enabled.

**Mechanism:** if (1) accepted a premature/transient "park complete"
sample a moment before the carriage physically finished settling, (2)'s
gate — checking only the home sensor, not motor-enabled — waves the next
scan through anyway, and (3) then cuts motor power abruptly while the
carriage may still be in a residual settling motion. This is a plausible,
specific explanation for a "thunk"-type abrupt stop, distinct from (and
more precisely located than) anything tested via the 5 env-var flags
today. It also explains why the fault reproduced on the exact
"already-tested-clean" baseline config: this is a timing race, not a
fixed-state difference, so a clean trial proves nothing about the next
trial.

**Proposed fix (not yet applied, holding for user direction):**
1. Add the same 2-consecutive-sample debounce `_wait_idle_at_home_for_
   stationary()` already uses to `wait_until_at_home()`.
2. Make `_ensure_at_home()` (or `is_at_home()`'s use here specifically)
   also check `is_motor_enabled`, not just the home sensor bit, before
   treating the carriage as ready for the next stationary operation.
3. Consider having `_reset_stationary_scan_engine()` check motor-enabled
   state before its first unconditional `REG_0x02=0x00` write, rather than
   writing blind.

This is a more precise, code-derived hypothesis than either of today's
two prior guesses (both correlational, both disproven on hardware) — but
given the track record, not treating it as proven either. Next step is the
user's call: implement + hardware test, or investigate further first.

---

## 2026-08-30 — Implemented the traced fix, ready for a hardware attempt

At user's direction. Two changes, both in `gl128.py`:

1. **`wait_until_at_home()`** now requires two consecutive idle samples
   (`is_at_home and not is_motor_enabled`) before accepting park completion,
   matching `_wait_idle_at_home_for_stationary`'s existing debounce.
   Previously accepted a single sample.
2. **`_reset_stationary_scan_engine()`** now checks `is_motor_enabled`
   first; if the motor is still enabled, it waits (via the same debounced
   `_wait_idle_at_home_for_stationary`, bounded by the existing
   `_STATIONARY_IDLE_WAIT_S=2.0s`) before proceeding to its original
   unconditional `REG_0x02=0x00` write. If the motor is already idle
   (the common case), behavior is unchanged — no added delay.

Left everything else (`_start_stationary_strip`'s own `REG_0x02=0x00`
write, the AGOHOME-branch writes, error-path writes at lines 959/976) as
found -- those either run after this chokepoint already confirmed idle, or
are error/recovery paths outside today's traced mechanism. Not chasing
every `REG_0x02=0x00` site in the file, only the one on the traced
park→next-calibration critical path.

**Tests:** one existing test
(`test_acquire_afe_strip_waits_out_motor_busy_then_absolute_scan`) had a
fixed-length mocked status sequence that the new pre-check's extra reads
exhausted -- switched it to a self-extending sequence (same underlying
busy→idle→ready progression, just not brittle to exact call counts). Two
new tests: `test_wait_until_at_home_requires_two_consecutive_idle_samples`
(proves a reverting single sample is not accepted) and
`test_reset_stationary_scan_engine_waits_for_motor_idle_before_cutting_
power` (proves the first `REG_0x02` write doesn't fire until the debounced
pre-wait clears). Full suite: 262 passed, 1 skipped. Lint clean.

**Not committed to git yet in this entry -- see next commit.** Ready for a
hardware attempt whenever the user wants it; same safety protocol as every
prior attempt today. Given this is the third fix attempt today (after the
debounce-on-the-wrong-function attempt and the FEED_PROBE retirement, both
disproven), treating a clean result as encouraging but not conclusive --
and, per the day's central lesson, a single clean trial no longer counts
as proof either way if the fault is genuinely intermittent.

---

## 2026-08-30 — Traced fix (`b78f2b4`) also faulted. Third wrong guess today. Recommending a different method: capture the fault, don't guess at it.

Same terrible sound, same mechanical failure. User cut power immediately,
confirmed safe. Fifth real hardware fault this session, eighth total
across the project today.

**Three carefully-reasoned, code-traced fix attempts have now all failed
on real hardware:**
1. Debounce on `_wait_feed_probe_done` (`57ef166`) — wrong function.
2. Retiring `feed_probe_index=None` (`90b98b6`) — broken by the confirmation
   run faulting on an already-proven-clean config.
3. Debounce on `wait_until_at_home` + motor-idle guard on
   `_reset_stationary_scan_engine` (`b78f2b4`) — also faulted.

**Honest assessment: static code reading plus single-hardware-trial
testing has not worked today, three times in a row, on genuinely different
and individually plausible hypotheses.** Continuing to guess a fourth fix
this way is not a responsible use of further hardware attempts — each one
carries real risk to the scanner, and the hit rate so far is 0-for-3 on
reasoned hypotheses. Not proposing a fourth code guess.

**Recommendation instead: capture the actual fault on the wire, the same
way this whole project's Phase 1-4 methodology already works.** This
project has working tooling (`tools/capture_ledger.py`,
`tools/phase_segment.py`, `tools/register_program.py`) for turning a
USBPcap capture into an exact register-level timeline. Every fix attempt
today has been reasoning from source code and symptom description
("sounded like X, happened during Y") — not from an actual byte-for-byte
record of what the ASIC was told to do in the seconds before the fault.
Capturing one real failing run (USBPcap running during a deliberate
attempt, same physical-presence safety protocol as always) and running it
through the existing analysis tools would show the *actual* register
sequence and timing right before the sound — not a hypothesis about it.
This is more work upfront than another quick patch, but it's the
evidence-based version of what's been attempted three times as a guess.

**Not done:** no capture has been taken yet. This requires the user's
decision on whether to attempt one more hardware run specifically to
capture it (same risk as any other attempt, but this time the run's value
doesn't depend on being clean — a captured fault is exactly the useful
outcome). Not recommending further guess-and-check attempts without a
capture in hand.

---

## 2026-08-30 — CORRECTION: the "third fix attempt" test was contaminated by a leftover env var. Not actually disproven.

At user's prompting ("maybe this repo is contaminated"), checked for
leftover state rather than assuming today's tests were clean. Confirmed
repo (`git status` clean, HEAD matches expected, editable install resolves
to this checkout, not a stale one). **But the user's PowerShell session
still had two leftover env vars set:**

```
POF_GL128_V2_FIX_FEED_PROBE = 1
POF_GL128_V2_FIX_FEED_STEPS = 1
```

`FEED_PROBE` is harmless post-retirement (`90b98b6` removed the code path
that reads it — confirmed no-op). **`FEED_STEPS` is not harmless** — it is
still fully live in code (`feed_to_scan_steps`: 13128 → 13486).

**Reconstructing the session's env-var timeline: this assistant never
instructed clearing `FEED_STEPS` after the FEED_STEPS test, before moving
on to test the traced `wait_until_at_home`/`_reset_stationary_scan_engine`
fix (`b78f2b4`).** That means:

- The `FEED_STEPS`-alone test itself (its own PROGRESS.md entry) is
  unaffected and stands as reported — that was the intended config for
  that test.
- **The "third fix attempt" test (`b78f2b4`, reported as "also faulted")
  was NOT a clean test of that fix against default config.** It actually
  ran with `FEED_STEPS=1` still active on top of the new fix — an
  untested, never-intended combination. **The traced
  `wait_until_at_home`/`_reset_stationary_scan_engine` fix has not
  actually been disproven — it has not yet been properly tested at all.**

**This is an assistant process error, not a hardware or hypothesis
failure.** Every earlier flag-isolation instruction in this session
explicitly paired "set X" with "unset X" or a full baseline reset before
the next test — this discipline lapsed for the two most recent hardware
attempts (the `FEED_STEPS` test and the traced-fix test), and no
verification step (like the one just run) caught it until the user asked.

**Practical effect:** re-open the traced fix (`b78f2b4`) as untested,
not disproven. If a further hardware attempt happens, it needs the
env vars actually cleared and verified empty first — not just assumed
clear from memory of what was typed hours earlier.

**Process fix going forward:** before any further hardware attempt this
session, verify with `Get-ChildItem Env: | Where-Object { $_.Name -like
"POF_GL128*" }` returning nothing (or exactly the intended flag) — do not
rely on remembering what was set in a long-running shell.

---

## 2026-08-30 — Clean retest of the traced fix: ALSO faulted. Fourth reasoned hypothesis disproven. Stopping software-guess attempts for today.

User cleared and verified both leftover env vars empty, then re-ran the
same 1200dpi Prescan test with the traced `wait_until_at_home`/`_reset_
stationary_scan_engine` fix (`b78f2b4`) genuinely in isolation. **Same
failure, identical to before.** Powered off safely, confirmed.

This resolves the previous entry's ambiguity: the traced fix is now
**properly disproven**, not just contaminated-and-untested. Fourth
distinct, individually well-reasoned hardware attempt to fail today.

**Full, corrected tally (env-var-verified where noted):**

| Config | Result |
|---|---|
| `FIX_LINCNT` alone | Clean (1 trial) |
| `FIX_DEPTH_A` alone | Clean (1 trial) |
| Baseline, 1st trial | Clean |
| `FIX_FEED_PROBE` alone | Fault |
| `FIX_FEED_PROBE` + debounce fix (`57ef166`) | Fault |
| Baseline / retirement fix (`90b98b6`), 2nd trial | Fault |
| `FIX_FEED_STEPS` alone | Fault |
| Traced fix (`b78f2b4`), contaminated by leftover `FEED_STEPS` | Fault (invalid data point, superseded below) |
| Traced fix (`b78f2b4`), env-verified clean | **Fault** |

**Every multi-trial or verified-clean config has faulted. Only two
single-trial configs remain "clean," and given baseline's own repeat
faulted, neither should be trusted as proof of safety anymore** —
including `v1.3.0`'s single clean trial from earlier today.

**Assessment: four different, individually reasoned software hypotheses
have now failed against real hardware.** Not proposing a fifth code guess.
This increasingly looks less like a single findable timing bug in the
specific functions examined so far, and more likely either (a) something
environmental/physical — cable seating, connector wear, marginal power
delivery, thermal behavior over a long session of repeated cycles — or (b)
a software cause not yet located by the code paths checked today.

**Recommendation, unchanged from two entries ago and now stronger:** stop
guessing fixes from static code reading. Either (1) a physical/mechanical
inspection with no further scan attempts, or (2) capture an actual failing
run on the wire (USBPcap) and analyze it with this project's existing
tooling, is more likely to be productive than a fifth hypothesis. This is
the user's call — not proposing another hardware attempt without one of
those two changes in method.

---

## 2026-08-30 — First real USB capture of the fault: wrong phase entirely, register program looks clean

User captured a real failing run with USBPcap (Wireshark GUI, elevated),
1200dpi Prescan, saved as `C:\dev\morecapture\bad.pcapng` (5.6MB, 28s).
Ran through `tools/capture_ledger.py` (1689 events,
`docs/hw-ref/8100v2/ledgers/bad.events.json`).

**Finding: the fault occurred during active image bulk transfer, not
during positioning/feed.** The capture ends with ~2.5s of steady
`wIndex=0x8` image-bulk-announce preambles (51,840 bytes each, ~37ms
cadence, no visible gaps or anomalies), cut off by a single lamp-off
register write (`0x03=48`) — consistent with the power cut. **None of
today's four fix attempts (`_wait_feed_probe_done`, `wait_until_at_home`,
`_reset_stationary_scan_engine`, `feed_probe_index`) run during this
phase at all** — they govern positioning moves *between* scans, not the
continuous motor-driven acquisition itself. This is very likely why all
four failed: wrong phase of the operation entirely, contrary to every
verbal fault description collected today ("during feed/positioning,
before the image pass").

**Register program decoded from the writes immediately before this final
pass, all normal for a 1200dpi Prescan:** `DPISET=200` (×6=1200dpi),
`LINCNT=4836`, `STRPIXEL/ENDPIXEL=242/10610` (10,368-unit window),
`FEEDL=1`, `LPERIOD=11277` (matches
`MODEL_8100_V2.line_period_for(1200)`), `EXPOSURE=14000`. Nothing
anomalously large or miscalculated. USB bulk cadence during the fault
window was smooth and regular right up to the cutoff — no stall, error,
or backoff visible at the protocol level.

**Implication:** the cause is very unlikely to be a wrong simple-register
value (the class of thing every fix attempt today, and the original
session's F-007/009/012/013/014 corrections, all targeted). Leading new
candidate: the motor **slope-table content** (`SLOPE_TABLE_FAST`,
uploaded as an AHB bulk blob via `_upload_fast_slopes()`, governing
accel/decel ramp during exactly this continuous-motion image-acquisition
phase) has never been verified against real captures — it's inherited
from the SE model unmodified. This project's own working rule has been to
never decode bulk/AHB payload content (privacy/scope boundary from Phase
1 onward) — revisiting that scope for this one specific, safety-relevant
blob is the user's explicit direction for the next step. Alternative,
less tractable possibility: something with no USB-visible signature at
all (true mechanical/hardware behavior).

**Not yet done:** slope-table content not yet extracted or analyzed.

---

## 2026-08-30 — Slope-table content extracted and compared: real finding, cross-validated against two independent real captures

At user's direction, wrote a one-off extraction script (not added to
`tools/` — a targeted, deliberate exception to the "never decode bulk"
rule for this one small, safety-relevant 512-byte AHB table, not image
data) that finds the `wIndex=0x1` control-write announcing an
`AHB_SLOPE_SCAN`/`AHB_SLOPE_FAST` upload (`bulk_addr=0x1000c000` /
`0x10010000`, `size=512`) and reconstructs the following bulk-OUT payload
bytes from a real `.pcapng`.

**Also new this session: the user provided a second, independent capture
source** — `C:\dev\captured\8100-v2\04_color_7200.pcapng`, the original
V2 vendor-driver capture PR #30 cited and this project's Phase 0 had
previously found unrecoverable. (Separately, `C:\dev\morecapture`'s
software is now known to be a different third-party driver from that
original vendor capture — both remain referred to generically per the
project's standing "no software names" convention.)

**Extracted and compared against `SLOPE_TABLE_FAST`/`SLOPE_TABLE_SLOW`
(`tables_8200i_se.py`) across every file in `C:\dev\morecapture` with a
real scan, plus the newly-available `04_color_7200.pcapng`:**

- Every capture shows 2 AHB uploads (`AHB_SLOPE_SCAN` + `AHB_SLOPE_FAST`,
  same table to both) per feed, in feed pairs.
- **The first (reference) feed of every pair uploads `SLOPE_TABLE_FAST`
  — exact byte match.**
- **The second (final positioning) feed of every pair uploads
  `SLOPE_TABLE_SLOW` — exact byte match, not `SLOPE_TABLE_FAST`.**
- This alternating `FAST, FAST, SLOW, SLOW` (repeating per feed-pair)
  pattern is exact and consistent across all of `4/6/7/8/9/10/11.pcapng`,
  the multi-cycle `3.pcapng` (`FAST,FAST,SLOW,SLOW` × 6, once per cycle),
  and independently in `04_color_7200.pcapng` (`FAST,FAST,SLOW,SLOW,
  FAST,FAST` — a third feed-pair present in this capture also uses FAST,
  not yet investigated further).

**pyopticfilm's own code always uses `SLOPE_TABLE_FAST` for both feeds,
unconditionally.** `Gl128._upload_fast_slopes()` (called from
`_feed_capture()`, called from both of `position_for_full_frame_scan()`'s
feeds — `gl128.py` lines ~1671-1681) has no slow/fast distinction at all —
its own docstring even notes "Fast feeds still call `_upload_fast_slopes`
directly" as an explicit carve-out from the *other* slope-selection
mechanism (`_configure_tables()`'s `image_slope_slow`/`shading` params,
used only for shading/calibration, never for feeds).

**Assessment: this is real, cross-validated evidence, not a hypothesis
from static reading — the strongest lead of the day.** Two independently-
sourced real captures agree exactly on which table each feed should use;
pyopticfilm has never made this distinction. Using the aggressive FAST
ramp on the final positioning feed — the move immediately before image
acquisition starts, matching the capture's own fault timing from two
entries ago — is a plausible, well-evidenced mechanism for a motor
overspeed/hard-stop, distinct from (and better supported than) every
static-code hypothesis tried earlier today.

**Fix being implemented next (code only):** `_upload_fast_slopes()` gains
a `use_slow` parameter; `_feed_capture()` threads it through;
`position_for_full_frame_scan()` passes `use_slow_slope=True` only for
the second (`second`) feed, leaving the first (`first`, reference feed)
and every other caller (`feed()`, single standalone feeds) unchanged at
FAST — matching exactly what both captures show, no more, no less.

**Implemented and tested.** `Gl128._upload_fast_slopes(*, use_slow: bool =
False)`, `_feed_capture(..., use_slow_slope: bool = False)`, and
`position_for_full_frame_scan()`'s second `_feed_capture` call now passes
`use_slow_slope=True`. `feed()` (the general single-feed API) and the
first (reference) feed are unaffected — still `SLOPE_TABLE_FAST`, matching
both captures exactly.

**New tests** (`tests/test_gl128_slope_table.py`, 4 tests): direct
`_upload_fast_slopes()` default/slow byte-for-byte checks against the real
table constants; `_feed_capture()` threading; and a full end-to-end test
through `position_for_full_frame_scan()` on the mock-hardware transport,
tracking real `write_ahb()` calls and asserting the exact
`FAST, FAST, SLOW, SLOW` sequence both captures showed. Full suite: 266
passed, 1 skipped, lint clean.

**Not yet tested on hardware.** This is the strongest-evidenced fix of the
day (cross-validated against two independent real captures, not a
hypothesis), but given today's history, still holding for the user's
explicit go-ahead and the same physical-presence safety protocol before
any hardware attempt.

---

## 2026-08-30 — BREAKTHROUGH: full, clean 7200dpi scan, no fault, no corruption, in a fully isolated build

**Sequence of hardware tests that led here:**

1. Slope-table fix applied to the real branch (`02a0d4f`, includes the
   AFE-hang-fix pair, this session's other patches, and default priming
   on) — **faulted** (same as before the fix; slope fix alone was not
   sufficient on the real branch).
2. User's own idea: isolate the slope fix on top of unmodified `v1.3.0`
   (which predates the AFE-hang-fix pair entirely) — **1200dpi clean**
   (first fully clean hardware attempt of the entire day). 3600dpi showed
   a brief (3-5s) recoverable sound specifically during the Prescan's DPI
   change, correctly identified as almost certainly the pre-existing
   "AFE 0xcd hang on consecutive scans when changing DPI" issue that
   `v1.3.0` predates the fix for (not a new problem). Priming-skip env var
   added for this worktree only, purely additive; skip-priming (1200dpi)
   was also clean.
3. Tested the real branch (slope fix + AFE-hang-fix pair + this session's
   own debounce patches + priming disabled via the actual Scan Lab
   checkbox) — **faulted again.** This showed the AFE-hang-fix
   pair/this-morning's-patches area was still implicated independently of
   slope/priming.
4. **Fully isolated test**: new worktree `../pyopticfilm-afe-isolation`,
   `v1.3.0` + *only* `5a0efb0`/`7404739` (the AFE-hang-fix pair,
   cherry-picked cleanly except a trivial `CHANGELOG.md` conflict) + the
   same slope-table patch + the same priming-skip env var. Deliberately
   excludes this morning's debounce patches (`57ef166`, `b78f2b4`) and the
   feed-probe retirement (`2c66f4e`/`90b98b6`) — testing whether the
   AFE-hang-fix pair plus the slope fix alone, with priming skipped, is
   sufficient.

**Result: 1200dpi Prescan clean (sound, mechanical, image). 7200dpi scan
had the same known, pre-existing DPI-change sound during Prescan (see
step 2 — not a new issue) but then ran fully silent and mechanically
clean through the entire scan. The resulting image
(`C:\dev\morecapture\solvedcolor_short_7200dpi.tif`, 382MB) is
verified — 7206×10368×3 uint16, 0% near-black pixels, full frame content
visible with no cutoff, no vertical streaking, no corruption of any kind.**
This is the first time today a full 7200dpi scan has completed both
mechanically clean *and* with a correct image.

**What this isolates:** the combination of (a) the slope-table fix and
(b) priming skipped, on top of (c) the AFE-hang-fix pair alone (without
this morning's debounce patches or probe retirement), is sufficient for a
clean result. This does not yet prove which of (a)/(b) is doing the work,
or whether this morning's debounce patches (`57ef166`/`b78f2b4`) and the
probe retirement are unnecessary complexity now that the real root causes
appear to be elsewhere — that's not disentangled by this test alone.

**Also notable:** the image-corruption/mid-scan-stall bug from earlier
today (confirmed present even on the real branch's plain baseline, with
priming *on* and the old FAST-only slope table) did not reproduce here.
This raises real doubt about that bug being genuinely independent of
slope/priming as concluded earlier — it's possible priming (running with
the wrong slope table) or the wrong slope table itself was the actual
cause of the corruption too, not a separate issue as this session
initially concluded. Not proven either way by a single trial, but worth
revisiting rather than treating as settled.

**Not yet done:**
1. Consolidate this onto the actual working branch properly — the
   priming-skip mechanism used here is a throwaway env-var hack in an
   isolation worktree, not a real design decision. Options: make priming
   permanently skippable via the existing `gl128_prime=False`/Scan Lab
   checkbox (already real code on the working branch, just needs to
   default differently or stay opt-in), or leave priming on by default
   and revisit only if further testing shows it's still needed once the
   slope fix is the only variable.
2. Decide whether to keep or revert this morning's debounce patches
   (`57ef166` on `_wait_feed_probe_done`, `b78f2b4` on
   `wait_until_at_home`/`_reset_stationary_scan_engine`) and the
   feed-probe retirement (`2c66f4e`/`90b98b6`) — none of them were
   necessary for this clean result, though none are known to be harmful
   either.
3. More repeated trials before treating this as fully proven — today's
   pattern (intermittent faults, one clean baseline trial faulting on
   repeat) means a single clean 7200dpi run, however good, is encouraging
   but not the same as a proven-safe configuration.
4. Confirm whether the corruption bug is truly gone or just didn't occur
   on this one trial.

**Update — repeated on the same isolated build, per item 3 above:** user
ran 4 more trials on the identical `../pyopticfilm-afe-isolation` build:
1200dpi Prescan (clean), 1800dpi scan ×2 (both clean — mechanically silent,
correct images; the known pre-existing DPI-change prescan sound present
both times as expected, not a new issue), 7200dpi scan (clean, image
verified OK by the user). **5 clean full scans in a row across 3 DPIs, 0
faults, 0 corrupted images, on this exact build.** This is materially
stronger than any other configuration tested today — every other config
that ran clean at all did so on 1-3 trials before faulting on a repeat;
this one is 5-for-5. Confidence is now genuinely high that (AFE-hang-fix
pair + slope-table fix + priming skipped) is a real, working combination,
not a lucky streak — though "genuinely high" is not "certain," per the
day's own lesson.

**Next: consolidate onto the real working branch.** Plan, pending final
confirmation: apply the slope-table fix (already on the branch,
`02a0d4f`) together with a proper (non-hacky) priming-skip decision — not
the throwaway env var used for isolation testing. Open question for the
user: default priming OFF given repeated evidence it's unnecessary/
possibly harmful with the corrected slope table, or leave it opt-in via
the existing `gl128_prime`/Scan Lab checkbox and default ON pending a
dedicated "priming ON with the slope fix" trial (not yet run — every
clean trial so far also had priming off, so priming's own necessity now
that slope is fixed is not yet cleanly tested in isolation from slope
alone). Also pending: whether to keep or revert this morning's
now-unnecessary-but-not-harmful debounce patches (`57ef166`, `b78f2b4`)
and the feed-probe retirement (`2c66f4e`/`90b98b6`).

---

## 2026-08-30 — Consolidated onto the real working branch

Per user's direction: reverted the three now-unproven-necessary morning
patches (`57ef166` debounce on `_wait_feed_probe_done`, `b78f2b4` debounce
on `wait_until_at_home`/motor-guard on `_reset_stationary_scan_engine`,
`90b98b6` feed-probe retirement) via `git revert` (clean except trivial
`PROGRESS.md` conflicts, resolved by keeping this file's own history —
`4cdc7b8`, `a7c43cd`, `b1cbc3c`). `feed_probe_index` is back to its
pre-today flag-based state (default `0x21`, opt-in-only override to
`None` via `POF_GL128_V2_FIX_FEED_PROBE`, matching pre-this-session
behavior). None of these three were shown to fix anything on real
hardware; keeping the diff minimal to what's actually proven
(the AFE-hang-fix pair, already on the branch since before this session,
plus the slope-table fix).

**Priming default for V2, implemented properly (not the isolation
worktree's throwaway env var):** added `Model8200iSE.default_gl128_prime:
bool = True` (unchanged for SE) and overrode it to `False` on
`Model8100V2`, with an explanatory comment noting this is precautionary
(matches what's actually been tested clean, not a claim that priming
itself is dangerous — priming-ON-with-the-slope-fix hasn't been separately
tested). `Scanner.scan()`'s `gl128_prime` parameter changed from
`bool = True` to `bool | None = None`; when left unset it now resolves via
`model.default_gl128_prime` instead of a hardcoded `True`. Explicit
`gl128_prime=True`/`False` (including Scan Lab's existing checkbox, which
always passes an explicit bool) still fully overrides the default —
no change to Scan Lab's behavior itself, only to what happens when a
caller doesn't specify.

**New tests** (`tests/test_mock_scan.py`): V2 defaults to no priming when
`gl128_prime` is left unset (only the requested scan runs, `status ==
["prime_skipped", "scanning"]`); explicit `gl128_prime=True` still primes
on V2 despite the new default. Full suite: 265 passed, 1 skipped, lint
clean.

**Current state of the real working branch:** `v1.3.0` base + the
AFE-hang-fix pair + this session's earlier F-007/012/013/014-derived
per-fix isolation flags (all still default-off, unchanged from the
original incident response) + the slope-table fix (`02a0d4f`) + V2's new
default-off priming. This is the configuration to test on hardware next —
not yet done; the isolated worktree (`../pyopticfilm-afe-isolation`) is
what's actually been hardware-verified 5-for-5 so far, and it differs
from this consolidated branch state in one respect the isolated worktree
never carried: the per-fix isolation flags module machinery in
`model_8100_v2.py` (inert when unset, but present). Recommend one more
hardware confirmation on this actual branch before treating it as final.

**Confirmed.** User ran the consolidated real branch (`ac04ddc`, no env
vars, no checkboxes — priming off is now V2's actual default) through the
same test sequence. Result: clean — "all looks and works great," a small
sound during Prescan before the scan starts (consistent with the
already-understood, benign, hardware/DPI-change-adjacent sound noted in
earlier trials, not the mechanical fault), and the scan itself described
as "more silent than reference software." This is the first time the
*actual working branch* (not an isolation worktree) has been confirmed
clean, closing the loop from this morning's incident to today's fix.

**Summary of the day, root causes found:**
1. **Primary fix**: `Gl128._upload_fast_slopes()` always used the
   aggressive `SLOPE_TABLE_FAST` motor ramp for both feeds in
   `position_for_full_frame_scan()`; two independent real captures (this
   project's second capture session, and the original vendor capture
   `04_color_7200.pcapng`) show the second (final positioning) feed should
   use the gentler `SLOPE_TABLE_SLOW` instead — cross-validated exact byte
   match, not a guess. This appears to be the actual root cause of the
   original mechanical-fault incident. Applied in `02a0d4f`.
2. **Contributing/precautionary**: `Model8100V2.default_gl128_prime`
   changed to `False` — priming was tested off in every clean trial;
   whether priming itself was ever truly hazardous (vs. just interacting
   with the now-fixed slope table) was not cleanly separated, so this is
   documented as precautionary, not proven necessary on its own.
3. **Not the cause, reverted**: this morning's `_wait_feed_probe_done` /
   `wait_until_at_home` debounce patches and the `feed_probe_index`
   retirement — all individually disproven or superseded once the slope
   table was found; kept the branch minimal rather than carrying unproven
   complexity forward.
4. **Separately, not the cause of the mechanical fault but real**: the
   image-corruption/mid-scan-black-cutoff bug from earlier today did not
   reproduce in any of the slope-fix trials, casting doubt on this
   session's earlier conclusion that it was fully independent of
   slope/priming — worth revisiting in a future session rather than
   treating as settled.

**What's not done / open for a future session:**
- Whether priming is still needed at all now that the slope table is
  correct (never isolated from the slope fix on its own).
- The pre-existing image-corruption bug's actual root cause (not
  re-investigated today beyond noting it didn't reproduce here).
- Whether the 3-5s benign Prescan/DPI-change sound is fully explained by
  the pre-v1.3.0-fix issue theory or worth a closer look.
- Posting updated findings to issues #33/#35 and updating PR #10's
  description — not done this session, user's call on timing.
- `docs/hardware-reference-8100v2.md` and `scanner-validation.md` were not
  updated with today's findings — still reflect the pre-incident state.

**Done, same session:**
- `docs/hardware-reference-8100v2.md` and `scanner-validation.md` updated
  with a "Motor slope tables" section and revised change logs (`b9a391a`).
- Issue #33 (positioning jitter/spike): corrected an earlier posted
  comment that wrongly claimed feed-ramp table selection was
  "unremarkable" (now known false — see the slope-table finding above).
  Initially misapplied a native-resolution/oversampling closing theory to
  #33 by mistake — reopened it and deleted that comment once caught.
  **#33 remains open**, tracking the real, still-unexplained positioning
  jitter and rare 150-220px spike.
- Issue #35 (7200dpi jaggedness): closed, reclassified as expected
  behavior — the scanner's native optical resolution is roughly half of
  7200dpi, and the jagged/stepped-line symptom is consistent with that
  being visible only due to oversampling, not a register/timing bug.
  LPERIOD/dummy candidates from earlier in that thread were not
  hardware-tested for this specific symptom before closing; multi-sample
  antialiasing suggested as a separate future enhancement if pursued.
- Upstream `jboneng/pyopticfilm#39`/`#40` (the slope-table hotfix) remain
  open, awaiting the repo owner's review.

---

## 2026-08-30 — Retest session resumes, on Linux hardware: `FIX_FEED_STEPS` clean

Continuing the paused retest of the 5 original `POF_GL128_V2_FIX_*` flags
now that the slope-table fix is confirmed stable (6+ consecutive clean
trials) — earlier fault/clean results for these flags predate the fix and
are considered unreliable (the platform was intermittently faulting from
the slope-table bug during those tests).

**Environment change: this retest runs on Linux, not the Windows machine
used for the rest of this file.** Repo re-cloned fresh at
`/home/tobby/claude/pyopticfilm` (the prior Windows-path checkouts
referenced throughout this file no longer exist on this machine); `uv sync
--group lab` set up a fresh `.venv`; confirmed editable install resolves to
this checkout and `POF_GL128_V2_FIX_*`/`POF_GL128_V2_LPERIOD_7200` env is
clean before the first attempt. Scanner enumerates as `07b3:1824` (Plustek
Film Scanner A1D) with a pre-existing per-user ACL grant, no permission
issues. Branch unchanged: `hw-ref/8100v2-capture-analysis` at `8b63169`
(the slope-table fix, scoped to V2 only).

**`POF_GL128_V2_FIX_FEED_STEPS=1` alone, via Scan Lab, 1200dpi, no-prime,
two consecutive scans:** mechanically clean, no fault. Images
(`/home/tobby/claude/scans/1/{1,2}.noprime1200dpi.tif`, 1201×1712×3
uint16) compared for issue #33's positioning-shift symptom: phase
correlation finds essentially zero shift (dx=-0.001px, dy=0.74px, sub-pixel
at 1200dpi), and pixel correlation peaks cleanly at zero y-offset
(r=0.9979, degrading symmetrically at ±1/±2px) — no sign of a gross
misalignment. Only 2 trials, so this doesn't rule out the rare
intermittent large-spike symptom [[pyopticfilm-priming-benchmark]]
describes, just doesn't surface it here.

**Attribution caution, raised by the user then corrected:** the clean
alignment is much more likely explained by the slope-table fix already
baseline on this branch (`use_slow_final_positioning_feed=True`, active in
every trial regardless of which `FIX_*` flag is under test) than by
`FEED_STEPS` itself, which only changes a feed *distance* target
(13128→13486), not ramp speed — it has no mechanism that should affect
positioning repeatability. Don't credit FEED_STEPS for jitter improvement
without a dedicated same-config repeat comparison.

**"Bug T67"**: user-assigned name for the recurring 3-6s stronger-hum sound
heard just before Prescan/Scan starts, unaffected by priming, present
across every hardware trial today regardless of which `FIX_*` flag is set.
Not new — matches the pre-existing, already-documented benign
Prescan/DPI-change sound (attributed to the pre-`v1.3.0`-fix "AFE 0xcd hang
on consecutive scans" behavior area, see the "BREAKTHROUGH" entry above) —
distinct from the mechanical fault this whole incident is about. User plans
to address it in a future session; use "Bug T67" to refer to it going
forward instead of re-describing the symptom each time.

**Result: `FIX_FEED_STEPS` cleared** — mechanically clean, image correct,
no positioning-shift signal in this pair. Next: `FIX_FEED_PROBE`.

---

## 2026-08-30 — Retest paused; pivoted to issue #33 (positioning jitter) at user's request

User asked to finish characterizing issue #33 before continuing the
`FIX_*` isolation retest, to see whether today's slope-table fix also
resolves the jitter, so priming-off and issue #33's closure could be
decided together. `FIX_FEED_PROBE`/`FIX_DUMMY`/`FIX_LINCNT`/`FIX_DEPTH_A`
remain untested since the FEED_STEPS entry above — not abandoned, just
deferred.

**"Bug T67" (the 3-6s hum) — user's own follow-up theory, not yet
checked:** possibly priming or a failed/retried pre-scan, based on its
duration. Flagged as a lead for a future session, not investigated further
now (priming is off by default in these very scans via
`default_gl128_prime=False`, so if T67 is priming-related it isn't classic
single-priming-pass behavior — worth double-checking `gl128_prime`'s
actual resolved value during a T67 occurrence before assuming either way).

**Ran a repeat-scan drift test, modeled on [[pyopticfilm-priming-benchmark]]'s
original methodology** (small crop, phase-correlate consecutive scans) to
get a real number under today's actual fix, since `tools/scan_sweep.py`
(the tool that memory's benchmark used) lives only on an unrelated,
incompatible branch (`origin/feat/gl128-benchmark-tooling`, diverged
`gl128.py` internals) not safe to pull into this branch. Used
`tools/hw_ab_capture.py` in a loop instead — one real scan per invocation,
fresh `Scanner.open()` each time, same crop as the old benchmark's default
(`0.45,0.45,0.55,0.55`), 7200dpi, priming off (today's actual default, no
flag needed). Installed `pillow` into `.venv` to satisfy this tool's
undeclared dependency (not in the `lab` group in `pyproject.toml` — a real
gap, not fixed this session, flagged for later). User ran 4 of a planned 6
scans, one at a time, watching for the mechanical fault between each —
all 4 mechanically clean, only "Bug T67" heard each time, no fault. Output:
`/home/tobby/claude/scans/33-jitter/scan{1..4}.{npy,png}`
(677×940×3 crop at 7200dpi).

**Phase-correlation drift across the 3 consecutive pairs:**

| pair | dx | dy | magnitude | corr response |
|---|---|---|---|---|
| 1→2 | 0.04px | -9.68px | 9.68px | 0.939 |
| 2→3 | 0.03px | -8.85px | 8.85px | 0.936 |
| 3→4 | -0.01px | 14.83px | 14.83px | 0.938 |

**mean shift 11.12px, max shift 14.83px** — all still essentially pure
Y-axis (dx ≈ 0.00-0.04px throughout, matching every prior measurement of
this jitter's axis). Strong, consistent phase-correlation response (~0.94)
each time, not noise.

**Honest read, not a clean "solved":**
- **No catastrophic (>100px) spike in this run** — a real, good sign
  against the old fast-ramp baseline (45.22px mean / 191.25px max, prime
  off, drain off, 7200dpi, pre-slope-fix). But only 3 pairs is far too few
  to rule out an event [[pyopticfilm-priming-benchmark]] itself
  characterized as occurring roughly 1 session in 4-5 — this run doesn't
  clear that question either way, it just doesn't happen to show one here.
- **Ordinary jitter (9-15px) has NOT visibly improved** — it sits
  comfortably inside the range the original benchmark already measured at
  7200dpi with priming off (mean 6.32-11.92px depending on quiet-drain, in
  the 96-scan sweep) and is well above the best-case ~2.35px "ordinary
  baseline" that same benchmark found excluding each config's worst pair.
  Today's fix targeted the mechanical-fault mechanism (aggressive ramp on
  the *final* feed specifically); it was never a targeted fix for this
  jitter's own root cause, which the original issue #33 write-up already
  concluded is NOT feed-ramp-speed-dependent (a bigger, dedicated 32-scan
  test disproved that). No reason from this data to expect it would move —
  and it apparently hasn't.
- **n=4 scans (3 pairs), single condition, single session — same
  small-sample caveat that applies to every hardware measurement today.**
  Not a substitute for a larger repeat set if a real answer is wanted.

**Recommendation: do not close issue #33 based on this data.** The
mechanical fault and the positioning-jitter issue remain two genuinely
separate problems (as this file already established earlier today) — the
slope-table fix solved the first, and this run gives no evidence it
touched the second. Priming is already off by default on `Model8100V2`
(`default_gl128_prime=False`, applied earlier today, orthogonal reasoning
from the mechanical-fault side) — that part of the user's proposed
action is effectively already done regardless of the jitter question, and
doesn't need a separate decision.

**Not done:** no PROGRESS.md-worthy conclusion on Bug T67's cause; scans
5-6 of the planned 6 (user stopped at 4, "enough test"); posting anything
to issue #33 (not recommended yet, given the above); resuming the
`FIX_FEED_PROBE`/`DUMMY`/`LINCNT`/`DEPTH_A` retest.

---

## 2026-08-30 — Bug T67 confirmed unrelated to priming (code, not just inference); upstream priming-default PR opened

**T67 ruled out as priming, by code, not just correlation.** Read
`Scanner.scan()`'s branch at `scanner.py:340-350`: when `gl128_prime`
resolves `False` (V2's default, active in every trial today), execution
takes the `on_status("prime_skipped")` path and returns — it never calls
`prime_session.run(...)` (only reachable from the `else` branch below,
lines 351-381). So in every trial where T67 was heard, the priming pass
did not merely have no effect — it never executed at all: no session, no
feed, no USB traffic. T67 cannot be the priming pass. Per the user's
request, not investigating further right now; likely candidate remains
the AFE/DPI-change calibration path noted earlier in this file.

**Reviewed what's actually upstream-ready in PR #10** (33 commits) at the
user's request:
- The slope-table fix itself is already split out and open upstream
  (`jboneng/pyopticfilm#39` issue, `#40` PR) — confirmed `#40`'s diff is
  scoped exactly to that fix, nothing else riding along.
- `default_gl128_prime` (V2 off, SE unchanged) was **not** yet upstream —
  bundled into this branch's `ac04ddc` alongside unrelated revert
  commits. Isolated it cleanly (just the priming logic + its 2 new tests,
  no incident-log docs, no isolation-flag scaffolding) onto a fresh branch
  off `upstream/main` (`8b6e014`, matching what `#40` is based on), rather
  than cherry-picking `ac04ddc` directly.
- The 5 `POF_GL128_V2_FIX_*` isolation flags, the 2 experimental
  overrides, and all of `docs/hw-ref/8100v2/*`/the capture-analysis
  tooling remain fork-only by design — diagnostic scaffolding and this
  project's own investigation trail, not upstream candidates as-is.

**Opened `jboneng/pyopticfilm#41`** (branch `fix/v2-priming-default`,
pushed to `origin`/`TobbyTravel/pyopticfilm` first, matching this
project's fork-first-then-upstream convention same as `#40`). Diff:
`Model8200iSE.default_gl128_prime: bool = True` (new field, unchanged SE
behavior) + `Model8100V2.default_gl128_prime: bool = False` (override) +
`Scanner.scan()`'s `gl128_prime` param widened to `bool | None = None`,
resolving through the model default only when the caller doesn't specify
— explicit `True`/`False` (including any host's own UI toggle) is
unaffected. Two new `test_mock_scan.py` tests (V2 skips priming by
default; explicit `gl128_prime=True` still overrides it). 251 passed, 1
skipped; lint clean.

**PR framing, at the user's explicit direction:** does not claim priming
is harmful or ineffective on V2 — that's unconfirmed (priming ON has
never been separately tested with the slope fix in place). The honest
case is narrower: every hardware-clean trial today had priming off, it
isn't required for the mechanical-fault fix to work, and it costs real
time/noise per session. Also added, at the user's request: a doc comment
on `Model8200iSE.default_gl128_prime` noting GL128 priming's original
~30-46px positioning-benefit rationale was measured specifically on 8100
V2 hardware (see the Aug 25 commit `c6c57e9` and `scanner-validation.md`'s
"Measured on the OpticFilm 8100 V2" line) and only extended to the SE by
inheritance (shared `Gl128` code), never independently confirmed on SE
hardware — flagged as worth its own future re-evaluation/benchmark, not
acted on for SE in this PR. SE's default is unchanged (`True`).

**Not done:** no SE hardware exists in this project to actually run that
re-evaluation; `#41`'s test-plan checkbox for a dedicated V2 hardware
confirmation run is left unchecked (informally covered by today's #40
testing, but no dedicated priming-on/off A/B under the fixed slope table
has been run) — noted honestly in the PR body rather than checked off.

---

## 2026-08-30 — PR #10 description synced; `FIX_FEED_PROBE` retest under the fixed platform: FAULT AGAIN

Updated PR #10's GitHub description (stale since before the whole
incident/fix/priming-PR story) to reflect current state: `#40`/`#41` split
out and open upstream, `FIX_FEED_STEPS` cleared, `#33` open/`#35` closed,
remaining flags paused. Confirmed no upstream drift first — `origin/main`
== `upstream/main` == `8b6e014`, this branch's merge-base with both is
exactly that commit, no rebase needed.

**Clarified a mix-up before resuming hardware testing:** the user asked
whether `FIX_FEED_PROBE`'s original fault was the one invalidated by the
leftover-env-var contamination. It was not — the contamination affected a
*different* test (the traced `wait_until_at_home` debounce fix, `b78f2b4`).
`FIX_FEED_PROBE`'s own individual fault test was clean at the time; what
actually broke its correlation was a later confirmation run (FEED_PROBE
always on, no flags — the empirically-"clean" baseline) faulting on
repeat. `#40`'s validity doesn't depend on FEED_PROBE's status either way
— it was found via a real fault capture and confirmed independently on
hardware, not by process of elimination through these 5 flags.

**User proposed testing all 4 remaining flags (`FEED_PROBE`, `DUMMY`,
`LINCNT`, `DEPTH_A`) plus `FEED_STEPS` together at once ("feeling
lucky").** Pushed back explicitly: this is the exact scenario that caused
the original incident (all-at-once testing), `FIX_FEED_PROBE` is the one
flag with an actual fault history, and `DUMMY`/`DEPTH_A` each have their
own separate unresolved image-corruption bug that combining could
worsen. User agreed to continue one-flag-at-a-time instead.

**`POF_GL128_V2_FIX_FEED_PROBE=1` alone, via Scan Lab, 1200dpi Prescan
(same test as its original fault), env verified clean before setting:**
**mechanical fault, real, hard.** User cut power immediately, confirmed
safe, no reported injury or damage. This is the third distinct occasion
`FIX_FEED_PROBE` has produced this fault (original isolation round: alone,
and alone+debounce fix) — now confirmed to recur under the fixed slope
table too, so this is not an artifact of the old slope-table bug.

**Practical effect:** `FIX_FEED_PROBE` stays "confirmed hazardous, do not
enable" — if anything, more confirmed than before, since this is the
first time it's been tested with the mechanical-fault root cause already
fixed and it *still* faulted. Given the day's broader lesson that the
fault can be intermittent and configs can go either way on repeat, this
alone doesn't cleanly prove FEED_PROBE causes it deterministically (same
caveat as the original round) — but there is no reason left to treat it
as a false positive from the old bug, since that bug is now fixed and
confirmed absent in every other trial today.

**Recommendation: do not test `FIX_FEED_PROBE` again without a specific
new plan** (e.g. a real fault capture of *this* occurrence, the same
method that found the slope-table root cause, rather than another guess).
`DUMMY`/`LINCNT`/`DEPTH_A` retest is a separate question — not
automatically implicated by this result, but pausing all remaining
hardware testing pending the user's direction after a real fault.

---

## 2026-08-30 — Code investigation of `FEED_PROBE`'s history, then retired from the codebase

User asked whether `0x21`/`FEED_PROBE` is itself a captured value and
what its context is, and to look for anything special about it (code
investigation only, no hardware) rather than immediately retesting.

**Findings:**
- `_FEED_PROBE_INDEX=0x21` / `_FEED_PROBE_DONE=0x04` (`gl128.py`) trace to
  an SE-only capture (session 03) — never observed in any V2 capture,
  this project's 10 files or the recovered original vendor capture. F-014
  already established the real V2 driver never queries `wIndex=0x21` at
  all.
- The real V2 driver instead polls `wIndex=0x20` (constant 0x55) and
  `wIndex=0x18` (2 or 18) — but not feed-specific: `findings.md` F-014
  shows these run continuously from device-open onward, 85-1060 times per
  capture, reading "more like a generic engine-busy/heartbeat check" than
  a completion signal. This is a steady background rate of vendor-request
  traffic to the ASIC for the whole session that pyopticfilm has no
  equivalent of anywhere else.
- **Re-tallied every real-hardware trial today by probe state, separating
  pre- and post-slope-fix** (the earlier "correlation is broken, must be
  intermittent" conclusion was itself reached entirely on pre-fix data,
  confounded by the still-active slope-table bug at the time):
  - Probe active, post-slope-fix only: 5/5 clean (FEED_STEPS-retest +
    4 jitter-test scans).
  - Probe disabled (`FIX_FEED_PROBE=1`), every trial ever run, pre- and
    post-fix: 3/3 fault, zero clean.
  - The pre-fix "broken correlation" data points (baseline/retirement
    retest faulting, FEED_STEPS-old faulting) both happened *before* the
    slope-table fix was found — i.e. during the period the platform was
    independently, intermittently faulting from a different, since-fixed
    bug. Once that confound is set aside, the FEED_PROBE-disabled signal
    is actually clean and strong: it has never once tested safe, in three
    tries, across the whole project.

**Hypothesis presented to the user (not proven mechanistically — would
need a real fault capture of this specific failure to confirm):**
disabling the `0x21` poll may remove the only steady background
vendor-request traffic pyopticfilm ever sends to the ASIC during a feed,
even though `0x21`'s own *response* is meaningless for V2 — possibly
mirroring the real driver's continuous `0x20`/`0x18` heartbeat-style
traffic in effect, by accident, via a completely different mechanism.

**At the user's direction: retired `POF_GL128_V2_FIX_FEED_PROBE` from the
codebase entirely**, rather than leaving it as a confirmed-hazardous
opt-in someone could still flip on:
- `model_8100_v2.py`: removed the `feed_probe_index` field override and
  its `_fix_enabled(...)` branch — `Model8100V2` no longer defines this
  field at all, so it unconditionally inherits `Model8200iSE.
  feed_probe_index=0x21`. Module docstring's flag list updated to mark
  the entry retired rather than describing a live flag.
- `gl128.py`: updated the (already-stale — it referenced a since-reverted
  V2-defaults-to-None state) comment near `_FEED_PROBE_INDEX` with the
  retirement history and the keep-alive hypothesis, plus an explicit
  "don't reintroduce without a fault capture" warning.
- `tests/test_gl128_feed_probe.py`: rewritten — dropped the old
  flag-enables-disable test, added a regression test proving the retired
  env var is now a no-op (still queries `0x21`).
- `tests/test_multi_model.py`: dropped `FEED_PROBE` from the per-fix
  isolation-flags case dict (the field no longer exists to isolate); added
  a dedicated no-op regression test.
- Docs updated to match: `ab-testing-guide.md` (new update banner,
  isolation-flags table row struck through), `hardware-reference-8100v2.md`
  (unknowns item 5 rewritten from "reverted to opt-in" to "confirmed
  hazardous, retired"; change-log gains a dedicated `FEED_PROBE` entry
  separate from the other four flags), `scanner-validation.md` (same
  correction, flag list split into "four surviving" vs. "retired").

**Tests:** 267 passed, 1 skipped; `ruff check` clean.

**Not done:** no real fault capture of this specific failure (the
methodologically stronger next step, per the user's own earlier framing,
if this is ever revisited) — retirement here is a code-safety measure
given the evidence in hand, not a claim the mechanism is fully understood.
`DUMMY`/`LINCNT`/`DEPTH_A` retest remains paused, untouched by this.

Scan Lab process killed, `POF_GL128_V2_FIX_FEED_PROBE` cleared from the
shell. No further hardware testing this session without explicit
re-confirmation.

---

## 2026-08-30 — `FIX_DUMMY` tested alone: mechanically clean, but the
## pre-existing corruption bug reproduces at 1200dpi for the first time

User physically present, env verified clean before setting.
`POF_GL128_V2_FIX_DUMMY=1` alone, via Scan Lab, 1200dpi Prescan (same test
pattern as the FEED_STEPS/FEED_PROBE retests).

**Mechanically clean** — user confirmed no unusual sound, only the
already-known "Bug T67" hum (present, as expected, unrelated to any
flag). `FIX_DUMMY` clears the mechanical-fault question, same as
FEED_STEPS.

**But the image is corrupted again** —
`/home/tobby/claude/scans/2/prescan_1200dpi.tif` (1201×1712×3 uint16,
96.6% near-black pixels). Visual inspection confirms the same signature
as every prior occurrence: a black frame with vertical, single-pixel-wide
color-channel streaks (red/green/faint white lines) — classic byte/line-
width misalignment. Column- and row-wise brightness profiles show no
clean structure (no obvious left/right or top/bottom split).

**Two things new about this occurrence, worth flagging:**
1. **First time seen at 1200dpi.** Every prior occurrence this project
   has recorded (`FIX_LINCNT`-alone, `FIX_DEPTH_A`-alone, and the
   all-flags-off baseline) was 7200dpi only — this DPI-specificity was an
   open assumption, now contradicted.
2. **No valid content strip.** Prior occurrences all had a real,
   correctly-decoded strip on the right ~20% of the frame; this one shows
   no clean region at all in the sampled profile.

**Revised read:** this is now the fourth different config (`LINCNT`,
`DEPTH_A`, baseline, and now `DUMMY`) to reproduce this exact corruption
signature. Combined with it no longer being DPI-specific, this looks
increasingly like a frequent, config/DPI-independent pyopticfilm bug
rather than something narrowly tied to any one flag, DPI, or the earlier
"7200dpi-only" framing. Root-causing this remains explicitly deferred
(per the standing "don't fix during isolation, report and decide next
steps" rule) but its apparent scope is now broader than previously
documented.

**`FIX_DUMMY` result: mechanically cleared.** Corruption bug reproduced
again, consistent with it being pre-existing and unrelated to this flag
specifically.

**Not done:** root-causing the corruption bug itself; retesting `LINCNT`
or `DEPTH_A` under the fixed platform (still untested since the original
pre-fix round); `FIX_FEED_PROBE` is retired, not part of the remaining
retest.

---

## 2026-08-30 — `FIX_LINCNT` alone: mechanically clean, but a cropped
## (not corrupted) image — traced to expected GL128 behavior, not a bug.
## Then `FIX_LINCNT`+`FIX_DEPTH_A` together (the actual captured
## combination) tested clean and correct.

**`POF_GL128_V2_FIX_LINCNT=1` alone**, 1800dpi (user's own choice of
resolution/mode this round, not the usual 1200dpi Prescan pattern),
mechanically clean (T67 only, as expected). Image
(`/home/tobby/claude/scans/3/color_short_1800dpi.tif`, 1801×2568×3)
**not corrupted this time — cropped**: real, correctly-colored content,
no streaking, but 1801 lines vs. `compute_geometry()`'s expected 1813 for
a full-frame 1800dpi scan (width 2568 = 2592−24 matches the normal,
expected `usb_end_drop`).

**User asked whether this was captured/validated, and at what DPI —
answered directly:** `image_lincnt_per_line=1`/`usb_image_lincnt_half_lines=
False` (F-012) is the *most* broadly captured-and-verified of the
original 4 fixes — exact byte-for-byte match across all 7 single-pass
files spanning 6 DPI points (600/1200/1800/2400/3600/7200×2). But every
one of those captures also carried the `DEPTH_A=0x04` mixed-pair register
(`FIX_DEPTH_A`) — LINCNT's formula was never captured or verified
independently of that register state, exactly the same caveat raised
when LINCNT-alone first corrupted at 7200dpi pre-fix.

**Traced the 12-line shortfall precisely (code only, no hardware):**
`compute_geometry()` at 1800dpi gives `shift_b=12` (the blue-channel CCD
row's physical offset, scaled to resolution) — exactly matching the
observed 1813→1801 shortfall. `pipeline.py`'s `apply_line_shifts()`
docstring documents this outright, pre-existing and unrelated to today's
session: *"Models that size LINCNT for the crop alone — GL128 OpticFilm,
which has no travel to spare — lose `max_shift` lines off the bottom
instead."* GL128 has no scan-time slack to absorb R/G/B row-offset
alignment the way GL845 does, so it trims instead. Under the old, wrong
×4 LINCNT convention this loss was masked inside a much larger
oversampled buffer; under the corrected ×1 convention (which matches how
the ASIC actually behaves, per F-012) it's simply visible. **Not a bug,
not new, not related to issue #35's jaggedness (different mechanism —
CCD row-offset alignment cost, not optical oversampling).**

**User speculated (explicitly framed as wild ideas) whether this reflects
a deliberate choice by the reference driver** — e.g. reading less for
speed, or a deeper/oversized scan specifically to avoid 7200dpi jagged
lines. Assessed and set aside: the loss is a fixed handful of lines
(single digits to low teens) regardless of scan length, not a meaningful
speed trade; and it's a CCD-alignment artifact unrelated to the optical-
resolution mechanism behind #35. No evidence points to intentional
behavior here beyond ordinary GL128 hardware geometry.

**`POF_GL128_V2_FIX_LINCNT=1` + `POF_GL128_V2_FIX_DEPTH_A=1` together**
— restoring the exact combination every source capture actually showed,
not a "combine and hope" test — same 1800dpi setup, mechanically clean
(T67 only). **Result: correct, no corruption — but the same benign crop
persisted** (user: "just a crop scan," re-verified directly against the
saved file afterward: 1801 lines vs. 1813 expected, an *identical* 12-line
shortfall to the LINCNT-alone trial, matching `shift_b=12` exactly).
**Correction to an earlier draft of this entry, which wrongly said "no
crop"** — the crop is present in every LINCNT-active trial today,
combination or not, because it's independent of both flags entirely (see
above: `compute_geometry()`'s `shift_b`/`lines` are byte-identical with or
without either flag). What the combination actually fixes is the
*corruption* seen when either flag ran alone — the crop itself is
unrelated, pre-existing, and unaffected either way. This is the first
real-hardware evidence that LINCNT's formula works as captured when
tested in its actual captured context (paired with DEPTH_A), consistent
with the standing "combination not captured" caveat being the
explanation for LINCNT-alone's corruption (not its crop) rather than
LINCNT's formula itself being wrong.

**Practical effect:** `FIX_LINCNT`-alone's crop is explained and benign
(pre-existing GL128 behavior, not caused by this session, unaffected by
either flag). `FIX_LINCNT`+`FIX_DEPTH_A` together now has one clean,
correct (crop aside) real-hardware trial — encouraging, but per the day's
own lesson, one trial is not proof; worth a repeat before trusting it
further. `DEPTH_A` alone (isolated from `LINCNT`) remains the one
still-genuinely-untested configuration from the original 4.

**Not done:** repeating the LINCNT+DEPTH_A combination for confidence;
testing `DEPTH_A` alone under the fixed platform; root-causing the
separate, still-open corruption/stall bug (unrelated to this entry —
did not reproduce in either of today's LINCNT trials).

---

## 2026-08-30 — DEPTH_A's capture context re-examined (code only); `FIX_LINCNT`+`FIX_DEPTH_A` repeated at 7200dpi — clean again

User asked for `DEPTH_A`'s own capture context before testing it alone
(code investigation, no hardware): confirmed via `findings.md` F-013 that
`REG_DEPTH_A` (0x33) is captured across the same breadth as LINCNT — all
7 single-pass files, 6 DPI points, not DPI-specific. Verified directly
against file 7's raw register-write event log: `0x33` writes **23 → 31 →
4** before the image preamble, `0xAF` writes **70 → 127 → 255** — `0x04`
(`DEPTH16_A`) is confirmed the *terminal* value right before the image
bulk transfer starts, not a transient mid-sequence artifact (the sequence
does pass through `31 = 0x1F = DEPTH8_A` earlier, ruling out a "this
register is just a constant/misnomer" theory). `pyopticfilm`'s own use of
`DEPTH16_A=0x04` for shading is itself inherited from the SE's original
session 03/04 captures, not independently confirmed by this project's V2
captures — coincidence (or shared-ASIC reality) that it matches V2's
image-pass terminal value, not redundant proof. Same "untested
combination" caveat as LINCNT applies: every one of DEPTH_A's captures
also carried the corrected LINCNT convention.

**Given that, repeated `POF_GL128_V2_FIX_LINCNT=1` +
`POF_GL128_V2_FIX_DEPTH_A=1` together at 7200dpi** — the exact DPI where
`LINCNT`-alone and `DEPTH_A`-alone each corrupted pre-fix, a more
relevant repeat than re-running 1800dpi. Env verified clean before
setting. **Result: mechanically clean (T67 only), prescan and scan both
good.** Image (`/home/tobby/claude/scans/4/color_short_7200dpi.tif`,
7206×10272×3) verified: 0% near-black, real content throughout, no
streaking. Only the same benign, expected crop as before —
`compute_geometry(7200)` gives `shift_b=48`, expected lines 7253, actual
7206 (47-line shortfall, matching `shift_b` almost exactly) — the same
documented GL128 CCD row-shift trim from the previous entry, not
corruption.

**Two clean, correct trials in a row for `LINCNT`+`DEPTH_A` together**
(1800dpi and 7200dpi — the latter being the exact DPI where both flags
individually corrupted pre-fix). Meaningfully stronger support now for
the "untested combination caused the corruption" theory over either
fix's own formula being wrong.

**Practical effect:** `FIX_LINCNT`+`FIX_DEPTH_A` together is now
2-for-2 clean and correct on real hardware, including at the DPI most
relevant to the original corruption reports. `DEPTH_A` alone (isolated
from `LINCNT`) remains the one flag never tested by itself under the
fixed platform — lower priority now, given the combination it was always
captured in tests clean.

**Not done:** `DEPTH_A` alone, in isolation; a third combined trial for
further confidence; root-causing the separate corruption/stall bug
(still not reproduced in any of today's LINCNT/DEPTH_A trials).

---

## 2026-08-30 — New "zoomed in" symptom discovered while local-testing #40+#41+constants together; `feed_to_scan_steps` A/B tested and cleared; T67 also cleared of this feed

At the user's request, drafted a fourth upstream PR (`fix/v2-constant-corrections`,
branched fresh off `upstream/main`, independent of `#40`/`#41`) applying
`feed_to_scan_steps`, `dummy_by_dpi[7200]`, `image_depth_a`, and
`image_lincnt_per_line`/`usb_image_lincnt_half_lines` as real hardcoded
defaults (not opt-in flags) — same treatment as `#40`/`#41`. Full draft
PR body prepared, not yet opened.

**Before opening it, the user asked to locally test all three pending PRs
together** (`#40` + `#41` + the new constants draft) merged onto
`upstream/main`, via a temporary local integration branch
(`integration-test-40-41-constants`) — not pushed anywhere, purely for a
real-hardware sanity check before proposing the draft. Merged cleanly
(one trivial non-overlapping conflict in `model_8100_v2.py`, `#40`'s
`use_slow_final_positioning_feed` next to `#41`'s `default_gl128_prime`).
267/257 tests passed at each stage, lint clean throughout.

**User: "so my concern was real, i think when i say crop i meant zoomed
in."** Re-examined the LINCNT+DEPTH_A test images *visually* for the
first time (earlier analysis only checked pixel dimensions, not content)
— both the 1800dpi and 7200dpi combined-flags images show content
filling the frame edge-to-edge with no border margin, only a small
corner of the real subject visible, mostly out-of-focus background. This
is a much bigger, real problem — not the small CCD-shift line trim
diagnosed earlier.

**Investigation, in order:**
1. Checked whether either of `#40`/`#41`/the constants PR touch the
   physical-area geometry constants (`x_size_ta_mm` etc.) — none do.
2. User provided a reference file, `/home/tobby/claude/scans/12k-7200dpi.tif`
   (7206×10368) — full subject visible, black border margins, rotated
   90° relative to our scans. Asked whether it's a guaranteed same-frame
   comparison — user said it could differ, offered to get a same-session
   reference instead.
3. **Launched Scan Lab on plain, unmodified `upstream/main`** (detached
   HEAD, zero fixes) to get a true apples-to-apples reference of the
   *current* film. Result (`scans/fullframe.tif`, 7206×10272): matches
   the user's `12k-7200dpi.tif` reference exactly — full subject, correct
   framing, same rotation. Confirms plain `main` frames this correctly.
4. **User proposed a better-controlled A/B**: presume `#40`+`#41` are
   correct (already independently verified, unrelated to positioning),
   and A/B test `feed_to_scan_steps` specifically on top of that baseline
   rather than bare `main` (removes two irrelevant confounds — no slope
   fix, priming on). Built `ab-baseline-40-41` (= commit right before the
   constants-PR merge in the integration branch, i.e. `upstream/main` +
   `#40` + `#41`, `feed_to_scan_steps` still unmodified at `13128`).
5. **Test A** (`feed_to_scan_steps=13128`, the `#40`+`#41` baseline
   unmodified): two scans (`up-40-41-test.tif`, `up-40-41-test2.tif`),
   both correct framing, matching the reference. Phase-correlation
   between the two: 59px Y-shift — real, attributable to issue #33's
   known, unresolved jitter, not a framing problem (visually both scans
   were clearly correct).
6. **Test B** (`feed_to_scan_steps=13486` only, same baseline
   otherwise, branch `ab-test-13486`): two scans
   (`up-40-41-test3.tif`, `up-40-41-test4.tif`) — **also correct
   framing**, matching Test A and both references. Phase-correlation
   Test A vs. Test B: only 5.5px — well within normal jitter, not a real
   difference.

**Conclusion: `feed_to_scan_steps` is not the cause of the "zoomed in"
symptom.** Both `13128` and `13486` produce correct framing on this
hardware/frame when tested in isolation on the `#40`+`#41` baseline —
directly contradicting the working hypothesis from the previous entry.
This was a wrong lead; noted and moved on rather than defended.

**Revised leading candidate, reasoned through with the user:**
`feed_to_scan_steps` only controls *where the carriage starts* (before
the image pass). `image_lincnt_per_line`/`usb_image_lincnt_half_lines`
control `register_lincnt` — how many physical lines the ASIC is told to
travel *during* the image pass itself, i.e. how much of the frame
actually gets captured. If that's wrong in the direction of covering
*less* physical distance than the target output height needs, the
result would be exactly this symptom: same output pixel dimensions,
content from only a small physical sub-region, stretched to fill them.
Mechanistically a much better fit than the feed target ever was — not
yet tested in isolation.

**User's own T67 hypothesis, tested and ruled out:** suspected T67 (the
recurring 3-6s hum) might be the second positioning feed (governed by
`feed_to_scan_steps`) driving against a mechanical end-stop
unnecessarily. Tested `feed_to_scan_steps=0` on the `#40`+`#41` baseline
— per `gl128.py`'s `_feed_capture()`, a 0-step feed returns immediately,
a true no-op with zero motor movement for that specific feed (confirmed
in code before testing, not just assumed). **Result: T67 still
occurred.** Since there was no motor movement at all from this feed and
T67 happened anyway, this cleanly rules out the second positioning feed
as T67's source — consistent with the existing AFE/DPI-change
calibration-path hypothesis from earlier today, not a new lead.

**Branches created this entry** (all local only, none pushed):
`integration-test-40-41-constants`, `ab-baseline-40-41`,
`ab-test-13486`, `ab-test-feed-zero`, plus the original
`fix/v2-constant-corrections` draft (still uncommitted-in-spirit — the
`feed_to_scan_steps` correction needs to come out of it pending the
LINCNT isolation test below).

**Not done:** isolating `image_lincnt_per_line`/`usb_image_lincnt_half_lines`
alone against the same `#40`+`#41` baseline (the new leading candidate
for "zoomed in"); removing `feed_to_scan_steps` from the constants PR
draft; re-testing `dummy_by_dpi`/`image_depth_a` alone against this
baseline (untested in this investigation, not yet implicated either
way); opening the constants PR (paused pending this).

---

## 2026-08-30 — "Bug T67" deep-dived (root cause not found, paused); upstream `#40`/`#41` confirmed merged; constants PR canceled; fork synced; PR #10 cleaned up

At the user's request: re-read this whole file plus the reference docs
and re-evaluated "Bug T67" (the recurring 3-6s hum) from scratch,
including the day's actual scan/test patterns rather than just the prior
hypotheses. Wrote up a ranked-candidate analysis (AFE-hang-fix retry
loops as the leading theory, given their bounded ~2s×2 wait windows
summing close to T67's duration) and proposed instrumented timing as the
next step.

**Attempted a real USB capture first** (same method that found the
slope-table root cause) — blocked: the currently *running* kernel
(`7.2.0-1-cachyos`) has no installed module directory at all (system has
since upgraded to `7.2.2-1-cachyos`/LTS but hasn't rebooted), so `usbmon`
can't load. Not fixed by rebooting the user's machine mid-session without
asking; user chose the software-instrumentation alternative instead.

**Instrumented `_reset_stationary_scan_engine()` and
`_wait_idle_at_home_for_stationary()`** (temporary `T67_TRACE` timestamped
logging, additive only, no behavior change) and ran a real trial via
`enable_debug_logging()` + a small launcher script. **Result: neither
function was ever called** — this particular scan reused a cached ASIC
shading table (`"GL128 applied cached ASIC shading"`), skipping the
stationary-strip acquisition path entirely. T67 occurred anyway per the
user. **This hypothesis is ruled out, cleanly, by direct evidence.**

**Pivoted to plain timing analysis of the existing (already-logged) feed
sequence** — found a striking pattern across all 4 trials in that
session (2 priming-on, 2 priming-off, confirmed via
`Scanner.py`'s own "priming pass skipped" log line): the *second*
positioning feed (13128 steps, `feed_to_scan_steps`) consistently took
6.9-9.3 seconds, while the *first* feed (28292 steps, more than double
the distance) took only 0.3-0.4s — completely independent of priming.
Leading theory: this is the second feed's slow motor ramp (today's own
slope-table fix, `use_slow_final_positioning_feed=True`) simply being
audible for the first time, now that it takes multiple seconds instead
of a near-instant fast move. **Explicitly not proposing reverting to the
fast ramp to test this** — that's the exact configuration that caused
the original mechanical fault; ruled out as a test option regardless of
diagnostic value.

**Found a real anomaly while examining the DEBUG-level `0x101` status
poll log**: a 6.033-second gap with zero register I/O logged at all,
mid-feed, in one trial. Hypothesized `_read_feed_probe()`'s real
(unlogged) USB control transfer (`read_request_register`, no debug-log
wrapper) as a possible silent-blocking culprit — added timing
instrumentation around that one call site (`T67_TRACE ... SLOW`, logged
only when >100ms) and re-tested. **Result: no slow probe reads at all**,
and **the 6-second silent gap did not reproduce** in the new trial
(feed durations were quite different trial-to-trial: 1.25s and 9.21s
this time, vs. consistently 6.9-9.3s before) — despite T67 still
occurring. Both the probe-stall theory and the specific "6-second gap"
observation are now not looking reproducible/causal.

**Status: root cause not found.** Three specific, code-grounded
hypotheses ruled out by direct instrumentation (AFE-hang-fix retry
loops, slow probe reads, the specific silent-gap pattern) without
finding what actually causes the audible sound. The only thing that's
held up across every trial today: T67 happens somewhere in the
startup/positioning sequence, before the real image scan, loosely
correlated with the second positioning feed's now-variable duration —
not a fixed, pinned-down mechanism. Next genuinely useful step
identified but not attempted: real-time correlation (user calling out
loud the instant they hear it, against a live-tailing log) rather than
more blind instrumentation. **At the user's direction, paused for the
near future.** All temporary `T67_TRACE` instrumentation discarded
(never committed).

**Then, at the user's request, took stock of the day's upstream/fork
status and cleaned up:**

- Confirmed via `gh`: upstream issue `#39` closed, PR `#40` **merged**,
  PR `#41` **merged** (`upstream/main` now at `30a0fea`, 2 commits ahead
  of the `8b6e014` base everything today branched from).
- **Canceled the constants PR** (`fix/v2-constant-corrections`, never
  pushed or opened) — deleted locally, along with every other throwaway
  local branch from today's A/B testing (`ab-baseline-40-41`,
  `ab-test-13486`, `ab-test-feed-zero`, `integration-test-40-41-constants`,
  `test-feed-to-scan-steps-isolated`). None were ever pushed anywhere.
- **Synced the fork's `main`** to `upstream/main` (clean fast-forward,
  `8b6e014` → `30a0fea` — the fork's `main` had no unique commits of its
  own to lose).
- **Cleaned up PR #10**: merged the newly-synced `main` into
  `hw-ref/8100v2-capture-analysis` (chose merge over rebase — this
  branch's commit history is itself part of the documented investigation
  and rewriting it risked breaking intermediate states, since several
  commits apply/revert/re-apply the same changes). 4 conflicts, all in
  files both sides had touched (`gl128.py`, `model_8100_v2.py`,
  `model_8200i_se.py`, `scanner.py`) — all trivial wording/duplicate-field
  resolutions, no logic changes. Caught and fixed one real duplicate
  field (`use_slow_final_positioning_feed` defined twice in
  `model_8200i_se.py`) via `ruff` after resolving, not before. 267
  passed, 1 skipped; lint clean. Pushed — PR #10's diff against the base
  now correctly shows only fork-only content (isolation flags, `FEED_PROBE`
  retirement, `docs/hw-ref/8100v2/*`, capture-analysis tooling) instead of
  duplicating what's now merged upstream. PR #10's description rewritten
  to reflect current status (`#40`/`#41` merged, constants PR canceled,
  T67 paused).

**Not done:** T67's actual root cause; the "zoomed in" framing
regression from the canceled constants-PR investigation; the separate
image-corruption bug; issue #33's jitter.

## 2026-09-01 — N-bracket ME (PR #47, upstream): AFE-strip stationary hang confirmed unfixable by capture-derived retry, blind-sleep formula caught as unsupported by its own cited captures, PR scope cut; new color-balance finding filed

**Context switch to upstream work-in-progress:** picked back up
`jboneng/pyopticfilm` PR #47 (N-bracket multi-exposure ME, `n_brackets`
2-9), left mid-session the previous night. A prior session (uncommitted,
never reached this log) had, after hitting a real hardware stall during
5-bracket/7200dpi/cropped testing, widened `acquire_afe_strip`'s
stationary-data-ready timeout and committed that as `69095cc`, then gone
further overnight into an uncommitted rewrite: a fast-retry backoff
ladder (claimed derived from real USBPcap captures) plus a DPI-scaled
"blind sleep" before the AGOHOME park status check (claimed derived from
a linear regression over 5 capture files at 600-3600dpi).

**First bug found on resume, before any hardware testing:** the
uncommitted `acquire_afe_strip` default `timeout_s` had been wired to
`_STATIONARY_RETRY_DATA_WAIT_S` (1.0s) instead of the hardware-confirmed
15.0s from `69095cc` — every retry attempt in the new backoff ladder was
budgeted 1s regardless of position in the ladder. Fixed (default back to
15.0), unused constant removed.

**Real hardware repro #1** (real 8100 V2, USB capture running):
1200dpi, N-Exposure/5 brackets, full image → crashed at 40% with
exactly the 1s-timeout symptom above, confirming the bug rather than a
real stall. Fixed as above.

**Verified the backoff ladder against its cited source captures** (own
tshark parsing of `REG_START` (`0x0F`) writes in
`docs/hw-ref/.../14_multi_exposure_scans/{Prescan_me,
1800ppi_Scan_No_IR_ME}.pcapng`, 8200i SE): the claimed gap sequence
(`[0.46, 0.13, 0.29, 0.16, 0.12, 0.59, 1.03, 1.33, 1.06, 2.53]`) is real
— reproduced byte-for-byte across three independent transitions across
both files. This part of the overnight work holds up.

**The DPI-scaled AGOHOME blind-sleep formula did not.** Checked the
formula (`0.0075 s/dpi`, claiming a "near-perfect linear fit" from 5
captures at 600/1200/1800/2400/3600dpi) against real data:
- No 2400dpi or 3600dpi single-pass capture exists on disk in the cited
  form at all.
- The 8200i SE DPI-ladder captures that do exist (600/1200/1800dpi) give
  gaps of 3.26s/1.27s/2.78s — not monotonic with DPI, let alone linear.
- A real 3-pass 7200dpi VueScan capture of the actual 8100 V2
  (`/home/tobby/claude/morecapture/3.pcapng`, `readme.txt`: "Option
  Number of passes: 3") shows a max silent gap of **0.9s** anywhere in
  420s — the formula predicts 54s at 7200dpi.
- The cited `1800ppi_Scan_No_IR_ME.pcapng` itself: max gap anywhere in
  the whole 112s capture, including the final AGOHOME park, is 0.995s —
  the formula predicts 13.5s at 1800dpi, on the very file it claims to
  be derived from.

Removed entirely (`_AGOHOME_SETTLE_S_PER_DPI`, `_last_scan_dpi`,
`blind_sleep_s` plumbing, the `FakeUsbTransport` mock-detection dance) —
unsupported by any real capture, contradicted by the ones it claimed to
use. **Lesson for future sessions: a code comment citing a capture-
derived regression is a claim, not evidence — verify the source files
exist and actually show the claimed numbers before trusting or building
on it.** This one didn't, likely fabricated late in an overnight,
fatigued session.

**Real hardware repro #2**, with the 1s-timeout bug fixed and the
blind-sleep code removed (backoff ladder + `_STATIONARY_POLL_S=0.25`
kept, USB capture running): same 1200dpi/5-bracket/full-image case →
crashed again, this time at 60%, same symptom. Log showed all 11 backoff
attempts (10 retries, ~170s total) genuinely exhausting their full 15s
data-ready wait at status `0xc9` — a real, persistent stall, not a
timeout artifact. **The capture-derived backoff ladder does not recover
this stall on real 8100 V2 hardware.**

**Decision: pulled the whole AFE-strip-hang thread out of PR #47.**
`acquire_afe_strip` predates this PR (initial release) — N-bracket just
triggers more mid-scan remeasures, it didn't introduce the hang. Only
ever confirmed on the 8100 V2. Reverted `69095cc` on `feat/me-n-brackets`
(new commit `1274fc1`, pushed), discarded the uncommitted backoff-ladder
work entirely, filed the hang as its own issue
(`jboneng/pyopticfilm#49`, includes tonight's negative backoff-ladder
result), posted a PR #47 comment explaining the scope cut, updated PR
#47's description, and marked it ready for review (was draft).

**New finding, ME image quality (separate from the hang):** comparing
`single (fixed 42k)` / `ME fixed-2 (existing)` / `N5 (new)` on the same
8100 V2 frame (inverted to a positive), `single` and `ME fixed-2` agree
on color balance in a highlight region; `N5` shows a visibly warmer
cast there (measured R/B ratio: single 0.921, ME-fixed-2 0.955, N5
0.993). Noise is still best on N5, matching PR #47's own numbers — this
is a color-balance shift, not a quality regression. Traced a plausible
mechanism in `merge_n_exposures`: per-channel confidence ramps down
starting at 80% full-scale independently per channel, so a channel
nearer its clip point at the (8100 V2: fixed, non-adaptive) 42000
ceiling loses weight to shorter brackets more than a channel that isn't
— a genuine color effect, not just brightness, and one that only exists
because the 8100 V2 is pinned to `me_default_exposure_mode="fixed"`
(added by this same PR) rather than adaptively picking a ceiling that
avoids the clip band per scene, as the 8200i SE does. Filed as a tuning
question, not a bug: `jboneng/pyopticfilm#50`. Untested on 8200i SE;
only one 8100 V2 frame tested so far.

**Not done:** AFE-strip hang root cause (#49) — real-time correlation
with live register state while it's stuck is the next idea, not
attempted. Color-balance shift (#50) — needs a second frame to confirm
it's systematic rather than scene-specific, and an 8200i SE test to see
if it's V2-only. Neither T67, the "zoomed in" framing regression, the
image-corruption bug, nor issue #33's jitter were touched this session.

## 2026-09-05 — New Forensic-tab CLI tooling built (upstream PR #54); T67 revisited with it; "first-scan-only" theory tested and disproved

**Context**: separate session comparing `jboneng/pyopticfilm` PR #52
(N-bracket ME) against `main` for single-pass regressions, using the
Forensic tab (`tools/scanlab/forensic_*.py`, upstream PR #54,
`feat/register-reference-catalog`). Along the way, added a headless
`compare` (+ `list-runs`) subcommand to `tools/scanlab/cli.py` — wraps
`first_divergence`/`build_ai_report` with no Qt, so two recorded runs
(even from different checkouts sharing a `tools/scanlab/runs` directory
junction) can be diffed from the command line. Pushed to PR #54.

**Bug found in that new CLI, not in the engine**: `cli.py`'s `scan`
subcommand hardcoded `--gl128-prime` to `default=True` and always
forwarded an explicit bool to `Scanner.scan()`, bypassing
`Model8100V2.default_gl128_prime=False` entirely — every CLI-driven
recording was forcing the discarded priming pass back on, unlike
`examples/scan.py` and the GUI (both correctly leave it `None` → model
default). Fixed to a tri-state default (`None`) matching `Scanner.scan()`'s
own convention and the GUI's `_gl128_prime_arg()`. Pushed to PR #54.
Confirms priming itself needs no further action here — it's already
correctly off for V2 in every real code path; the bug was confined to
this one new debug tool.

**Register-`0x21` divergence (main vs. PR #52), investigated and
attributed to jitter, not PR #52**: the new `compare` subcommand's first
real use, on a plain single-pass 600dpi scan, found a first-divergence at
event 62 (register `0x21`, value 0 vs 4). Code check: `asic/gl128.py`'s
feed-probe poll loop is byte-identical between `main` and PR #52; the
divergence sits entirely inside the (accidentally forced-on, per the CLI
bug above) discarded priming pass. 15 pairwise comparisons — 3 same-branch
`main` runs, 3 same-branch PR #52 runs, all 9 cross-branch pairs — showed
divergences scattered across several registers/indices (`0x37`, `0x101`)
on *both* same-branch and cross-branch pairs alike, at no better rate for
cross-branch than same-branch. Concluded: real-hardware run-to-run jitter
inside the discarded priming pass, not a PR #52 effect. (Tangential to T67
below, but same discarded-pass mechanism as the priming investigation
higher up this log.)

**T67 revisited with the new tooling — "first-scan-of-session" theory
tested and disproved.** Issue #33's own reference-driver capture analysis
(`drafts/issue-33-comment.md`) noted the vendor driver targets a
different, smaller second-feed distance (13128 steps, "top of TA window")
only on the *first* acquisition of a session, switching to ~13486 for
every later one — raising the question of whether pyopticfilm's own
~6.9-9.3s second-feed duration (this session's own earlier finding, see
above) might likewise only be slow on the first `scan()` call after
`Scanner.open()`.

Tested directly: two `scanner.scan()` calls in one `Scanner.open()`
session (no reopen between them), each independently Forensic-recorded,
with the CLI's `gl128_prime` bug already fixed so priming stayed
correctly off for both. **Result: theory disproved.** Both calls targeted
the identical 13128-step distance (pyopticfilm never switches targets
between calls, unlike the reference driver) — first call: 6.94s, second
call: 9.17s (*longer*, not shorter). Duration remains within the same
0.3-9.2s variable range already documented above, uncorrelated with
call-order.

Also confirmed (re-reading `model_8100_v2.py`'s own docstring, not new
evidence) that this 13128-step feed is **not removable** — it's the
capture-confirmed move to the top-of-TA-window scan start position
(`04_color_7200.pcapng` frame 2999); skipping it would misposition every
scan, not just save time. What's variable is purely how long that
already-necessary move takes under the slow ramp
(`use_slow_final_positioning_feed=True`, the motor-overspeed safety fix —
not to be reverted to test this).

**Not done:** T67's actual root cause remains open — "first-scan-only"
is now a fourth ruled-out mechanism (alongside priming, the zeroed second
feed, the AFE-hang-fix retry loop, slow probe reads, and the specific
6-second silent-gap pattern from 2026-08-30). Real-time human/log
correlation (watching the Forensic tab's live per-event timestamps while
listening) or an actual USB capture (`usbmon` kernel-module issue on the
Linux rig, still unresolved) remain the next genuinely untried steps.

**Addendum, same day: visually confirmed the second feed is load-bearing
for image correctness, not just noise — and the August "zeroed feed"
test zeroed the wrong attribute.** The August 2026-08-30 entry's
`feed_to_scan_steps=0` test zeroes a field that, per `gl128_common.py`'s
`feed_to_scan_steps_for_area()`, is **only consulted when
`geometry.area is None`** — the real full-frame session path (via
`image_feed2_steps()` in `session_gl128.py`) always supplies a concrete
`area` tuple, so that field was never actually read at runtime; the
August test's "T67 still occurred with zero motor movement" conclusion
may itself need revisiting; the effective full-frame second-feed
distance instead comes from the *shared* `Gl128Common.feed_to_scan_top_steps
= 13128` field, which `Model8100V2` never overrides.

Re-tested today with the correct field
(`dataclasses.replace(model, feed_to_scan_top_steps=0)`, real hardware,
600dpi single-pass, one fresh `Scanner.open()` session per trial,
16-bit TIFF saved for visual inspection):

| | baseline (13128) | `feed_to_scan_top_steps=0` |
|---|---|---|
| Image shape | 600×856×3 | **1147×856×3** |
| Visual result | correctly framed | **large solid-black band (~45% of height) prepended before the real photo** |
| Total `scan()` elapsed | 20.34s | 21.56s (not faster) |
| `probe_read` (0x21) count | ~350+ | **7** (polling loop genuinely eliminated) |

So the motor-noise-causing poll loop *does* disappear when this feed is
zeroed, confirming it as a real, physical contributor to the audible
sound — but the scan is then wrong (wrong start line, taller image with
a black gate/leader band) and takes the same total time regardless (the
saved feed-wait time is spent transferring the extra, unwanted image
rows instead). **Conclusion: this positioning move is necessary and
should not be removed or shortened by distance — only its speed (the
slow ramp) is a legitimate target if a quieter/faster experience is
wanted, and that trades directly against the motor-overspeed safety
margin the slow ramp exists for.**

## 2026-09-05 (cont'd) — Second-feed ramp-speed A/B/C test, in person, on the exact fault-history configuration

**Same session as above, continued at user's direction** to actually test
the ramp-speed tradeoff just concluded above, with the user physically
present at the hardware (hand on the power switch) for every trial.
Technique throughout: monkeypatch the module-level `SLOPE_TABLE_SLOW`
name in `pyopticfilm.asic.gl128` (process-local only, reverts on exit, no
file edits) so `_upload_fast_slopes(use_slow=True)` uploads a different
table for the second feed than production ships.

**A (baseline, unmodified) vs. B (custom, 17.5% blend toward FAST at
every one of the 256 per-step entries) — 600dpi single-pass:**

| | A | B |
|---|---|---|
| Second-feed duration | 6.94s | 5.95s |
| Total scan time | 20.55s | 19.54s |
| `probe_read` count | 120 | 104 |
| Outcome | success | success, correct image |

B is a genuinely new table (never vendor-captured, never run on this
hardware before this trial) but still ~4.3x slower than
`SLOPE_TABLE_FAST`'s own steady-state (895 vs. 208 ticks/step) — a
conservative first step. Clean on one trial; user confirmed both sounded
fine in person.

**C: full `SLOPE_TABLE_FAST` for the second feed — the *exact*
configuration `6198d4b` documented as having caused the original
"high-pitched sound followed by a thunk" mechanical fault.** Explicitly
flagged this to the user before running (fast-fast has never been seen in
*any* real vendor capture on either GL128 model — V2's own captures show
fast-then-slow, the SE's show slow-then-fast, 39/39 pairs — so this is a
deliberate departure from all known vendor behavior, not a simplification
toward it). User accepted the risk and ran it anyway, in person:

- 600dpi single-pass: second-feed 1.54s (vs. 6.94s baseline), total scan
  15.16s (vs. 20.55s), correct 600×856×3 image, no exceptions.
- 1200dpi multi-exposure fixed mode, full frame: 90.15s total, correct
  1201×1712×3 image, 2971 events, 102 anomalies (2 warning/4 critical —
  same categories as every other trial today, nothing new), no
  exceptions.
- User confirmed both sounded fine in person, no grinding/clunk/stall.

**Follow-up validation campaign, ascending DPI order, stop-on-exception,
user present with hand on power switch throughout** — one trial each at
1200/1800/3600/7200dpi, single-pass, full frame, full fast-fast:

| DPI | Outcome | Image shape | Elapsed | Anomalies (warn/crit/info) |
|---|---|---|---|---|
| 1200 | success | 1201×1712×3 | 24.09s | 1/2/39 |
| 1800 | success | 1801×2568×3 | 33.28s | 1/2/39 |
| 3600 | success | 3603×5136×3 | 71.59s | 2/2/162 |
| 7200 | success | 7206×10272×3 | 130.83s | 2/2/166 |

All four succeeded, no exceptions, correct framing confirmed visually at
every DPI (7200dpi checked closely — sharp, correctly positioned, no
artifacts). Combined with the two trials above: **6 total clean fast-fast
trials today, spanning 600/1200/1800/3600/7200dpi** (5 distinct DPIs, one
DPI — 600 — covered twice, once single-pass and once via the earlier
600dpi trial before this campaign).

**Compared explicitly against `6198d4b`'s own validation bar** ("6+
consecutive full scans at 1200/1800/3600/7200dpi, following repeated
faults before it under the same test conditions"): today's campaign
**matches the DPI coverage exactly** but has **fewer total repeats** (6
today, spread across DPIs and scan modes, vs. 6+ *consecutive at the
fault-reproducing configuration* in the original). Today's runs also
differ from the original validation in kind: they're testing whether
fast-fast (the pre-fix, fault-documented state) is *safe*, whereas the
original 6-trial bar was validating that *slow-final* eliminates a fault
that had already reproduced multiple times under fast-fast. Today never
reproduced that fault, on this unit, today — that is real, positive
information, but it is not the same evidentiary shape as the original
validation, and a single day's clean streak on one unit is not the same
claim as "this is now safe to ship as the default for every 8100 V2."

**Not done:** repeated trials at the *same* DPI (today's campaign was one
trial per DPI, not multiple back-to-back at any single DPI beyond the two
600dpi/1200dpi trials earlier); any change to shipped code
(`model_8100_v2.py`'s `use_slow_final_positioning_feed` remains `True`,
unchanged) — today's fast-fast runs were all via the runtime monkeypatch,
nothing committed. Decision on whether/how to pursue a production change
deliberately deferred pending more data.

## 2026-09-05 (cont'd) — Fast-fast produces corrupted images in further testing; reverted, not pursuing

**Negative result, reported by the user from further hands-on testing**
(the fast-fast monkeypatch had been wired into a real downstream
application, NegPy, via a `_v2_fast_final_feed` context-manager patch in
`negpy/infrastructure/scanners/plustek_backend.py`, scoped to the 8100 V2
by model name — reverted as of this entry, see below): additional
fast-fast scans through that path **produced a corrupted image — a
single-color noise pattern** — while the sound stayed good. No mechanical
fault (no grinding/thunk), but a real data-integrity failure: something
in calibration or another startup-dependent step is sensitive to the
faster second-feed timing in a way none of today's earlier 6 clean trials
happened to trigger.

**No forensic run or saved image evidence exists for the corrupted
trial(s)** — this was observed by the user directly during their own
testing, not captured through the `ForensicRun`/`tools.scanlab.cli`
pipeline used for every other trial today. That's a real gap: we don't
have the decoded-event trace, DPI, or exact repro steps for the failure,
only the symptom report. Worth capturing properly (forensic-logged) if
this is revisited.

**Action taken:** reverted the NegPy patch entirely (`git checkout --`
on `plustek_backend.py` — the file is back to unmodified, shipped
behavior, `SLOPE_TABLE_SLOW` used unconditionally for the V2's second
feed, no monkeypatch). No pyopticfilm file was ever edited — every
fast-fast trial all day used a process-local runtime monkeypatch of
`pyopticfilm.asic.gl128.SLOPE_TABLE_SLOW`, so no repo state needed
reverting there.

**Revised conclusion, superseding the "6 clean trials, promising" framing
above:** fast-fast is **not safe** to pursue further without a lot more
investigation — it can silently corrupt image data even when it sounds
and completes normally, which is a worse failure mode than the
originally-documented mechanical fault (that one was at least audible and
obvious; this one produces a bad file with no obvious warning during the
scan). `model_8100_v2.py`'s `use_slow_final_positioning_feed=True`
remains the right shipped default. Not pursuing a production change.
Any future revisit of this should start by reproducing the corruption
under Forensic-tab recording so there's an actual event trace to diagnose
against, rather than repeating today's approach of trusting "no exception
+ correct dimensions" as sufficient evidence of a good scan — it clearly
isn't.
