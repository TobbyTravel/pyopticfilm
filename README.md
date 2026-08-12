# pyopticfilm

PyUSB driver for Plustek OpticFilm USB film scanners (Genesys GL842/843/845/128 ASIC).

This package is the low-level scanner driver extracted from [NegPy](https://github.com/marcinz606/NegPy). NegPy integrates it via the optional `plustek` dependency group (`pip install negpy[plustek]`).

## Install

```bash
pip install pyopticfilm
```

On Windows, `libusb-package` is installed automatically so PyUSB can load libusb-1.0.

## Quick start

```python
from pyopticfilm import Scanner

with Scanner.open() as scanner:
    image = scanner.scan(resolution=1800, mode="color")
    image.rgb  # uint16 H×W×3
```

## Development

```bash
uv sync --all-groups
uv run pytest
```

## License

GPL-3.0-or-later — see [LICENSE](LICENSE).
