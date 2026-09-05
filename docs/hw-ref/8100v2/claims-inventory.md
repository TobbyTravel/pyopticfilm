# 8100 V2 claims inventory

Every concrete, testable claim about the 8100 V2 / GL128 found in docs, code
comments, and constants, as of repo commit `75fcf8c` (branch
`hw-ref/8100v2-capture-analysis`). Each gets an ID (`C-NNN`) used as the
checklist for Phase 3 verification against the reference driver captures in
`C:\dev\morecapture` (files `1.pcapng`–`11.pcapng`, no `5.pcapng`).

**Provenance warning (read first):** almost every V2-specific claim below
cites `04_color_7200.pcapng` / "capture session Aug 2026" — a capture file
that is **not** among the 11 files we have, and whose capture software
(the reference driver vs the SE model's original reference driver vs another driver) is undocumented in-repo. The 8200i
SE tables it inherits from are explicitly cited as **the SE model's original reference driver** captures
(`captures/8200i-se/`, sessions `03`–`14`). Our new captures are confirmed
the reference driver (`readme.txt`, autocrop on). So:

- Every SE-inherited table (C-010–C-024) is a claim from the SE model's
  original reference driver, run on 8200i SE hardware,
  not yet validated against V2 hardware *or* the reference driver at all.
- Every V2-override claim (C-001–C-006, from PR #30) is validated against an
  unknown-software V2 capture that predates our dataset.
- Our job is to re-derive these independently from the reference driver captures and
  record agreement/disagreement — a match is not guaranteed even where PR #30
  already "confirmed" a value, because the driver differs.

**Numbering caveat:** the user has flagged that the readme.txt file→scan
mapping for files 6–11 may not be reliable (file 11 is confirmed 7200 dpi by
the user, contradicting its own label of "3600 dpi"). Phase 1 must derive
DPISET from registers, not trust the label, for every file 6–11 claim below.

**Note (2026-09-05):** C-002, C-012, C-013, and C-014's register-meaning
conclusions have been re-derived into the confidence-tagged catalog at
[jboneng/pyopticfilm#54](https://github.com/jboneng/pyopticfilm/pull/54)
(see the "migrated" note on each row below) — kept here too since the
capture-specific comparison detail is still relevant background for the
rest of this table.

## Status legend

`unverified` (default, nothing checked yet) · `confirmed` · `contradicted` · `new` (found during analysis, wasn't a prior claim)

---

## A. V2-specific overrides (PR #30, `src/pyopticfilm/device/model_8100_v2.py`)

| ID | Claim | Source | Status |
|----|-------|--------|--------|
| C-001 | `feed_to_scan_steps = 13128` for V2 full-frame colour scan (SE uses 13704) | model_8100_v2.py:90, PR #30 frame 2999 regs 0x3D-0x3F | **contradicted (F-007)**: 7 the reference driver full-frame scans across 5 DPIs use feed2=13486, not 13128; 13128 appears only on the session's first-ever acquisition (confounded with capture 3 also being the only multi-pass capture — see F-007 for the two open hypotheses) |
| C-002 | `lperiod_by_dpi[7200] = 16035` for V2 (SE uses 15963) | model_8100_v2.py:65-68,93-95, PR #30 frames 1661/2257/3203 reg 0x28-0x2A | **contradicted (F-009)**: the reference driver captures 4 and 11 (both 7200dpi) show LPERIOD=15914 and 15999 respectively — neither matches 16035, and they don't match each other either. **Migrated to pyopticfilm#54** (`REG_LPERIOD`, SUSPECTED) |
| C-003 | V2 white shading-strip dummy (reg 0x2B) at 7200 dpi = `0x10` (SE computes `0x17`) | model_8100_v2.py:106-120, PR #30 frame 2257 reg 0x2B | unverified |
| C-004 | `max_image_lincnt_by_feed2 = {13128: 29012}` for V2 (SE has `{13128: 4836}`, a different-dpi entry) | model_8100_v2.py:99-101, PR #30 frame 3203 regs 0x25-0x27 | table key suspect (F-007: 13128 is session-first-only here). Value also suspect: F-012 shows the reference driver's V2 LINCNT is output-line-count directly (no ×4); PR #30's cited 29012 only makes physical sense under the *old* SE ×4 convention (7252 output lines × 4 ≈ 29008, close to 29012) — either PR #30's source capture used different LINCNT semantics than the reference driver, or V2 behaves differently at full-window travel than at the cropped travel this dataset tested. Not resolved — flag for a future capture at V2's true full-window (top-of-window to scan-window-end) travel |
| C-005 | `ladder_feed2_steps = 13128` for V2 (SE uses 13560) | model_8100_v2.py:104, PR #30 frame 2999 | unverified directly; same 13128-vs-13486 concern as C-001/C-004 (F-007) |
| C-006 | V2 confirmed matches with SE (no override needed): `feed_to_reference_steps=28292`, `STRPIXEL=242`, `ENDPIXEL=10610`, `DPISET=1200` (=7200/6), base `EXPOSURE=14000`, AHB addresses, motor slope table sizes, USB protocol framing | PR #30 body | unverified |
| C-007 | Geometric identity: `(27636 − 13128) / 14400 × 7200 × 4 = 29016 ≈ 29012` (observed LINCNT) | PR #30 body | **suspect (F-007)** — built on the same 13128 that our data shows is session-first-only, not the steady-state feed2; re-derive with 13486 in Phase 3 |
| C-008 | Declared image bulk size `29012 × 10368 × 3 = 902389248` bytes for V2 7200 dpi full frame | PR #30 body | **contradicted as a general formula (F-012)**: the verified the reference driver formula is `LINCNT × width_px × 3channels × 2bytes` (note ×2 for 16-bit, not ×3 — PR #30's formula is missing the depth factor entirely, or assumes 8-bit samples, which contradicts C-014's confirmed 16-bit wire samples). Matches file 4/11's actual announced sizes exactly (416,239,356 / 443,613,456) using the ×6 formula; PR #30's own formula does not reproduce either |
| C-009 | Lower-priority, **not yet implemented**: GPO regs 0xA7/0xA9 = 0x01 during dark shading; reg 0x1D bit-1 set during calibration; other-DPI LPERIOD values differ from SE | PR #30 body | new — candidate follow-up PR items, check against our multi-DPI captures (6-10) |

## B. SE-inherited tables the V2 shares unmodified (`model_8200i_se.py`), unvalidated for V2

| ID | Claim | Source | Status |
|----|-------|--------|--------|
| C-010 | `register_dpiset_by_dpi`: DPISET = dpi/6 at ≥600dpi; floors at 100 (=600dpi) below 600 | model_8200i_se.py:126-138 | **confirmed** — captured DPISET matches `compute_geometry()`'s prediction exactly at all 7 checked files/DPIs (600/1200/1800/2400/3600/7200×2), see `register-program-by-dpi.md` |
| C-011 | `_LPERIOD_BY_DPI` full table (150→11064 ... 3600→13407, 7200→16035 after V2 override) | model_8200i_se.py:158-170 | unverified except 7200 (see C-002) |
| C-012 | `_DUMMY_BY_DPI` full table (150-900→0x01, 1200-1800→0x02, 2400→0x03, 3600→0x04, 7200→0x17) | model_8200i_se.py:215-227 | **contradicted at 7200dpi (F-013)**: captured dummy is 15/16, not 23 (0x17) — a ~35% difference, at the one DPI where this table jumps far above every other entry. Other DPIs match (see `register-program-by-dpi.md` `dummy(0x2B)` column: 1,2,2,3,4 for 600-3600, matching table exactly). **Migrated to pyopticfilm#54** (`0x2B`, SUSPECTED) |
| C-013 | `image_lincnt_per_line = 4` — LINCNT register units per output line; bulk buffer carries LINCNT/2 rows (Y oversampled 2x), host averages pairs | model_8200i_se.py:347-352 | **contradicted for V2 (F-012, resolved)**: V2's LINCNT is the output line count directly (effectively `image_lincnt_per_line=1`), confirmed by an exact zero-diff bulk-size formula match on 7/7 files. This is a constant specific to the SE model's original reference driver, and does not apply to V2 under our reference driver — candidate `Model8100V2` override |
| C-014 | `usb_image_depth = 16` (16-bit LE samples on wire) despite DEPTH8 *registers* during image pass (session 11 oracle) | model_8200i_se.py:277-278, session_gl128.py:183-190 | `usb_image_depth=16` **confirmed** (F-012's formula needs ×2 bytes to match exactly); `usb_image_lincnt_half_lines=True` (LINCNT/2 wire-row halving) **contradicted for V2** (F-012); and the "DEPTH8 registers" half of this claim is also **contradicted (F-013)** — the reference driver's real image pass writes DEPTH_A=0x04 (a DEPTH16 value), not DEPTH8_A=0x1F, paired with DEPTH_B=0xFF (DEPTH8_B) — a mixed pair `pyopticfilm` never programs. **Migrated to pyopticfilm#54** (`REG_DEPTH_A`/`REG_DEPTH_B`, SUSPECTED) |
| C-015 | `optical_end_inactive_native = 96` — trailing dummy clocks per line to drop, not shrink ENDPIXEL by | model_8200i_se.py:300-305 | unverified |
| C-016 | `ld_shift_r/g/b = 0/24/48` — tri-linear CCD channel line-shift (measured at 1200dpi Lab scan, not GL128 capture) | model_8200i_se.py:333-339 | unverified, and explicitly NOT capture-derived per its own comment |
| C-017 | `feed_to_reference_steps = 28292` constant first feed from home before every scan | model_8200i_se.py:424 | unverified for V2 (C-006 claims PR#30 confirmed this matches) |
| C-018 | `scan_window_end_steps = 27636` hard stop; every capture satisfies feed2+travel ≤ this | model_8200i_se.py:441-445 | unverified |
| C-019 | `exposure_short = 14000`, `exposure_long = 42000`, `multi_exposure_factor = 3` | model_8200i_se.py:389-394 | **partially confirmed (F-011)**: `exposure_short=14000` matches every captured image pass; `exposure_long`/`multi_exposure_factor` untestable — no ME captures in this dataset, and capture 5 (the only non-1.0-exposure single-pass capture) is missing |
| C-020 | ME exposure clamps: 7200dpi long exposure clamped to [14000,42000]; other PPI [14000,85000] | scanner-validation.md:99-107, session_gl128.py:53-70 | unverified — captures 1-11 are single/3-pass, not ME, so may be untestable from this dataset |
| C-021 | `x_offset_ta_mm=0.43, x_size_ta_mm=36.58, y_offset_ta_mm=28.5, y_size_ta_mm=25.59` (TA window geometry) | model_8200i_se.py:318-326 | **partially contradicted, minor**: captured X window is a consistent ~0.27-0.28mm narrower than these constants imply at every crop tested (file 4: 35.72mm captured vs 36.0mm readme; file 11: 36.32mm captured vs 36.6mm readme) — small but real, unresolved (secondary residual from F-010/F-012, not chased further this session). Y (`y_size_ta_mm=25.59`) is close to but not exactly file 11's captured ~25.32-25.33mm (no-crop) height |
| C-022 | `_STAGGER_BY_DPI` empty at every PPI incl. 7200 (STAGGER clear) | model_8200i_se.py:229-241 | unverified |
| C-023 | `optical_span_alignment = 4` — ENDPIXEL−STRPIXEL floored to multiple of 4 | model_8200i_se.py:290-294 | unverified |
| C-024 | `strpixel_native_units = True` — STRPIXEL/ENDPIXEL programmed in native 7200dpi clocks, byte-identical across resolutions for same crop | model_8200i_se.py:295-299 | **confirmed** — default-crop files (4/6/7/8, DPIs 600-7200) all show `ENDPIXEL−STRPIXEL` native span 10126-10128 (±2, effectively identical); full-sensor files (9/10/11) all show span 10296 exactly. Used directly by F-012's formula, which matched exactly on all 7 files |

## C. Protocol / framing assumptions (decoder + driver)

| ID | Claim | Source | Status |
|----|-------|--------|--------|
| C-025 | the SE model's original reference driver image framing: `VALUE_BUFFER` (0x82) OUT, `wIndex=0x08`, announced `bulk_size = LINCNT × width × 3`; at 7200dpi one 65508-byte URB per line | tools/scanlab/capture_pcap.py:36-37,130-133,343-345,408-412 | **split**: wIndex=0x08 for the image preamble — confirmed (F-003). "One 65508-byte URB per line at 7200dpi" — contradicted (F-004): the reference driver uses ~60-62KB multi-line chunks scaling with DPI, not a fixed 65508 constant |
| C-026 | pyopticfilm itself (not the reference driver) does NOT use one full-image preamble — it announces per-USB-chunk (`IMAGE_CHUNK_BYTES = BULK_MAX_SIZE = 0xF000`) because "a single full-image preamble was louder on real GL128 hardware" | session_gl128.py:12-14,37-44; usb/device.py:61 | new / self-observation, not from captures — could differ further from the reference driver |
| C-027 | Status register lives at `0x101` (GL128), not `0x41` (GL845); bit layout otherwise similar | gl128.py:10-11 | unverified |
| C-028 | Analog frontend reached via `0x51`/`0x5D`/`0x5E` (GL124-family) | model_8200i_se.py:11-13, gl128.py:13 | unverified |
| C-029 | Cold-boot: 116 registers written in ascending order, no soft reset first (`_INIT_REGS`, session `02_cold_boot_open`) | model_8200i_se.py:50-74, gl128.py:1119-1128 | unverified — captures 1 & 2 (power on/off) are the direct test |
| C-030 | Two 34/32-byte opaque blobs written to AHB `0x000FFF00`/`0x000FFF01` before register blast, meaning unknown, replayed verbatim | gl128.py:151-157,1126-1127 | unverified |
| C-031 | Positioning is two capture-constant feeds from home (first feed 28292, second feed model-specific), then image pass runs `FEEDL=1` with `AGOHOME` so carriage parks after | gl128.py:17-19, session_gl128.py:255-282 | unverified — capture 3 (3-pass) is the direct test for repositioning between passes |
| C-032 | Positioning feeds always upload `SLOPE_TABLE_FAST` — no existing slow-ramp option for them (unlike image-pass creep, which has `image_slope_slow`) | issue #33 body | unverified from captures — check whether the reference driver itself ever uses a slower table for its positioning feeds (issue #33 asks this) |
| C-033 | `DEFAULT_IMAGE_USB_PACE_S = 0.003` — adaptive quiet-drain paces bulk reads to LPERIOD when >0 | gl128.py:176-180,227 | not a capture claim (driver behavior) — irrelevant to the reference driver comparison directly, but relevant to #33/#35 root-cause search |

## D. Issue-specific claims to verify/extend

| ID | Claim | Source | Status |
|----|-------|--------|--------|
| C-034 | GL128 first-pass positioning: first image pass after opening lands ~46px off later passes at 1200dpi full-frame (pre-priming measurement) | scanner-validation.md:60-62 | unverified |
| C-035 | Priming (600dpi small-crop discarded pass) reduces first-scan position error from ~30px to ~1px; costs ~5s at 600dpi, ~24s@1200, ~71s@3600, ~150s@7200 for a full-frame prime | scanner-validation.md:76-83 | unverified — not directly testable from the reference driver captures (the reference driver doesn't prime), but capture 3's pass-to-pass data is comparable evidence for positioning repeatability |
| C-036 | Issue #33: ordinary scan-to-scan Y jitter 1-6px typical, mean shift ~400:1 Y:X ratio; rare 150-220px spikes only seen at 7200dpi (2 of 7 sessions), not correlated with priming/drain/feed-ramp-speed | issue #33 body | new — capture 3 (3-pass @ 7200dpi) is the only multi-pass capture in this dataset and is the direct evidence source |
| C-037 | Issue #33: feed ramp speed (SLOPE_TABLE_FAST vs slow) does not affect ordinary jitter magnitude (ruled out after a larger follow-up sweep) | issue #33 body | new |
| C-038 | Issue #35: jagged/stepped lines at 7200dpi only (not 1200/3600), present with PR#30 constants applied, not fixed by toggling priming or quiet-drain; `image_slope_slow` untested; the reference driver/the SE model's original reference driver clean on same unit | issue #35 body | new — direct target of Phase 4's #35 diff; captures 3/4 (7200dpi) vs 6-10 (other dpi, pending renumbering fix) are the evidence |

## E. Missing-data gaps (cannot be checked from this dataset)

| ID | Gap | Why it matters |
|----|-----|-----------------|
| G-001 | Capture 5 (1200dpi, RGB exposure 2.0 locked) is absent from `C:\dev\morecapture` | Blocks the planned Phase 3 exposure diff (captures 4 vs 3-pass1 vs 5 vs 6); proceeding with captures 3/4/6 only |
| G-002 | The 8100-V2-specific capture referenced by PR #30 (`04_color_7200.pcapng`, "capture session Aug 2026") is not present anywhere found so far (checked `pyopticfilm_captures` repo — no `8100` path in history) | Cannot cross-check PR #30's own citations directly; can only re-derive independently from our the reference driver set |
| G-003 | ~~File→scan-number mapping for files 6-11 is user-flagged as possibly wrong~~ **RESOLVED (F-001, F-002)**: file 11 = 7200dpi (register-confirmed, readme label "3600dpi" was wrong); files 6/8/9/10 match their labels exactly (1200/1800/2400/3600); file 7 is consistent with 600dpi but not fully distinguishable from 150/300 by DPISET alone (all program 100) | Per-DPI tables can now use files 6,8,9,10,11 at face value; file 7 needs one more cross-check (output pixel dims) in Phase 3 before treating as confirmed-600 |

---

*Generated during Phase 0 orientation, 2026-08-30. Update statuses in place as
Phase 1-4 analysis proceeds; do not duplicate rows — add new C-/G- IDs for
findings not covered above.*
