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

## ScanImage contract (NegPy mapping)

| `ScanImage` | NegPy `ScanResult` | Notes |
|-------------|-------------------|--------|
| `rgb` | `rgb` | H×W×3 uint16 linear; ME deliverable when `multi_exposure=True` |
| `ir` | `ir` | Optional H×W uint16 (8200i SE + `infrared=True`) |
| `device_model` | (metadata) | Vendor string from the film model |
| `dpi` | (metadata) | Scan resolution |

NegPy’s `PlustekBackend` maps only `rgb`, `ir`, and `device_model` today. ME
bracket planes are **not** on `ScanImage`; use `Scanner.last_me_debug` in Scan
Lab or audit tooling. Enable ME in apps with `scanner.scan(..., multi_exposure=True)`
(wiring in NegPy is separate follow-up work).

## If scans still appear mirrored in your app

Your backend may bypass `assemble()` (e.g. calling `decode_rgb()` only). Either:

- Route through `Scanner.scan()` / `session.run()` so `assemble()` runs, or
- Apply `rgb[:, ::-1]` (and IR if present) **once**, after IR/RGB alignment,
  keyed on `model.mirror_x` — and remove any compensating crop-coordinate flip.

Applying both pixel flip and crop flip, or flipping twice, will mis-orient scans
and crops.

## GL128 adaptive USB drain (8200i SE)

Image bulk reads use **line-aligned adaptive quiet drain** by default
(``Gl128.image_usb_pace_s`` / :data:`~pyopticfilm.asic.gl128.DEFAULT_IMAGE_USB_PACE_S`,
typically 3 ms per line cap). This keeps motor creep continuous at high PPI.
Disable for fastest host drain (louder):

```python
scanner._asic.image_usb_pace_s = 0.0
```

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
