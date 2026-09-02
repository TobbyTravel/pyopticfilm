# Contributing

Hardware-tested models (today: OpticFilm 8200i SE and OpticFilm 8100 V2) share
one GL128 driver path (`asic/gl128.py`, `scan/session_gl128.py`, geometry,
pipeline, calibration, and `device/tables_8200i_se.py`). A change that is
correct for one model is not automatically correct for the other.

## Specialize, do not retarget

- Put model-specific constants and flags on **that model's** class
  (`device/model_8200i_se.py` or `device/model_8100_v2.py`). Do not change a
  sibling's defaults to make a fix work.
- The 8100 V2 does **not** subclass the SE tables. Shared identical tables live
  in `device/gl128_common.py`. Capture-proven differences are listed in
  `GL128_DIVERGENT_FIELDS`. Adding a GL128 field requires updating that catalog
  (and both leaf classes when it is a divergent knob).
- Do not introduce `getattr(model, "new_knob", se_default)` in shared GL128
  code. Add a required field on `Gl128Model` / both leaves instead.

## Model-lock tests vs invariants

- **Oracles** live under `tests/model_lock/<model>/`. They freeze driver-path
  numbers for one hardware-validated model. Do not parametrize them across
  models, copy them into `tests/` with weaker asserts, or edit them to green
  another model's change. See [tests/model_lock/README.md](tests/model_lock/README.md).
- **Invariants** (even USB widths, IR refused when unsupported, priming
  default) may be parametrized over every GL128 model in ordinary tests.

Run lock tests with `uv run pytest -m model_lock`.

Add a lock folder only when a model is hardware-validated and the maintainer
asks. Every `scan_ready` model must have a lock folder.

## Hardware sign-off

CI has no scanner. Protocol goldens and lock tests do not prove motors, AFE, or
USB races. Before flipping `scan_ready` or changing a model's lock oracles,
confirm on that hardware: park, full-frame 1200 / 1800 / 7200, a crop, ME, and
IR if the model has an IR channel. See
[docs/scanner-validation.md](docs/scanner-validation.md).

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run pytest -q
```
