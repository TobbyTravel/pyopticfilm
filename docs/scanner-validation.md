# Scanner protocol validation (hardwareless)

This document describes how pyopticfilm tests scanner protocol behaviour
without a physical device, and how that differs from hardware bring-up.

Protocol tests **do not** prove that a physical scanner works. Motor timing,
lamp/AFE analogue behaviour, firmware quirks, and real USB races are out of
scope. `scan_ready` stays `False` for every model except the OpticFilm 8200i SE
until that model has produced a real image and a successful park.

## Support levels

| Level | Meaning |
|-------|---------|
| **Hardware tested** | Live scan + park on physical hardware. Only the 8200i SE. |
| **Protocol validated** | Python USB traffic for a documented setup matches a golden trace, and optical registers match independently computed geometry. A SANE genesys register dump, when present, is an additional oracle. |
| **Experimental** | Tables and session code exist; `scan()`, `home()`, `park()`, and `calibrate()` stay locked. |

The 8200i SE is capture-derived (not a SANE port). SANE is not an oracle for it.

## Architecture

```text
ScanSession / Gl845
        │
        ▼
GenesysUsbProtocol
        │
        ▼
UsbTransport (Protocol)
        ├──────────────► UsbDeviceHandle  → PyUSB
        └──────────────► FakeUsbTransport / MockScannerTransport → tests + Scan Lab
```

`UsbTransport` already existed on `GenesysUsbProtocol`. Tests construct
`create_asic(GenesysUsbProtocol(fake), model)` and call ASIC / `ScanSession`
directly. They do **not** go through `Scanner.scan()` (the `scan_ready` gate is
intentional).

Helpers:

| Path | Role |
|------|------|
| `tests/scanners/fake_usb.py` | Recording fake USB device |
| `tests/scanners/trace_compare.py` | JSON traces, poll collapsing, first-difference diffs |
| `tests/scanners/sane_debug.py` | Parse `SANE_DEBUG_SANEI_USB` / genesys logs |
| `tests/traces/python/` | Golden Python USB traces (CI) |
| `tests/traces/sane/` | Independently generated SANE fixtures (optional) |
| `tools/dump_python_setup_trace.py` | Regenerate a Python golden trace |
| `tools/compare_scanner_trace.py` | Compare two JSON traces |
| `tools/sane_debug_to_trace.py` | Convert a SANE debug log to JSON |
| `tools/scanlab/` | PyQt6 bring-up GUI (mock or real SE); [user guide](../tools/scanlab/README.md) |

## Current golden: OpticFilm 8200i, 1800 dpi, RGB16

Phase recorded: ASIC `init()` + `ScanSession._configure()` (no home poll, no
lamp, no image bulk, no calibration AHB). Status register `0x41` is scripted
idle (at home, buffer empty, frontend ready) so poll loops exit immediately.

CI checks:

1. USB-decoded DPISET / STRPIXEL / ENDPIXEL / LPERIOD / dummy / MAXWD / LINCNT
   match `compute_geometry(1800, model=MODEL_8200I)`.
2. Full USB transaction list matches `tests/traces/python/8200i/1800_rgb16_setup.json`.
3. If `tests/traces/sane/8200i/1800_rgb16_setup.registers.json` exists, optical
   registers are compared to that SANE dump.

Regenerate the Python fixture after an intentional protocol change:

```bash
python tools/dump_python_setup_trace.py
```

Review the diff. Do not copy Python tables into a second Python file and assert
they are equal.

## SANE as oracle (spike notes)

Byte-for-byte USB against a live `sane_start` is **not** the first oracle.
SANE genesys is a full device state machine. Even a correct port diverges on:

- Home / feed / buffer-empty poll counts
- Gamma LUT AHB uploads (not ported)
- Calibration shading blobs
- Warm vs cold boot and slope-table bulk payloads if generation differs

Prefer the **register program after scan setup** (`DPISET`, `STRPIXEL`,
`ENDPIXEL`, `LINCNT`, `LPERIOD`, `MAXWD`, lamp/scan bits).

### Interception points (lowest practical first)

1. **Genesys register log** (preferred). `SANE_DEBUG_GENESYS=255` emits
   `write_register (0xNN, 0xVV)`, `reg[0xNN] = 0xVV`, and
   `address: 0xNNNN, value: 0xVV` from `ScannerInterfaceUsb::write_register`.
2. **`sanei_usb_control_msg`**. `SANE_DEBUG_SANEI_USB=255` logs `rtype`, `req`,
   `value`, `index`, `len`, then hex dumps. This is the USB wrapper below
   genesys and above libusb.
3. **libusb / umockdev / USB gadget**. Needed for a true hardwareless SANE
   *run*. Not required to *compare* a log captured on a machine that has the
   scanner, and not the starting point.

A hardwareless SANE process still needs a responding USB device (or a patched
`scanner_interface_usb.cpp`). Until that exists, do not commit a USB JSON file
claiming to be from SANE.

### Generating a SANE register fixture (Linux, genesys built with debug)

```bash
export SANE_DEBUG_GENESYS=255
export SANE_DEBUG_SANEI_USB=255
scanimage -d genesys --resolution 1800 --mode Color \
    -x 36.33 -y 25 --format=pnm > /dev/null 2> sane-8200i-1800.log

python tools/sane_debug_to_trace.py sane-8200i-1800.log \
    --out tests/traces/sane/8200i/1800_rgb16_setup.json \
    --model "OpticFilm 8200i" --dpi 1800 --revision "$(git -C sane-backends rev-parse HEAD)"

# Register-only file is enough for CI (see test_sane_register_fixture_if_present):
python tools/sane_debug_to_trace.py sane-8200i-1800.log \
    --out tests/traces/sane/8200i/1800_rgb16_setup.registers.json \
    --model "OpticFilm 8200i" --dpi 1800 --revision "$(git -C sane-backends rev-parse HEAD)"

python tools/compare_scanner_trace.py --registers-only \
    tests/traces/sane/8200i/1800_rgb16_setup.json \
    tests/traces/python/8200i/1800_rgb16_setup.json
```

SANE file anchors for optical init and calib order are listed in
[sane-opticfilm.md](sane-opticfilm.md). Keep the Python setup golden intact;
add home/feed sequences as separate fixtures once those paths are stable.

Record the SANE backends git revision in the JSON `meta.revision` field so
fixture drift is explainable.

Parser coverage is exercised in CI against
`tests/data/sane_debug_sample.log` (a canned snippet, not a full scan).

### Lab bring-up (GL845 8200i, does not flip `scan_ready`)

```bash
uv run python tools/bringup_gl845_8200i.py --dry-run
uv run python tools/bringup_gl845_8200i.py --allow-unvalidated \
    --steps open,status,lamp,home,tiny,park,calib,ir
```

Flip `MODEL_8200I.scan_ready` only after a successful image + park on hardware.

## What protocol validation can establish

- Register configuration and command ordering
- Endpoint / vendor-request framing
- Transfer sizes and bulk preambles
- Model table application (DPISET, exposure, dummy, MAXWD units)
- Host image decode (channel order, endian, 8/16-bit, line shifts)

## What it cannot establish

- Physical motor / sensor / lamp timing
- ASIC hardware quirks and analogue frontend behaviour
- Calibration quality
- Firmware-specific behaviour
- Real USB timing and disconnect races

## 8200i SE

Use USB captures → golden traces when converting existing PCAP/PCAPNG files.
Do not compare the SE path to SANE genesys (there is no GL128 command set).
