# Scan Lab

PyQt6 bring-up GUI for exercising `pyopticfilm` without NegPy.

Scan Lab is **repo-only** (not shipped on PyPI). Use it to try mock USB for any
known OpticFilm model, or a real OpticFilm 8200i SE when one is plugged in.

It does **not** flip `scan_ready`. Non-SE models stay scan-locked on real USB
unless you explicitly enable **Override safety HW gate** (lab bring-up only).
The mock path is the usual way to drive those pipelines without hardware.

## Requirements

- A checkout of this repository
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- PyQt6 (via the `lab` dependency group)
- For real scans: WinUSB/libusb access to the scanner — see
  [docs/windows-setup.md](../../docs/windows-setup.md)

## Install and run

From the repository root:

```powershell
uv sync --group lab
uv run python -m tools.scanlab
```

On first launch you should see the main window with a device list, controls on
the left, and image / USB log tabs on the right.

## Quick start (mock)

1. Leave **Run against MOCK** checked (default).
2. Pick any model in **Device** (for example `OpticFilm 8200i SE (GL128)`).
3. Click **Prescan** — a synthetic pattern appears on the Prescan tab.
4. Drag a crop rectangle on the prescan image (optional).
5. Choose **PPI**, optionally enable **IR pass**, then click **Scan**.
6. Open the **USB log** tab to see control/bulk traffic with section markers.

Mock frames are **not** film. The fake USB layer fills RGB16 with a deterministic
pattern (`R=x`, `G=y`, `B=x XOR y`) so the pipeline and UI can be checked without
a scanner.

## Quick start (real 8200i SE)

1. Bind the scanner for libusb (Windows: WinUSB via Zadig — see windows-setup).
2. Click **Refresh devices**. A connected SE should appear with `— connected`.
3. Select that device.
4. **Uncheck** **Run against MOCK**.
5. **Prescan** at 1200 dpi (full safe window), drag a crop if you want, then
   **Scan** at the PPI you care about.
6. With **IR pass** enabled, Scan Lab runs colour then infrared. The IR tab shows
   a grayscale plane (green CCD channel, host-flattened), not a magenta RGB
   preview.

Real non-SE OpticFilms can appear as connected, but the library will refuse to
scan them until that model is hardware-validated (`scan_ready`) — unless you
check **Override safety HW gate** (warning dialog; motors/lamp can run).

## Quick start (real non-SE bring-up, e.g. 8100)

Lesson from early 8100 HW: override unlocks the gate; it does **not** make
full-TA high-PPI Scan safe.

1. Uncheck **Run against MOCK**, check **Override safety HW gate** (accept warning).
2. Leave **Apply calib** unchecked until a short image looks sane (avoids extra motor moves).
3. **Prescan** (uses the model’s lowest PPI, e.g. 600 on 8100) — Lab clamps to a
   short Y strip, not full TA.
4. If the image is rainbow-striped with a recognizable scene, toggle **USB planar RGB**
   and Prescan again (chunky vs planar decode).
5. Drag a **small** crop, pick a low/mid PPI, then **Scan**. At ≥2400 dpi without a
   short crop, Lab warns and still clamps travel.
6. Do **not** expect full-frame 3600 until decode is correct and short Scans park cleanly.
   `scan_ready` stays False until that is proven.

## Controls

| Control | Purpose |
|---------|---------|
| **Device** | Connected Plustek scanners first, then every known model. |
| **Run against MOCK** | On (default): fake USB. Off: open the selected connected device. |
| **Override safety HW gate** | Off (default). On: after a warning, unlock scan/home/park on real USB for models with `scan_ready=False`. Does not flip `scan_ready`. |
| **Apply calib** | Off (default). Host/ASIC shading before the image. Forced off when enabling HW override. |
| **USB planar RGB** | Off = chunky `RGBRGB…` (default). On = planar planes — try this if Prescan is rainbow-striped. |
| **Refresh devices** | Re-enumerate USB and rebuild the device list. |
| **PPI** | Resolutions from the selected model’s `resolutions_dpi` (Scan only; Prescan uses a fixed low dpi). |
| **IR pass** | After colour Scan, run a second infrared pass (disabled if the model has no IR). |
| **Multi-exposure (ME)** | GL128 / 8200i SE only: short then long colour pass (3× exposure). |
| **ME merge** | Optional host merge of short+long (`None`, Linear, Fusion). Raw planes always kept on ``ScanImage``. |
| **Prescan** | Low-res preview (SE: 1200 dpi safe window; non-SE: lowest dpi + short Y strip). |
| **Scan** | Colour scan at the chosen PPI; optional IR and/or ME. Uses the prescan crop when one is set (clamped on non-SE). |
| **Open capture…** | Open a USBPcap ``.pcap`` / ``.pcapng``. Decodes the largest bulk IN through the selected model’s image pipeline and diffs FEEDL / LINCNT / DPISET vs Lab geometry (Capture tab). |
| **Cancel** | Sets the scan cancel event (busy scans only). |

