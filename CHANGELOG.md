# Changelog

All notable changes to pyopticfilm are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.2] - 2026-08-22

### Added

- **Multi-exposure (ME) scanning** for OpticFilm 8200i SE (GL128): short and long colour passes with host SNR/IVW merge into the deliverable `ScanImage.rgb`.
- **`MeScanDebug`** and **`Scanner.last_me_debug`** for lab-only bracket planes, fusion stats, and pass alignment shifts (not part of the public `ScanImage` type).
- **Scan Lab** (`tools/scanlab/`): PyQt6 bring-up GUI (mock or real SE), prescan crop, IR pass, ME tabs, USB log, pcap decode, calib cache controls, and adaptive quiet-drain toggle.
- **Adaptive quiet USB drain** default on GL128 (`DEFAULT_IMAGE_USB_PACE_S`, line-aligned throttle in bulk acquire).
- **`tools/audit_me_bracket`**: bracket TIFF audit; **`--align`** reports estimated pass shift and post-align luma residual.
- **Mock scanner tests** and expanded geometry / pipeline test coverage.
- **`opencv-python-headless`** in the `lab` dependency group for ME/IR pass registration in Scan Lab.

### Fixed

- **Scan orientation** on `mirror_x` models (8200i SE): left–right correction in `ImagePipeline.assemble()`; prescan crop no longer applies a compensating X flip.
- **ME shadow colour fringes** from short/long misregistration: improved OpenCV phase-correlation alignment (AREA downsampling, ROI refinement), merge guard at misaligned edges, and chunked IVW merge to avoid float32 OOM at 3600+ dpi.
- **High-PPI memory use** in the image pipeline (integer oversample sums, chunked host calib / film-base makeup / border clamp).
- **8200i SE IR scans**: correct colour DVDSET handling, flattened green IR plane on `ScanImage.ir`, grayscale preview in Scan Lab.
- **Scan Lab**: crop UI limited to Prescan tab; missing `busy_changed` worker signal; effective crop status in status bar.

### Changed

- **`ScanImage`** slimmed to NegPy-facing fields only (`rgb`, `dpi`, `device_model`, optional `ir`). ME bracket data moved to `Scanner.last_me_debug`.
- ME short/long planes stay **linear** on the bracket; film-base makeup runs **once** on the merged deliverable.
- Non-SE bring-up: short Y travel clamps, USB planar RGB toggle, and SANE-aligned session improvements for experimental models.

### Notes for integrators

- **Forward-only orientation fix:** scans saved before 1.1.2 may still appear mirrored on disk; rescan if orientation matters.
- **NegPy / other apps:** consume `ScanImage.rgb` / `ir` as returned; do not apply an extra `[:, ::-1]` flip or prescan crop mirror compensation.
- **Breaking:** removed `ScanImage` ME fields (`rgb_short`, `rgb_long`, `exposure_*`, `merge_*`, `align_shift_*`). Use `Scanner.last_me_debug` for bracket inspection.

## [1.1.1] - 2026-08-12

Prior release. See the [v1.1.1](https://github.com/jboneng/pyopticfilm/releases/tag/v1.1.1) tag for details.
