# pyopticfilm

Python driver for Plustek OpticFilm USB film scanners, built on [PyUSB](https://github.com/pyusb/pyusb) and reverse-engineered from USB captures and the SANE `genesys` backend.

The library talks directly to the scanner’s Genesys ASIC (GL842, GL843, GL845, or GL128) over USB—no vendor Windows driver is required once the device is bound for libusb access.

## Supported hardware

**Only the OpticFilm 8200i SE is validated for scanning in this release.**

| Model | USB ID | ASIC | Scan |
|-------|--------|------|------|
| OpticFilm 8200i SE | `07b3:1825` | GL128 | **Yes** |
| OpticFilm 8200i | `07b3:130d` | GL845 | Probe only |
| OpticFilm 8100 | `07b3:130c` | GL845 | Probe only |
| OpticFilm 7600i (v1 / v2) | `07b3:0c3b` | GL845 | Probe only |
| OpticFilm 7500i | `07b3:0c13` | GL845 | Probe only |
| OpticFilm 7400 (v1 / v2) | `07b3:0c3a` | GL845 | Probe only |
| OpticFilm 7300 | `07b3:0c12` | GL845 | Probe only |
| OpticFilm 7200i / 7200 | `07b3:0c04`, `07b3:0807`, `07b3:0c07` | GL843 / GL842 | Probe only |

Other OpticFilm models **enumerate and open**: you can read status, turn the lamp on/off (where implemented), and dump registers for bring-up. **`scan()`, `calibrate()`, `home()`, and `park()` are intentionally gated** on probe-only models until the protocol is verified on real hardware—calling them raises `AsicError` with a clear message rather than risking carriage or lamp damage on untested tables.

`Scanner.open()` prefers a scan-ready 8200i SE when several Plustek film scanners are connected.

## Features (8200i SE)

- Color and infrared transparency scans at 150–7200 dpi (ASIC programs at ≥600 dpi; lower PPI shares the 600 dpi register set and is downsampled on the host)
- Optional crop via normalized `area` (`x1, y1, x2, y2` in 0–1)
- Dark/white shading calibration with on-disk cache (`~/.cache/pyopticfilm/calib_v2.json`)
- GL128 ASIC shading path (AFE codes + shading blob) aligned with SilverFast capture order
- 16-bit RGB `numpy` output; optional TIFF export via `tifffile`
- Progress and cancel hooks for long scans

Not implemented or out of scope here: iSRD infrared dust removal, SilverFast-style UI, or shipping a desktop app—applications own post-processing.

## Requirements

- Python ≥ 3.11
- `numpy`, `pyusb`
- **libusb 1.0** backend for PyUSB
  - **Windows:** `libusb-package` is installed automatically with `pip install pyopticfilm` and provides a bundled `libusb-1.0.dll`
  - **Linux:** system `libusb-1.0` (e.g. `libusb-1.0-0` on Debian/Ubuntu) and permission to access the device (udev rule or run as root—not recommended)
  - **macOS:** libusb via Homebrew or `libusb-package`

Optional: `tifffile` for `ScanImage.save_tiff()`.

## Installation

```bash
pip install pyopticfilm
```

From source:

```bash
git clone https://github.com/jboneng/pyopticfilm.git
cd pyopticfilm
uv sync --all-groups
```

## USB access

The Plustek vendor driver must **not** own the device when using this library.

### Windows

Use [Zadig](https://zadig.akeo.ie/) to replace the vendor driver with **WinUSB** (or libusbK) for the scanner’s USB interface. Step-by-step instructions, troubleshooting, and how to revert to the Plustek driver are in [docs/windows-setup.md](docs/windows-setup.md).

### Linux

Install libusb and add a udev rule granting your user access to Plustek film scanners, for example:

```
# /etc/udev/rules.d/99-plustek-opticfilm.rules
SUBSYSTEM=="usb", ATTR{idVendor}=="07b3", MODE="0666"
```

Reload udev rules and replug the scanner.

## Quick start

```python
from pyopticfilm import Scanner

with Scanner.open() as scanner:
    print(scanner.model.model, scanner.device_id)
    scanner.warmup()  # init, home, lamp on
    image = scanner.scan(resolution=1800, mode="color")
    print(image.rgb.shape, image.rgb.dtype)  # H×W×3 uint16

    image.save_tiff("frame.tif")  # requires tifffile
```

Infrared scan (8200i SE):

```python
with Scanner.open() as scanner:
    scanner.warmup()
    ir = scanner.scan(resolution=1800, mode="infrared")
```

Crop (normalized coordinates on the transparency window):

```python
image = scanner.scan(
    resolution=2400,
    mode="color",
    area=(0.1, 0.1, 0.9, 0.9),  # x1, y1, x2, y2
)
```

List devices without opening:

```python
from pyopticfilm.usb.device import find_devices

for info in find_devices():
    print(info.device_id, info.product_id, info.asic_hint, info.is_supported)
```

## API overview

| Entry | Purpose |
|-------|---------|
| `Scanner.open(device_id=None)` | Open preferred or specified OpticFilm |
| `scanner.warmup(home=True, lamp=True)` | Boot ASIC, optional home + lamp |
| `scanner.scan(...)` | Run a full scan → `ScanImage` |
| `scanner.calibrate(...)` | Run shading; updates cache |
| `scanner.status()` | Read scanner status flags |
| `scanner.home()` / `scanner.park()` | Motor positioning |
| `scanner.lamp_on()` / `scanner.lamp_off()` | Lamp control (allowed on probe-only models) |
| `scanner.advanced` | Low-level register read/write (bring-up) |
| `scanner.calibrator` | Direct access to calibration cache |

`ScanImage` fields: `rgb` (uint16 H×W×3), `dpi`, `device_model`, optional `ir` plane (not populated by default).

Enable debug logging:

```python
from pyopticfilm.logging import enable_debug_logging

enable_debug_logging()
```

## Calibration

Shading runs automatically before scan when a matching cache entry exists. Force a new calibration with `scanner.calibrate(force=True)` or `scanner.scan(..., apply_calib=False)` to skip applying cached data.

The cache key includes resolution, crop geometry, and scan method (transparency vs infrared). GL128 colour shading uses ASIC-internal measurements at home; IR uses a white-only path suitable for stationary shading.

## Probe-only models

Code for additional OpticFilm variants is included so enumeration, model selection, and SANE-derived geometry tables can be exercised without hardware. These paths are **deliberately locked** for motor moves and image acquisition:

- `model.scan_ready` is `False` for every model except the 8200i SE
- `Scanner._ensure_scan_ready()` blocks scan, calibrate, home, and park
- GL128 motor moves stay disabled unless the model is scan-ready

If you have a non-SE OpticFilm and want to help validate scanning, open an issue with your exact USB IDs (`bcdDevice` matters for some models) and we can work through capture-based bring-up.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run pytest -q
```

CI runs on Python 3.11–3.13 (lint + tests; no hardware in CI).

Project layout:

- `src/pyopticfilm/usb/` — enumeration, claim, Genesys USB protocol
- `src/pyopticfilm/asic/` — per-ASIC drivers (GL128, GL845, …)
- `src/pyopticfilm/device/` — per-model register/geometry tables
- `src/pyopticfilm/scan/` — geometry, calibration, scan session pipeline

## License

GPL-3.0-or-later — see [LICENSE](LICENSE).

## Acknowledgements

Register and motor tables for GL845-family models are derived from the SANE `genesys` backend (see [NOTICE](NOTICE) and [docs/sane-opticfilm.md](docs/sane-opticfilm.md)). The 8200i SE (GL128) protocol was reconstructed from USB traffic captures of the Windows driver and SilverFast; it is not present in SANE.
