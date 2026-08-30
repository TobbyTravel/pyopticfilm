# Model lock tests

Frozen **driver-path** oracles for hardware-validated models. They encode how
that model's scan window, motor tables, crop mapping, and flags behaved when
the tests were written.

- Add a sibling folder only when that model is hardware-validated and the
  maintainer asks for a lock.
- Do not retarget assertions here to make another model's change pass. Specialize
  the other model (flags, subclass, session branch) instead.
- Updating oracles for a model requires an explicit request from someone working
  on **that** model (see `.cursor/rules/model-lock.mdc`).

Run only this tree:

```bash
uv run pytest -m model_lock
```
