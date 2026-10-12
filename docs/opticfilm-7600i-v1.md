# OpticFilm 7600i v1 (GL843)

`07b3:0c3b`, bcdDevice `0x0400`. Capture-derived: scans adapt SilverFast's recorded job sequences
from USB captures of a real 7600i v1 (sequences and findings from
[OpenOptic](https://github.com/bazfp/OpenOptic), where they run on this hardware). The 7600i v2
(bcdDevice `0x0605`, GL845) is unaffected.

| | |
|---|---|
| Colour | 1440, 3600, 7200 dpi full frame, 16-bit linear RGB |
| Sampling | Nominal vertical resolution, one dummy CCD line at 7200 dpi (zero at lower resolutions), two-line buffer threshold and backtracking disabled |
| Area | 36.61 × 24.72 mm: sensor pixels 80–10458 (3600 dpi: 82–10458; 1440 dpi: 83–10463), the full area. SilverFast's window is 210–10458 (36.15 mm). The pixel count must stay even: with an odd count the scanner sends a pixel less per line and the read stalls |
| Infrared | 3600, 7200 dpi (`mode="infrared"`, or `infrared=True` with colour) |
| Crop | `area=`, on the host; acquisition time and peak memory still reflect the full frame |
| Not yet | multi-exposure, multi-pass, manual exposure, other resolutions, grey |

Differences from the SANE GL843 path:

- Register reads set the address (`0x83`, one byte) then read `0x84`; the address auto-increments
  after each read. Every write is acknowledged by polling `0x8E`/`0x20` bit 0.
- Boot and homing use capture-derived register programs and motor tables. Scan timing, window
  and calibration are adapted as described above and below. The first positioning move stops
  after the captured interval (2.56–2.57 s), rather than a live position event; host scheduling
  delays can affect its endpoint. Repeated positioning across devices needs hardware validation.
- Raw frames arrive mirrored with fractional R/G/B row delays. Captured jobs sample at twice
  the nominal vertical resolution; defaults sample once. At 7200 dpi the column stagger is
  eight raw lines in captured jobs, four with single sampling. Infrared uses the red row.
- Infrared at 7200 dpi was not captured: it is the 7200 dpi colour job with the changes SilverFast
  makes for infrared at 3600 dpi (lamp off, infrared LED, AFE), and shading computed from the job's
  white frame as SilverFast does (`0x13000 × 0x2000 / white`).
- With `apply_calib=True` (default), live probes calibrate each scan: four-pixel peak means select AFE gains;
  32 black pixels determine offsets; 128-line references discard eight extremes at each end.
  Dark correction uses a forward 100-value mean (separate column parities at 7200 dpi).
  Both shading uploads cover the actual window, including added pixels and infrared hot columns.

Calibration runs inside each scan; `scanner.calibrate()` is unsupported and no cached calibration
is used. `apply_calib=False` keeps captured AFE values and recorded shading where available;
widened windows and derived 7200 dpi IR still recompute final gains from the white frame.
For recorded sampling and timing, use a `Model7600iV1` with `single_sample=False` and `dummy_lines="recorded"`; the
window and live calibration remain independent settings. These are model/session options,
not additional `Scanner.scan()` arguments.

Validation: strict playback tests cover the four captured profiles (1440/3600/7200 dpi colour,
3600 dpi IR). Derived 7200 dpi IR is checked structurally, not against a capture. Separate tests
cover calibration substitution, assembly, homing, upload failure and cancellation, with simulated
end-to-end scans. Playback does not establish optical accuracy of the transformed defaults.

Live calibration was derived from eight captured passes. The dark outlier gate uses inclusive
64; captures do not uniquely identify the threshold. Final colour gain increments remain
0/+1/+2 as captured. Optical verification on hardware is still needed. Full-frame colour plus
IR retains both three-channel frames during processing, increasing peak memory use.

Data: `device/data/opticfilm_7600i_v1.json.gz`, rebuilt with
`python tools/import_7600i_v1_profiles.py /path/to/OpenOptic`.
