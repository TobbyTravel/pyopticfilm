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
geometry with the wrong factor. This is Phase 4.3's headline item: needs corroboration
from sessions 06/07, then a careful fix (promote these fields from
`GL128_SHARED_FIELDS` to `GL128_DIVERGENT_FIELDS`) with the full Phase 5 SE-regression
process, since the code path is shared with the 8200i SE. **Tracked in PR #13's
checklist — must not be dropped before Phase 4 lands.**

**Status: skeleton — no verdicts recorded yet.** This document's "Our 2026-09-05 value"
column is populated by `tools/extract_v2_registers.py` (Phase 1) plus manual
cross-verification; no row should carry a Verdict until that column has an exact
file/frame/packet citation.

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
| `feed_to_scan_steps` / FEEDL2 (0x3D-0x3F), full-frame 7200dpi | 13128 | ~13486 (C-001; 13128 seen only on one anomalous first-cycle acquisition) | 13704 (SE `feed_to_scan_steps`) | **13128** — frame 3015 of `04_color_7200.pcapng`, payload `3d 00 3e 33 3f 48` = 0x003348. Cross-checked via manual `tshark -x` hex dump, exact match. | **Confirms PR #30.** Contradicts Aug-30's ~13486. Single-session result — see note below re: needs corroboration from session 06/07 repeats before ruling out "first-cycle-only" confound in the *other* direction. |
| `lperiod_by_dpi[7200]` (0x28-0x2A) | 16035 | 15914 and 15999 (two captures disagree with each other) | 15963 | **16035** — settles at frame 1673, reaffirmed unchanged at frame 3219 (image-pass register blast). Cross-checked, exact match. | **Confirms PR #30.** Contradicts both Aug-30 values. |
| Shading dummy (0x2B), white strip @ 7200dpi | 0x10 | ~15/16 (C-012) | 0x17 (SE-computed) | **0x10 (16)** — frame 2269. Cross-checked, exact match. | **Confirms PR #30.** Aug-30's "~15/16" is the *same value* as 0x10=16 decimal — not actually a conflict, just a hex/decimal mismatch in how it was reported. |
| Shading dummy (0x2B), dark strip @ 7200dpi | 0x17 | _not separately re-checked in Aug-30_ | 0x04 (SE) | **0x17 (23)** — frame 1247. Cross-checked, exact match. | **Confirms PR #30.** |
| LINCNT (0x25-0x27) semantics / `image_lincnt_per_line` | inherits SE ×4/half-lines convention | V2 uses output-line-count directly (C-013) | ×4, half-lines (SE) | **LINCNT=29012 used directly as output line count, no ×4/half-lines factor.** Exact-integer proof: bulk size 902,389,248 = 29012 × 31104 (31104 = width 10368 × 3 channels × 1 byte/sample from STRPIXEL/ENDPIXEL span); 902,389,248 ÷ 124,416-byte URB = 7253 exactly (matches NOTES.md's own URB count); 124,416 ÷ 31,104 = 4 lines/URB exactly. | **CONFIRMS Aug-30's C-013, contradicts the inherited SE convention.** This is a real architectural bug if pyopticfilm's V2 decode path assumes the SE ×4 convention — see code-impact check below. Highest-priority finding from this session. |
| DEPTH_A/DEPTH_B (0x33/0xAF) pairing, image-scan pass | not programmed (pyopticfilm chooses 16-bit throughout) | mixed pairing observed in real driver (C-014) | 16-bit shading / 8-bit image pair | **0x1F / 0xFF** (8-bit) — frames 2755 and 3015. Cross-checked, exact match. Shading pass separately confirmed 0x04/0x46 (16-bit), matching SE-expected pairing pattern. | **Confirms the real vendor driver uses 8-bit for the image pass** — but this was already flagged as a **known non-issue** in earlier analysis (`8100-v2/IMPROVEMENTS.md`): pyopticfilm intentionally scans 16-bit for quality regardless of what the vendor driver does. No action needed unless byte-depth bookkeeping elsewhere assumes the vendor's 8-bit choice. |
| Cancel/park register sequence | inherited from shared `Gl128.home()`/`park()` | not directly studied | SE session-08 recipe (structural template only) | _pending (session 05)_ | _pending_ |
| ME exposure-bracket schedule | n/a | n/a | n/a | _pending (session 07 — also gives a second independent 7200dpi FEEDL2/LPERIOD data point)_ | _pending — informs PR #53 / issue #50, and corroborates/contradicts the FEEDL2/LPERIOD confirmations above_ |

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

## Extraction log

_Populated as `tools/extract_v2_registers.py` produces per-session ledgers. Each entry
records: session, output ledger path, extraction date, cross-check method used._

(none yet)
