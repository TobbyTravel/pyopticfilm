# Changelog

All notable changes to pyopticfilm are documented here.

## Unreleased

### Fixed

- **Scan orientation (OpticFilm 8200i SE and other `mirror_x` models):** Left–right
  mirroring is corrected in `ImagePipeline.assemble()` when `model.mirror_x` is
  true. Prescan crop coordinates no longer apply a compensating X flip; widget
  coords map directly to TA `area`.

### Notes for integrators

- **Forward-only:** Files scanned before this release may still appear
  left–right mirrored on disk. Rescan if correct orientation matters.
- **NegPy / other apps:** Use `Scanner.scan()` output (or call `assemble()` on
  decoded RGB). Do not flip pixels again if you consume `ScanImage.rgb` from
  pyopticfilm. Remove any `prescan_mirror_x` / `crop_to_scan_window(...,
  mirror_x=...)` crop compensation — see [docs/negpy-integration.md](docs/negpy-integration.md).
