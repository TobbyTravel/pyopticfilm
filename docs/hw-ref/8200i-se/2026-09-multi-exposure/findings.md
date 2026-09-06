# 8200i SE `14_multi_exposure_scans` — register-level findings (2026-09-06)

Source: `jboneng/pyopticfilm_captures/8200i-se/14_multi_exposure_scans/` (six
captures, 1800 dpi, SilverFast, 2026-08-28). Ledgers extracted with
`tools/extract_v2_registers.py` (no product-ID filtering — reused verbatim,
not V2-specific despite the name) into `ledgers/*.registers.json` alongside
this file. Cross-referenced against the 8100 V2 `07_multi_exposure` findings
already in `docs/hw-ref/8100v2/2026-09-recapture/claims-reconciliation.md`
(commit `24336a0`).

This investigation was scoped by [jboneng/pyopticfilm#52](https://github.com/jboneng/pyopticfilm/pull/52)
(N-bracket ME, draft — real-hardware faults reported on SE at 7200ppi/3-bracket
and low-ppi first-bracket) and [#50](https://github.com/jboneng/pyopticfilm/issues/50)
(V2-only color-balance shift near the N-bracket exposure ceiling).

## Headline finding: the vendor driver's ME is identical on both models, at every DPI checked

`1800ppi_Scan_No_IR_ME.pcapng`'s `REG_EXPOSURE` (`0x7D-0x7F`) write sequence
(packet indices from the ledger, matching `tshark`/Wireshark `frame.number`):

| Phase | Packet | 24-bit value |
|---|---|---|
| Bracket 1 dark-shading probe | 533 | 11000 |
| Bracket 1 settled (dark+white+image, all of bracket 1) | 743 | **14000** |
| Bracket 2 dark-shading probe | 11939 | 11000 |
| Bracket 2 dark-shading settled | 12091 | 14000 |
| Bracket 2 white-shading + image settled | 14759 | **42000** |

Exactly **2 brackets, EXPOSURE=14000 then 42000, a 3:1 ratio** — byte-identical
to what the 8100 V2's `07_multi_exposure` capture showed at 1200 and 7200 dpi.
This is now confirmed at **1800 dpi on the SE** too, i.e. **the real vendor
driver does not vary the ME exposure scheme by DPI or by model** — it is a
flat, fixed 14000/42000 scheme everywhere it's been captured, on both ASICs.

**Confirms the framing already used for the V2 side**: `n_brackets > 2` and
adaptive (content-driven) per-frame exposure selection have **no vendor
precedent on either model** — both are pyopticfilm inventions built on top of
a vendor scheme that itself never adapts or exceeds 2 brackets.

Note for calibration code: bracket 2's *dark-shading* phase briefly settles at
the baseline 14000 (packet 12091) before the white-shading/image portion of
that same bracket switches to 42000 (packet 14759) — dark shading appears to
always run at the flat baseline exposure, not at the bracket's own target.
Not investigated further here; flagging in case it matters for a future
per-bracket calibration audit.

## Pixel clock (`0xA5`/`0xAB`) split re-confirmed independently

`gl128_common.py`'s `PIXEL_CLOCK_LONG_BY_DPI` comment claims "session 14:
slower clock at 1440/1800" — re-derived from scratch here rather than trusted:

| Bracket | Packet (settled) | `0xA5` | `0xAB` |
|---|---|---|---|
| 1 (short, EXPOSURE=14000) | 3035 | 2 | 2 |
| 2 (long, EXPOSURE=42000) | 14759 | 1 | 1 |

Matches `PIXEL_CLOCK_BY_DPI[1800] = 0x02` and `PIXEL_CLOCK_LONG_BY_DPI[1800] =
0x01` exactly. **Confirmed, not just inherited from an old comment.**

## Each bracket is a full independent shading→image cycle (matches V2)

`LINCNT` (`0x25-0x27`) traces two complete small→large ramps (dark shading →
white shading → full image, LINCNT settling at 6772 both times), one per
bracket, at packet ranges ~529-4103 and ~11935-15163. Same "no shared
calibration between brackets" pattern already documented for V2 in
`claims-reconciliation.md`.

## IR + ME interaction (`1800ppi_Scan_IR_ME.pcapng`) — SE-only, no V2 equivalent

With IR enabled, the `REG_EXPOSURE` sequence gains one extra
probe→settle-at-14000 cycle in the middle (packet 22573→22723) before the
final 42000 settle at 25389 — i.e. **three acquisitions in IR+ME mode: IR,
color-short, color-long**, each with its own dark-shading cycle, vs. two
without IR. No evidence of any exposure/pixel-clock value change caused by IR
itself; this matches `session_gl128.py`'s architecture (IR is an "early" pass
alongside color-short, before the ME long bracket runs). No anomaly found.

## `me_long_exposure_ceiling_default = 85000` (SE, non-7200-dpi) — not contradicted

`Model8200iSE`'s comment only claims 7200 dpi's `42000` as vendor-derived; the
`85000` default for other DPIs was never claimed as a vendor value, so this
capture's 1800dpi EXPOSURE=42000 doesn't contradict shipped code — it's new
corroborating data (vendor uses the same 42000 figure at 1800dpi too, not
something scaled toward 85000), not a bug fix.

## What this does and doesn't validate for PR #52 / issue #50

**Validated (both models, now cross-confirmed)**: the 2-bracket 14000/42000
scheme, the short/long pixel-clock split at 1800dpi, and the
per-bracket-is-a-full-cycle shading pattern that pyopticfilm's own
`_acquire_pass(remeasure=True)` loop already replicates.

**Not validated by any capture, on either model**: `n_brackets > 2`, adaptive
per-frame exposure selection, and — the specific hypothesis worth testing on
real V2 hardware per the parent plan — whether an N-bracket schedule's
intermediate exposures (much closer to 14000 than 42000) should really get
the same "long" pixel-clock treatment (`0xA5/0xAB = 1/1`) that's only ever
been observed paired with the full 42000 exposure. That requires the Phase 3
real-hardware trial (Scan Lab Forensic tab recording), not more capture
analysis — the vendor never generates that traffic shape on either scanner.
