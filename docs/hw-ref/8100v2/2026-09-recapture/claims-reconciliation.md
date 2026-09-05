# 8100 V2 claims reconciliation — 2026-09 recapture

Three-way reconciliation of 8100 V2 / GL128 register and behavior claims against a
**third, independent capture set**: `TobbyTravel/pyopticfilm_captures`, branch
`add-8100-v2-captures`, 7 sessions captured 2026-09-05, integrity-verified via `tshark`
(frame counts, transfer-type/size distributions, timing segmentation) but not yet
register-decoded as of this document's creation.

**⚠ HIGH PRIORITY OPEN ITEM — LINCNT semantics bug (do not lose track of this):**
`Gl128Common.image_lincnt_per_line` defaults to `4` (`usb_image_lincnt_half_lines=True`),
and `Model8100V2` never overrides either field. Session `04_color_7200` proves by exact
integer arithmetic that V2's real convention is direct (factor 1, no half-lines) — see
the LINCNT row below. Every V2 scan in pyopticfilm today is very likely computing LINCNT
geometry with the wrong factor. This is Phase 4.3's headline item. **Update 2026-09-06:
corroborated from sessions 06 (all 10 DPIs + preview, 11/11 exact-integer matches) and 07
(both 7200dpi ME brackets, reproducing session 04's exact LINCNT=29012/bulk=902,389,248) — see
the contested-values table and the `06_ppi_ladder` per-DPI table below.** The finding is no
longer single-session; next step is the careful fix (promote these fields from
`GL128_SHARED_FIELDS` to `GL128_DIVERGENT_FIELDS`) with the full Phase 5 SE-regression
process, since the code path is shared with the 8200i SE. **Tracked in PR #13's
checklist — must not be dropped before Phase 4 lands.**

**Status: Phase 2 complete for sessions 04-07** — all six contested-value rows now carry
verdicts with exact file/frame/packet citations, each independently cross-checked via manual
`tshark -x` hex dump. This document's "Our 2026-09-05 value" column is populated by
`tools/extract_v2_registers.py` (Phase 1) plus manual cross-verification.

**Relationship to prior work** (read for comparison, not authoritative):

- `docs/hw-ref/8100v2/claims-inventory.md` / `findings.md` (branch
  `hw-ref/8100v2-capture-analysis`) — analyzed a *different*, earlier vendor-driver
  capture set (10-11 files, taken 2026-08-30, source capture software undocumented).
  Cited below as "Aug-30 hw-ref" by its existing `C-NNN` IDs.
- `src/pyopticfilm/device/model_8100_v2.py` (PR #30, merged) — cites an uncited,
  unrecoverable `04_color_7200.pcapng` from "Aug 2026." Cited below as "PR #30."
- `src/pyopticfilm/device/model_8200i_se.py` / `gl128_common.py` — SE-derived values,
  explicitly **not assumed to transfer** to V2.

This document does not edit the Aug-30 branch's docs in place — see the pointer note to
be added there once this document has real content.

## Contested values

| Claim | PR #30 value | Aug-30 hw-ref value(s) | SE-derived expectation | Our 2026-09-05 value | Verdict |
|---|---|---|---|---|---|
| `feed_to_scan_steps` / FEEDL2 (0x3D-0x3F), full-frame 7200dpi | 13128 | ~13486 (C-001; 13128 seen only on one anomalous first-cycle acquisition) | 13704 (SE `feed_to_scan_steps`) | **13128** — frame 3015 of `04_color_7200.pcapng`, payload `3d 00 3e 33 3f 48` = 0x003348. Cross-checked via manual `tshark -x` hex dump, exact match. **CORROBORATED at every one of the 10 DPI settings in `06_ppi_ladder.pcapng` (plus its preview) and at both exposure brackets of the 7200dpi segment in `07_multi_exposure.pcapng` — 13 independent occurrences total, all exactly 13128, always preceded by an identical first-feed of 28292.** Representative citations: `06_ppi_ladder.pcapng` frame 86433, payload `3d 00 3e 33 3f 48` (3600dpi); `07_multi_exposure.pcapng` frames 19095 and 37851, both `3d 00 3e 33 3f 48` (7200dpi, bracket 1 and bracket 2). All cross-checked via `tshark -x`, exact match. | **Confirms PR #30, decisively.** Contradicts Aug-30's ~13486. The "one-off" confound is now ruled out: FEEDL2=13128 is DPI-independent (constant across the entire ladder) and exposure-independent (constant across both ME brackets) — consistent with it being a fixed physical positioning distance, not a per-scan-parameter value. |
| `lperiod_by_dpi[7200]` (0x28-0x2A) | 16035 | 15914 and 15999 (two captures disagree with each other) | 15963 | **16035** — settles at frame 1673, reaffirmed unchanged at frame 3219 (image-pass register blast). Cross-checked, exact match. **CORROBORATED in `07_multi_exposure.pcapng`'s 7200dpi segment: image-pass LPERIOD = 16035 at frame 17745 (`28 00 29 3e 2a a3` = 0x003EA3) and again at frame 36509, identically, across both exposure brackets.** Cross-checked via `tshark -x`, exact match both times. | **Confirms PR #30, decisively.** Contradicts both Aug-30 values and the disputed 15914/15999 pair — this is now a second independent session's worth of evidence, not a single-capture result. |
| Shading dummy (0x2B), white strip @ 7200dpi | 0x10 | ~15/16 (C-012) | 0x17 (SE-computed) | **0x10 (16)** — frame 2269. Cross-checked, exact match. | **Confirms PR #30.** Aug-30's "~15/16" is the *same value* as 0x10=16 decimal — not actually a conflict, just a hex/decimal mismatch in how it was reported. |
| Shading dummy (0x2B), dark strip @ 7200dpi | 0x17 | _not separately re-checked in Aug-30_ | 0x04 (SE) | **0x17 (23)** — frame 1247. Cross-checked, exact match. | **Confirms PR #30.** |
| LINCNT (0x25-0x27) semantics / `image_lincnt_per_line` | inherits SE ×4/half-lines convention | V2 uses output-line-count directly (C-013) | ×4, half-lines (SE) | **LINCNT=29012 used directly as output line count, no ×4/half-lines factor.** Exact-integer proof: bulk size 902,389,248 = 29012 × 31104 (31104 = width 10368 × 3 channels × 1 byte/sample from STRPIXEL/ENDPIXEL span); 902,389,248 ÷ 124,416-byte URB = 7253 exactly (matches NOTES.md's own URB count); 124,416 ÷ 31,104 = 4 lines/URB exactly. **CORROBORATED at every DPI in `06_ppi_ladder.pcapng` (10 settings + preview, 11 exact-integer divisions, see per-DPI table below) and independently reproduced twice more in `07_multi_exposure.pcapng`'s 7200dpi segment (LINCNT=29012, bulk=902,389,248 — identical to session 04 — at frame 19299 for bracket 1 and frame 38055 for bracket 2, both cross-checked via `tshark -x`).** In the ladder, every one of the 11 segments' `main_bulk_bytes ÷ LINCNT` divides *exactly* (no remainder) into an integer bytes-per-line value that itself divides exactly by 3 (channels) — direct-line-count arithmetic holds with zero exceptions across the full DPI range 150–3600 plus preview and both 7200dpi ME brackets. | **CONFIRMS Aug-30's C-013, contradicts the inherited SE convention — now confirmed across 13 independent DPI/session data points, not just one.** This is a real architectural bug if pyopticfilm's V2 decode path assumes the SE ×4 convention — see code-impact check below. Highest-priority finding, now fully corroborated; ready for the Phase 4.3 fix (promote `image_lincnt_per_line`/`usb_image_lincnt_half_lines` to `GL128_DIVERGENT_FIELDS`). |
| DEPTH_A/DEPTH_B (0x33/0xAF) pairing, image-scan pass | not programmed (pyopticfilm chooses 16-bit throughout) | mixed pairing observed in real driver (C-014) | 16-bit shading / 8-bit image pair | **0x1F / 0xFF** (8-bit) — frames 2755 and 3015. Cross-checked, exact match. Shading pass separately confirmed 0x04/0x46 (16-bit), matching SE-expected pairing pattern. | **Confirms the real vendor driver uses 8-bit for the image pass** — but this was already flagged as a **known non-issue** in earlier analysis (`8100-v2/IMPROVEMENTS.md`): pyopticfilm intentionally scans 16-bit for quality regardless of what the vendor driver does. No action needed unless byte-depth bookkeeping elsewhere assumes the vendor's 8-bit choice. |
| Cancel/park register sequence | inherited from shared `Gl128.home()`/`park()` | not directly studied | SE session-08 recipe: lamp strobe on 0x03 (`0x30→0x20→0x10→0x00→0x20→0x30→0x20→0x30`), then `0x01=0x22` (clear SCAN), then 0x101 walks `0xa5→0xad→0xec` | **YES, same shape.** `05_midtravel_home.pcapng` tail (frames 4551-4587): 0x03 writes in order `0x30,0x20,0x10,0x00,0x20,0x30,0x20,0x30` — the identical 8-value strobe sequence, same order — with `0x01=0x22` (frame 4559) interleaved after the 2nd strobe value rather than after all 8 (a minor ordering difference, not an address/value difference). Register 0x101 then walks `0xa5` (repeated, frames 4549-4781, motor-active/homing) → `0xad` (frames 4783/4785/4787) → `0xec` (frame 4789, final frame of capture). All four write frames (4551, 4559, 4565, 4587) and both boundary 0x101 reads (4549/4550 = 0xa5, 4789/4790 = 0xec) cross-checked via `tshark -x`, exact match. | **Confirms the SE cancel-recipe shape transfers to V2**, same three register addresses (0x01, 0x03, 0x101), same value semantics, same strobe-value sequence and same terminal status walk. Only difference: the SCAN-clear write (0x01=0x22) lands mid-strobe rather than after it — cosmetic, not structural. |
| ME exposure-bracket schedule | n/a | n/a | n/a | **Exactly 2 exposure brackets per DPI: baseline EXPOSURE=14000 (1×) and EXPOSURE=42000 (3×), an exact 3:1 ratio.** Seen identically at both DPIs in `07_multi_exposure.pcapng`: 1200dpi segment (frame 2977-3361 write EXPOSURE=14000; frame 10935-11319 write EXPOSURE=42000) and 7200dpi segment (frame 17027-19299 write EXPOSURE=14000; frame 37671-38055 write EXPOSURE=42000). Each bracket is a full independent dark-shading→white-shading→image-scan cycle (own DPISET/LPERIOD/LINCNT register blast), not a shared-calibration multi-pass. A fixed EXPOSURE=11000 "probe" value is written once at the very start of each bracket's shading cycle before settling to the bracket's real value — not itself a third bracket. Second, independent 7200dpi FEEDL2/LPERIOD data point (below) obtained as a side effect. Cross-checked via `tshark -x` at frames 19095 (FEEDL2) and 37851 (EXPOSURE=42000, `7d 00 7e a4 7f 10` = 0x00A410 = 42000), exact match. | Informs PR #53 / issue #50: ME is a clean 2-bracket 1×/3× exposure scheme, not N-bracket or gain-based. **Corroborates** (does not contradict) the FEEDL2=13128 and LPERIOD[7200]=16035 confirmations above — see those rows. |

**Extraction detail**: script `tools/extract_v2_registers.py`, ledger
`docs/hw-ref/8100v2/2026-09-recapture/ledgers/04_color_7200.registers.json` (2,000
register/preamble events + 7,643 bulk-metadata entries, `packet_index` = 1-based,
matches tshark `frame.number`). Every value above independently cross-checked against
raw `tshark -x` hex dumps of the cited frames — all matched exactly, all rated
**CONFIRMED** (not tentative).

**Bug noted in passing (not part of this reconciliation, flagged for separate fix)**:
`tools/scanlab/capture_pcap.py`'s `optical_snapshot()` reads register `0x34` for its
`"dummy"` field, but the real DUMMY register is `0x2B` (per `tools/register_reference.py`
and this session's own evidence). Appears to be dead/unused elsewhere in the file, so
low-severity, but misleading if anyone starts relying on it.

## `06_ppi_ladder` per-DPI register table

Segment boundaries re-derived from `REG_DPISET` (0x2C-0x2D) write values themselves, not from
NOTES.md's timing-gap-only assumption. **All 11 assumed DPI labels are confirmed** — DPISET
register value = `round(actual_dpi / 6)` for every segment at 720dpi and above (exact match,
no rounding needed since all those DPIs are multiples of 6); the three segments below 600dpi
(150/300/600) all program DPISET=100 — this is *not* a mismatch, it is the documented
"below 600dpi the ASIC is programmed like 600dpi, floors at 100" behavior, and all three
segments are register-for-register identical (same LPERIOD, LINCNT, bulk size) as a result —
consistent with the driver scanning those three requests at a common 600dpi-equivalent native
resolution. `image_*` columns are the register snapshot taken from the final ("image pass")
`0x2C/0x2D` write of each segment (the third of three: dark-shading, white-shading, image);
FEEDL2 is the second-feed write (0x3D-0x3F) that follows the constant 28292 first-feed write in
every segment. Every LINCNT/main-bulk-bytes pair below divides *exactly* (bulk ÷ LINCNT ÷ 3 =
integer, no remainder) — the "no ×4 factor" arithmetic proof from session 04 holds at all 11
points.

| Segment (assumed/confirmed DPI) | DPISET (reg) | LPERIOD (image) | DUMMY dark/white/image | STRPIXEL–ENDPIXEL | LINCNT (image) | FEEDL2 | Main image bulk (bytes) | bytes/line ÷ 3 (width, px) |
|---|---|---|---|---|---|---|---|---|
| Preview | 240 | 11369 | 5 / 2 / 2 | 242–10612 | 5804 | 13128 | 36,112,488 | 2074 |
| 150 | 100 (floored) | 11067 | 2 / 1 / 1 | 242–10610 | 2420 | 13128 | 6,272,640 | 864 |
| 300 | 100 (floored) | 11067 | 2 / 1 / 1 | 242–10610 | 2420 | 13128 | 6,272,640 | 864 |
| 600 | 100 (exact) | 11067 | 2 / 1 / 1 | 242–10610 | 2420 | 13128 | 6,272,640 | 864 |
| 720 | 120 | 11110 | 3 / 1 / 1 | 242–10602 | 2904 | 13128 | 9,025,632 | 1036 |
| 900 | 150 | 11175 | 3 / 1 / 1 | 242–10610 | 3628 | 13128 | 14,105,664 | 1296 |
| 1200 | 200 | 11283 | 4 / 2 / 2 | 242–10610 | 4836 | 13128 | 25,069,824 | 1728 |
| 1440 | 240 | 11369 | 5 / 2 / 2 | 242–10612 | 5804 | 13128 | 36,112,488 | 2074 |
| 1800 | 300 | 11499 | 6 / 2 / 2 | 242–10610 | 7252 | 13128 | 56,391,552 | 2592 |
| 2400 | 400 | 11715 | 8 / 3 / 3 | 242–10610 | 9668 | 13128 | 100,237,824 | 3456 |
| 3600 | 600 | 13443 | 12 / 8 / **4** | 242–10610 | 14500 | 13128 | 225,504,000 | 5184 |

**Anomaly flagged, UNCERTAIN significance**: at 3600dpi, the image-pass DUMMY (4) differs from
the white-shading-pass DUMMY (8) — at every other DPI in the ladder (150 through 2400 plus
preview) the white-shading and image-pass DUMMY values are identical. Raw write sequence at
3600dpi (frames 84951→85613→86637, values `0x0c`→`0x08`→`0x04`) is unambiguous, so this is not
an extraction artifact, but whether it's a real per-DPI-dependent rule or specific to the top of
this ladder is not established — flagged rather than asserted.

**Additional finding**: FEEDL2 is confirmed **DPI-independent** — all 11 segments (preview +
10 DPIs) show the identical value 13128 immediately after an identical first-feed of 28292, at
every resolution from 150dpi to 3600dpi. This directly extends the session-04/07 FEEDL2=13128
finding: it is a fixed physical positioning distance, not a function of scan resolution.
Representative cross-check: `06_ppi_ladder.pcapng` frame 65071/65769/66441 write sequence (1800
dpi) and frame 84947/86433/86637 (3600dpi) both confirmed via `tshark -x`.

Extraction detail: ledger `docs/hw-ref/8100v2/2026-09-recapture/ledgers/06_ppi_ladder.registers.json`
(9,482 register-write events, 297 buffer preambles, 30,200 bulk-metadata entries).

## Session 05 cancel-sequence citations (raw)

`05_midtravel_home.pcapng`, tail of capture (frames 4549-4790, last ~1.75s of a 10.5s capture):

| Frame | Reg write / read | Value | Cross-check |
|---|---|---|---|
| 4551 | 0x03 write | 0x30 | `tshark -x`: payload `03 30` — exact match |
| 4555 | 0x03 write | 0x20 | (same payload structure, not re-dumped individually) |
| 4559 | 0x01 write | 0x22 (SCAN clear) | `tshark -x`: payload `01 22` — exact match |
| 4565 | 0x03 write | 0x10 | `tshark -x`: payload `03 10` — exact match |
| 4569 | 0x03 write | 0x00 | — |
| 4575 | 0x03 write | 0x20 | — |
| 4579 | 0x03 write | 0x30 | — |
| 4583 | 0x03 write | 0x20 | — |
| 4587 | 0x03 write | 0x30 | `tshark -x`: payload `03 30` — exact match |
| 4549/4550 | 0x101 read | 0xa5 | `tshark -x`: read setup addr=0x101, completion bytes `a5 55` — exact match |
| 4783-4787 | 0x101 read (×3) | 0xad | — |
| 4789/4790 | 0x101 read | 0xec | `tshark -x`: read setup addr=0x101, completion bytes `ec 55` — exact match |

Extraction detail: ledger `docs/hw-ref/8100v2/2026-09-recapture/ledgers/05_midtravel_home.registers.json`
(862 register-write events, 1,034 register-read events, 27 buffer preambles).

## Session 07 multi-exposure bracket citations (raw)

`07_multi_exposure.pcapng`. Confirms 2 brackets per DPI (1× / 3× exposure), and gives a second,
fully independent corroboration of session 04's 7200dpi FEEDL2=13128 and LPERIOD=16035:

| DPI | Bracket | EXPOSURE write (frame, value) | LPERIOD (image, frame) | LINCNT (image, frame) | FEEDL2 (frame) | Main image bulk |
|---|---|---|---|---|---|---|
| 1200 | 1 (1×) | 2977/3155/3361 = 14000 | — | — | — | — |
| 1200 | 2 (3×) | 10935/11115/11319 = 42000 | — | — | — | — |
| 7200 | 1 (1×) | 17027/18915/19095/19299 = 14000 | 17745 = 16035 | 19299 = 29012 | 19095 = 13128 | 902,389,248 |
| 7200 | 2 (3×) | 35785/37671/37851/38055 = 42000 | 36509 = 16035 | 38055 = 29012 | 37851 = 13128 | 902,389,248 |

All four highlighted 7200dpi cells (LPERIOD=16035 at frame 17745, FEEDL2=13128 at frame 19095,
LINCNT=29012 at frame 19299, EXPOSURE=42000 at frame 37851) cross-checked via `tshark -x` hex
dump, exact match on every one. Note both 7200dpi brackets produce byte-identical main image
bulk sizes (902,389,248 — the exact same number session 04 reported), confirming the
"no ×4 factor" LINCNT arithmetic (902,389,248 = 29012 × 31104) holds identically in a second,
independent capture, twice.

Extraction detail: ledger `docs/hw-ref/8100v2/2026-09-recapture/ledgers/07_multi_exposure.registers.json`
(3,448 register-write events, 108 buffer preambles, 19,822 bulk-metadata entries).

## Extraction log

_Populated as `tools/extract_v2_registers.py` produces per-session ledgers. Each entry
records: session, output ledger path, extraction date, cross-check method used._

| Session | Ledger | Date | Cross-check method |
|---|---|---|---|
| `04_color_7200` | `ledgers/04_color_7200.registers.json` | 2026-09 (prior pass) | Manual `tshark -x` hex dump of every cited frame |
| `05_midtravel_home` | `ledgers/05_midtravel_home.registers.json` | 2026-09-06 | Manual `tshark -x` hex dump of cancel-sequence write/read frames (see Session 05 citations table above) |
| `06_ppi_ladder` | `ledgers/06_ppi_ladder.registers.json` | 2026-09-06 | DPISET-derived segmentation (not timing-gap assumption) + manual `tshark -x` hex dump of representative frames per DPI (see per-DPI table above) |
| `07_multi_exposure` | `ledgers/07_multi_exposure.registers.json` | 2026-09-06 | Manual `tshark -x` hex dump of all bracket-boundary EXPOSURE/LPERIOD/LINCNT/FEEDL2 frames (see Session 07 citations table above) |
