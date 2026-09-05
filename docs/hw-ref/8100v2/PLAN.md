# 8100 V2 capture analysis — plan

Working branch: `hw-ref/8100v2-capture-analysis` in `/c/dev/pyopticfilm-src`
(clone of `jboneng/pyopticfilm`). Captures: `C:\dev\morecapture\{1,2,3,4,6,7,
8,9,10,11}.pcapng` (the reference driver, autocrop on, Windows, USBPcap). **File 5 is
missing** — proceeding without it; the 1200dpi/exposure-2.0 comparison leg in
Phase 3 is deferred until supplied.

Read this file and `PROGRESS.md` at the start of every session and after any
context compaction — they are how this survives resets.

## Ground rules (from mission brief, do not relax)

- Never decode/print image bulk payloads; no hex dumps >64 bytes of bulk IN.
- All analysis goes through scripts that write JSON/CSV/MD to disk; read only
  summaries. Cap printed tables at ~40 rows, full version in a file.
- Bulk image data: count/histogram/timing only, never inspected.
- Findings separate observation / interpretation / recommendation, never
  state a hypothesis as fact. Disagreeing captures: record both.
- Draft PR/issue text to `drafts/`, stop and ask before pushing/posting.
- No hardware experiments without explicit ask-first.
- Small, reviewable PRs over one big one.

## Known deviation from the mission brief's assumed starting state

The mission brief assumed the existing 8100 V2 constants (PR #30) and the SE
tables they inherit were derived from *these* captures or at least from
the reference driver. They are not: PR #30 cites a different capture file
(`04_color_7200.pcapng`) not present in our dataset, and the inherited SE
tables are explicitly the SE model's original reference driver-derived. See `claims-inventory.md`
provenance warning. This makes Phase 3 more valuable, not less — we are
independently re-deriving V2 register values from a *different* capture
software (the reference driver) than what produced the current tables, which is a
stronger validation than a same-software repeat would be.

## Phases

**Phase 0 — Orientation.** Done (this session). Produced
`claims-inventory.md` (38 claims C-001..C-038, 3 data gaps G-001..G-003),
this file, and the first `PROGRESS.md` entry.

**Phase 1 — Capture inventory and tooling check.**
1. Per-file packet/duration/transfer-type counts, bulk-IN size histogram,
   VALUE_BUFFER preamble list (wIndex, announced size) → `capture-inventory.md` + JSON.
2. Run existing `tools/scanlab/capture_pcap.py` against each file (parse only,
   no image decode); log where it succeeds/misclassifies/fails.
3. Decide extend-vs-rewrite for a timestamp-preserving, chronological event
   ledger tool (`tools/capture_ledger.py` or extend `capture_pcap.py`).
   Requirements: keep pcap timestamps, decode Genesys control requests,
   register writes/reads, status polls, bulk-IN as size-only, buffer
   preambles; support a register-snapshot-at-packet-index export. Test against
   a tiny synthetic capture.
4. **Derive DPISET (register 0x2C, per C-010) from each of files 6-11
   directly — do not trust readme.txt's dpi labels for these files** (user
   has flagged the mapping may be wrong; file 11 is confirmed 7200dpi by the
   user despite its "3600dpi" label). Re-map files 6-11 to actual dpi before
   any per-DPI table is built.
5. Determine the reference driver's image framing vs the SE model's original reference driver assumption (C-025):
   preamble wIndex, announced vs actual bytes, URB size per DPI, whether 7200
   is one-URB-per-line. Record as findings regardless of outcome.

**Phase 2 — Phase segmentation.** Per-capture timeline (enumeration → init →
lamp → home → shading → prescan? → image pass(es) → park → close) in
`phases/<n>.md` (~80 lines each). Captures 1/2 answer "what does the reference driver do
on open/power-on beyond enumeration". Capture 3 answers "what happens between
3 passes" (direct #33 evidence). Determine whether autocrop adds a prescan
pass in every capture.

**Phase 3 — Register program per DPI + claim verification.** Register
snapshot at image-pass-start for each resolved DPI (600/1200/1800/2400/3600/
7200) → `register-program-by-dpi.md`. Cross-check against
`compute_geometry()` and model tables; update `claims-inventory.md` statuses;
each contradiction → a finding + candidate PR item. Resolve the capture-11
7200-vs-3600 question from DPISET directly. Exposure diff limited to captures
3 (unlocked)/4 (locked 1.0)/6 (unlocked, 1200) — capture 5 (locked 2.0)
deferred (G-001).

**Phase 4 — Issue-targeted diffs.** `issue-33-diff.md` (positioning-feed
sequence from capture 3 + single-pass captures, vs pyopticfilm's feed/home/
prime path) and `issue-35-diff.md` (7200dpi the reference driver program vs pyopticfilm's
Python trace via fake-USB tooling, ranked candidate causes). Each ends with a
draft issue comment in `drafts/`.

**Phase 5 — Reference doc, fixtures, PRs.** Assemble
`docs/hardware-reference-8100v2.md`. Register-only golden traces (JSON, no
image bytes) for ≥1200 and 7200 into `tests/traces/` (ask before adding large
files). Split PRs: (a) tooling, (b) doc+fixtures, (c) constant corrections —
drafts to `drafts/`, ask before pushing/opening/posting.

## Definition of done for this session

- `PLAN.md`, `PROGRESS.md`, `claims-inventory.md`, `capture-inventory.md`
  exist and are current.
- Phase 1 complete, Phase 2 started.
- Top-5 open questions for a future capture/hardware session recorded in
  `PROGRESS.md`.
