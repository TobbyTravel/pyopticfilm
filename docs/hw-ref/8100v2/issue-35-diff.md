# Issue #35 diff — 7200dpi jagged/stepped lines

Compares pyopticfilm's own register program for a 7200dpi V2 image pass
(generated via `tools/trace_8100v2_python.py`, mock-hardware trace, no real
device touched) against the captured register program from our reference
driver's 7200dpi captures (files 3, 4, 11 — `register-program-by-dpi.md`,
`findings.md` F-009/F-010/F-012/F-013). Differences are ranked by
plausibility as a cause of per-line stepping, most first. Observation,
interpretation, and recommendation are kept separate throughout.

## What's confirmed to NOT differ (ruled out as a direct cause)

| register / behavior | captured (reference driver, 7200dpi) | pyopticfilm (mock trace) | match? |
|---|---|---|---|
| Motor mode (reg 0x02) | `0x30` = MTRPWR\|AGOHOME | `0x30` = MTRPWR\|AGOHOME | yes |
| Lamp (reg 0x03) | `0x30` = XPASEL\|LAMPPWR | `0x30` = XPASEL\|LAMPPWR | yes |
| Pixel clock (0xA5/0xAB) | `1/1` | `1/1` | yes |
| DPISET | `1200` (=7200/6) | `1200` | yes |
| EXPOSURE (0x7D) | `14000` | `14000` | yes |
| Priming / quiet-drain toggles | n/a (reference driver has no such setting) | on by default | ruled out per issue #35's own text — user already toggled both off/on on real hardware with no change |

None of these differ, and priming/quiet-drain are already ruled out by the
issue reporter's own hardware testing. They are not revisited here.

## Ranked candidate differences

### 1. `REG_EXPOSURE`-adjacent `LPERIOD` value (F-009) — highest plausibility, 7200-specific

**Observation:** captured LPERIOD at 7200dpi is 15914 (file 4) or 15999
(file 11); pyopticfilm's own trace uses the model's table value, 16035 —
which itself doesn't match either capture (see F-009). LPERIOD is the
per-line pixel-clock budget; a wrong value changes per-line timing
specifically, which is a direct, mechanistic route to a per-line visual
artifact (stepped/jagged lines), and the deviation is 7200dpi-specific in
this dataset (600-3600dpi LPERIOD deviations were 2-18 units — noise-sized;
the two 7200dpi captures deviate by up to 121 units from the model, and 85
units from each other).

**Interpretation:** this is the strongest match to issue #35's own
description ("7200dpi only... probably not a hardware/microstepping
ceiling, more likely something in this driver's 7200dpi path
specifically"). Not proven — F-009's own open question (is LPERIOD
crop-dependent?) needs resolving first, since files 4 and 11 have different
crops (default vs. full-sensor) as well as different LPERIOD.

**Recommendation:** safe to experiment with — try LPERIOD values in the
15900-16000 range (matching the captures) instead of the model's 16035, on
real V2 hardware at 7200dpi, and check whether jaggedness changes. Low risk
(a timing register, not a motor/geometry one).

### 2. Dummy register (0x2B) at 7200dpi (F-013) — high plausibility, 7200-specific

**Observation:** captured dummy is 15-16 at 7200dpi; the model's table
value (inherited unmodified from the SE) is 23 (0x17) — the one DPI where
the SE table jumps far above every other entry (1-4 elsewhere). Every other
DPI's captured dummy matches the table exactly.

**Interpretation:** dummy clocks pad the per-line pixel-clock budget at the
sensor boundary; a wrong value at exactly the DPI where the table is an
outlier (and where the symptom is exclusively reported) is a plausible,
7200-specific mechanism, though unproven.

**Recommendation:** safe to experiment with — try dummy=15 or 16 instead of
23 at 7200dpi on real hardware. Low risk (same register family as #1).

### 3. `REG_DEPTH_A` (0x33) mixed pair (F-013) — moderate plausibility, NOT 7200-specific

**Observation:** captured image passes at every DPI (600 through 7200, not
just 7200) write DEPTH_A=0x04 (a DEPTH16 value) paired with DEPTH_B=0xFF
(DEPTH8), a combination pyopticfilm never programs (it always pairs
DEPTH8_A/DEPTH8_B or DEPTH16_A/DEPTH16_B together).

**Interpretation:** present at every DPI, not just 7200 — so on its own it
cannot explain a 7200-only symptom. It's included because it's a genuine,
newly-discovered protocol difference (not previously documented anywhere)
and could still interact with something DPI-specific (e.g. if the ASIC's
internal packing behavior under this mixed pair scales differently at
higher pixel-clock rates). Ranked below #1/#2 specifically because it fails
the "why only 7200" test the issue itself uses to rule out priming/drain.

**Recommendation:** worth correcting for protocol accuracy regardless
(`_configure()` should program DEPTH_A=0x04 during the image pass, not
0x1F), but treat as a secondary experiment for #35 specifically — try it
only after #1 and #2 are tested, and note whether it has any effect even at
600/1200dpi (where the issue reporter says there's no visible jaggedness) to
see if it's truly inert there.

### 4. Image-pass motor slope table content — untested, not directly comparable from captures

**Observation:** issue #35 itself flags `image_slope_slow` as "not yet
tried." pyopticfilm's default (`image_slope_slow=False`) uploads
`SLOPE_TABLE_FAST` content to both `AHB_SLOPE_SCAN` and `AHB_SLOPE_FAST`
before an image pass (`gl128.py` `upload_tables`). The captures show AHB
upload preambles (`wIndex=0x01`) at the expected points in the sequence
(17 per single-pass capture — see `capture-inventory.md`), but their
*content* cannot be inspected under this project's constraints (never
decode/print bulk payload data), so the captures can only confirm *that*
an upload happens at the right point, not *what* slope profile it contains.

**Recommendation:** this is the one candidate that genuinely requires a
hardware experiment rather than further capture analysis — exactly what
issue #35 already identifies as the next untested knob
(`image_slope_slow=True`).

## What this diff cannot show

- Slope table *content* (by design — never decoded).
- Whether LPERIOD is genuinely crop-dependent (F-009's open question) —
  needed to know whether candidate #1's fix is a single constant or an
  area-dependent formula.

## Update: bulk-IN read cadence checked (F-015)

`capture_ledger.py` now supports opt-in per-packet bulk-IN timing
(`--bulk-timing`). Checked on file 4 (7200dpi): during the actual image
pass, cadence is smooth — 6796 chunks, inter-chunk interval mean/median
11.2ms, min 4.16ms, max 18.27ms, zero gaps over 50ms. No stalls or
irregular pacing that would obviously explain the jaggedness on its own.
This makes a host-side USB read-pacing explanation less likely and
reinforces the register-level candidates (#1 LPERIOD, #2 dummy) above —
not proof, since a per-line register misconfiguration could still produce
visible artifacts within an otherwise perfectly-paced transfer. Only
checked on one 7200dpi capture (file 4) so far; not cross-checked against
file 11 (full-sensor) or lower DPIs.

---

## Draft issue comment (`drafts/issue-35-comment.md` has the postable version)

Posted 2026-08-30: https://github.com/jboneng/pyopticfilm/issues/35#issuecomment-5464019792
