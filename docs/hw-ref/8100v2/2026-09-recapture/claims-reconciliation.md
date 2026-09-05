# 8100 V2 claims reconciliation — 2026-09 recapture

Three-way reconciliation of 8100 V2 / GL128 register and behavior claims against a
**third, independent capture set**: `TobbyTravel/pyopticfilm_captures`, branch
`add-8100-v2-captures`, 7 sessions captured 2026-09-05, integrity-verified via `tshark`
(frame counts, transfer-type/size distributions, timing segmentation) but not yet
register-decoded as of this document's creation.

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
| `feed_to_scan_steps` / FEEDL2 (0x3D-0x3F), full-frame 7200dpi | 13128 | ~13486 (C-001; 13128 seen only on one anomalous first-cycle acquisition) | 13704 (SE `feed_to_scan_steps`) | _pending (session 04, 06)_ | _pending_ |
| `lperiod_by_dpi[7200]` (0x28-0x2A) | 16035 | 15914 and 15999 (two captures disagree with each other) | 15963 | _pending (session 04)_ | _pending_ |
| Shading dummy (0x2B), white strip @ 7200dpi | 0x10 | ~15/16 (C-012; note 0x10=16 decimal — may not actually conflict, needs precise resolution) | 0x17 (SE-computed) | _pending (session 04)_ | _pending_ |
| Shading dummy (0x2B), dark strip @ 7200dpi | 0x17 | _pending re-check_ | 0x04 (SE) | _pending (session 04)_ | _pending_ |
| LINCNT (0x25-0x27) semantics / `image_lincnt_per_line` | inherits SE ×4/half-lines convention | possibly V2 uses output-line-count directly (C-013) | ×4, half-lines (SE) | _pending (session 04, 06, 07)_ | _pending — architectural, see Phase 4.3_ |
| DEPTH_A/DEPTH_B (0x33/0xAF) pairing | not programmed | mixed pairing observed in real driver (C-014) | 16-bit shading / 8-bit image pair | _pending (session 04)_ | _pending_ |
| Cancel/park register sequence | inherited from shared `Gl128.home()`/`park()` | not directly studied | SE session-08 recipe (structural template only) | _pending (session 05)_ | _pending_ |
| ME exposure-bracket schedule | n/a | n/a | n/a | _pending (session 07)_ | _pending — informs PR #53 / issue #50_ |

## Extraction log

_Populated as `tools/extract_v2_registers.py` produces per-session ledgers. Each entry
records: session, output ledger path, extraction date, cross-check method used._

(none yet)