The yellow banner states MOCK vs REAL for the current selection (HW gate override
and USB RGB layout when relevant).

## Tabs

### Prescan

Full-window preview. Drag with the left mouse button to set a normalized crop.
Scan uses that crop (mirrored on SE so image-left matches film/TA space).
Clear the crop by changing device, or by starting a new rubber-band that is too
small to keep.

### Color short / Scan

Colour result of the last Scan (short exposure when ME is enabled). **Load 16-bit
TIFF…** opens a saved short plane from disk (e.g. exported earlier or from NegPy).

### Color long

Long ME exposure frame when **Multi-exposure** was enabled. **Load 16-bit TIFF…**
opens a saved long plane. When both short and long are loaded (from scan or disk),
change **ME merge** (Linear / Fusion) to preview the host merge on the **Merged**
tab without rescanning.

### Merged

Host-merged short+long when **ME merge** is Linear or Fusion.

### IR

Infrared result when **IR pass** was enabled. Displayed as **grayscale** from
the driver’s IR plane (`ScanImage.ir`), which is the green CCD channel after
host flatten on GL128.

### Capture

Passive analysis of a USBPcap recording (VueScan / SilverFast / SANE / Lab):

1. Select the model that matches the capture (e.g. OpticFilm 8100).
2. **Open capture…** and choose a `.pcapng` / `.pcap` from USBPcap/Wireshark.
3. The **Scan** tab shows the decoded image. Lab finds the SilverFast
   ``VALUE_BUFFER`` preamble (``wIndex=0x08``), carves that many bulk-IN bytes,
   and for **8200i SE** decodes as 16-bit chunky RGB using capture
   STRPIXEL/ENDPIXEL/LINCNT/DPISET (Lab PPI is ignored for decode — a mismatch
   shears the image). Use **USB planar RGB** only if rainbow-striped. Select
   **8200i SE** for SE captures. The **Capture** tab reports preamble size and
   register diffs.
4. The **Capture** tab lists FEEDL / LINCNT / DPISET from the capture versus what
   Lab would program at the current PPI / crop — useful for motor-grind diagnosis.

This does **not** replay the foreign driver’s full USB conversation through
`Scanner.scan()`; it only decodes image data and compares key registers.

### USB log

Live truncated log of every control and bulk transfer through the recording
USB wrapper. **Open capture…** also fills this tab from the pcap (identical
line format; repeated bulk-IN lengths are collapsed as ``×N``).

- Dividers mark `PRESCAN`, `SCAN`, `IR`, and `CAPTURE` sections.
- **Jump** buttons scroll to those dividers when present.
- **Clear USB log** empties the buffer (Prescan also clears the log).

Progress for the active pass is shown in the status bar.

## Geometry notes

### 8200i SE

- Prescan / uncropped Scan use the capture-safe **preview** window (feed2 at
  the top of the TA window), not a raw full-TA `area=None` request that can
  overrun the motor window.
- Rubber-band crops are clamped so image `LINCNT` cannot past the scan-window
  end (see `captures/8200i-se/MOTOR.md` in the repo if present).

### Unvalidated non-SE (8100 / GL845 aliases, …)

- Prescan and uncropped Scan use a **short Y strip** (~5 mm / ≤18% of TA), never
  full TA — full-frame high PPI was observed to grind the motor on an 8100.
- Tall rubber-band crops are clamped to that same height budget.
- High-PPI Scan (≥2400) with override on prompts a warning before the clamped move.
- Prefer **Apply calib** off and **USB planar RGB** experiments before long travels.

Calibration is controlled by the **Apply calib** checkbox (default off).

## Motor grind recovery

If the carriage grinds, chatters against a stop, or stops mid-travel during Lab
bring-up:

1. **Power off** the scanner (front/power switch).
2. **Unplug the power cord** from the scanner (or wall), wait a few seconds.
3. **Reconnect** power and turn the scanner back on.
4. **Home** the head with a known-good tool (for example **VueScan** or
   **SilverFast**) before using Scan Lab or pyopticfilm again.

This recovery sequence has only been **tested on the OpticFilm 8200i SE**. Treat
it as a starting point on other models (8100, etc.); keep a hand near power and
do not force the carriage by hand.

## What Scan Lab is not

- Not part of the PyPI package or wheel/sdist.
- Not a NegPy replacement (no iSRD retouch UI, no roll workflow). Each image tab can export 16-bit TIFF for use in NegPy.
- Not a substitute for flipping `scan_ready` — override is lab-only unlock.

For protocol / support levels, see
[docs/scanner-validation.md](../../docs/scanner-validation.md).

## Layout

```
tools/scanlab/
  __main__.py   # uv run python -m tools.scanlab
  app.py        # main window
  backend.py    # device list, open real/mock, SE geometry helpers
  capture_pcap.py  # USBPcap parse + bulk decode / register diff
  worker.py     # QThread scan worker + USB log dividers
  widgets.py    # crop view + RGB/gray preview
  README.md     # this guide
```

Scans never run on the GUI thread; USB I/O goes through `ScanWorker` on a
`QThread`.
