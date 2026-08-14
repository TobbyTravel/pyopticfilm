# SANE genesys ↔ pyopticfilm OpticFilm map

This document maps every OpticFilm model in pyopticfilm to the SANE `genesys`
backend. Scan / home / park / calibrate remain **gated** (`scan_ready=False`)
until a model is **hardware-tested**. Protocol-validated traces (see
[scanner-validation.md](scanner-validation.md)) do not flip that gate. The
8200i SE is capture-derived and is not completed from SANE.

## Model → ASIC → SANE sources

| pyopticfilm model | USB ID | ASIC | SANE sensor id | Command set |
|-------------------|--------|------|----------------|-------------|
| OpticFilm 8200i SE | `07b3:1825` | GL128 | *(none)* | capture / `Gl128` |
| OpticFilm 8200i | `07b3:130d` | GL845 | `CCD_PLUSTEK_OPTICFILM_8200I` | `CommandSetGl846` (`gl846.cpp`) |
| OpticFilm 8100 | `07b3:130c` | GL845 | same sensor as 7400/8200i family | `gl846.cpp` |
| OpticFilm 7600i v2 | `07b3:0c3b` bcd `0x0605` | GL845 | alias of 8200i tables | `gl846.cpp` |
| OpticFilm 7400 v2 | `07b3:0c3a` bcd `0x0605` | GL845 | `CCD_PLUSTEK_OPTICFILM_7400` | `gl846.cpp` |
| OpticFilm 7500i | `07b3:0c13` | GL843 | `CCD_PLUSTEK_OPTICFILM_7500I` | `gl843.cpp` |
| OpticFilm 7600i v1 | `07b3:0c3b` bcd `0x0400` | GL843 | same as 7500i | `gl843.cpp` |
| OpticFilm 7300 | `07b3:0c12` | GL843 | `CCD_PLUSTEK_OPTICFILM_7300` | `gl843.cpp` |
| OpticFilm 7400 v1 | `07b3:0c3a` bcd `0x0400` | GL843 | 7300 tables | `gl843.cpp` |
| OpticFilm 7200i | `07b3:0c04` | GL843 | `CCD_PLUSTEK_OPTICFILM_7200I` | `gl843.cpp` |
| OpticFilm 7200 v2 | `07b3:0c07` | GL843 | 7200i tables, no IR | `gl843.cpp` |
| OpticFilm 7200 | `07b3:0807` | GL842 | `CCD_PLUSTEK_OPTICFILM_7200` | `gl842.cpp` |

## pyopticfilm landing sites

| Concern | Module |
|---------|--------|
| USB vendor I/O | `src/pyopticfilm/usb/protocol.py` |
| Model tables | `src/pyopticfilm/device/model_*.py` |
| DPI / exposure / dummy lookup | `src/pyopticfilm/device/sensor_lookup.py` |
| GL845/GL846 scan session | `src/pyopticfilm/scan/session.py` |
| GL843 scan session | `src/pyopticfilm/scan/session_gl843.py` |
| GL842 scan session | `src/pyopticfilm/scan/session_gl842.py` |
| GL128 (SE) scan session | `src/pyopticfilm/scan/session_gl128.py` |
| Host dark/white calib | `src/pyopticfilm/scan/calibrate.py` (non-GL128 branch) |
| ASIC DVDSET shading | `src/pyopticfilm/scan/calib_gl128.py` (**SE only**) |

## MAXWD unit differences (SANE)

These matter when sizing the ASIC line buffer:

- **GL845 / GL846**: `(line_bytes * channels) >> 2` (4-word units; genesys still multiplies by channels — preserved as-is)
- **GL843**: `(line_bytes * optical_res / full_res) >> 1` (2-word units). For OpticFilm CCD entries where optical≈full, this is `line_bytes >> 1`
- **GL842** (CCD): `line_bytes` (CIS would multiply by channels; OpticFilm 7200 is CCD)

## Intentionally not ported from SANE

- Gamma LUT upload
- Grayscale / lineart modes
- Sheet-fed document detect
- Non-Plustek genesys scanners
- Any GL128 / 8200i SE behaviour (use captures instead)

## Bring-up policy

1. Keep `scan_ready=False` (enforced by `tests/test_multi_model.py::test_scan_ready_se_only`).
2. Hardwareless protocol traces may be added per [scanner-validation.md](scanner-validation.md); they change the documented support level, not `scan_ready`.
3. On hardware: capture home + one 1800 dpi colour strip; compare register block to the ported session.
4. Flip **that** model’s `scan_ready` only after a successful image + park.
