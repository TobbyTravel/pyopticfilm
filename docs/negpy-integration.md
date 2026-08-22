# NegPy and other app integrators

pyopticfilm corrects left–right sensor mirroring in **`ImagePipeline.assemble()`**
when the film model sets `mirror_x=True` (e.g. OpticFilm 8200i SE). IR planes
derived after `assemble()` inherit the same orientation.

## Recommended integration

1. Scan via **`Scanner.scan()`** and use **`ScanImage.rgb`** / **`ScanImage.ir`**
   as returned — do **not** apply an additional `[:, ::-1]` flip.
2. Map Prescan crop rectangles with **`image_crop_to_scan_area()`** from
   `pyopticfilm.scan.bringup` — pass widget-normalized coords through without
   a separate `mirror_x` crop flip.

## If scans still appear mirrored in your app

Your backend may bypass `assemble()` (e.g. calling `decode_rgb()` only). Either:

- Route through `Scanner.scan()` / `session.run()` so `assemble()` runs, or
- Apply `rgb[:, ::-1]` (and IR if present) **once**, after IR/RGB alignment,
  keyed on `model.mirror_x` — and remove any compensating crop-coordinate flip.

Applying both pixel flip and crop flip, or flipping twice, will mis-orient scans
and crops.

## NegPy-specific cleanup (separate repo)

When upgrading NegPy to a pyopticfilm release that includes this fix:

| Location | Action |
|----------|--------|
| `PlustekBackend._scan_on_scanner()` | Use `Scanner.scan()` output; do not flip if already from `assemble()` |
| `ScannerCapabilities.prescan_mirror_x` | Remove |
| `crop_to_scan_window(..., mirror_x=...)` | Remove mirror branch; clamp only |
| `PrescanCropDialog._prescan_mirror_x` | Remove |

Previously saved scan files are not retroactively corrected; users should rescan
if orientation matters.
