# SANE genesys traces for OpticFilm 8200i

No independently generated SANE fixture is committed yet. Hardwareless SANE
still needs a responding USB device or a patched genesys USB layer.

When a Linux `SANE_DEBUG_GENESYS` / `SANE_DEBUG_SANEI_USB` log exists, convert
it and drop the JSON here:

```text
python tools/sane_debug_to_trace.py sane.log \
    --out tests/traces/sane/8200i/1800_rgb16_setup.json \
    --model "OpticFilm 8200i" --dpi 1800 --revision <sane-backends-sha>
```

For register-only compare (recommended), also write
`1800_rgb16_setup.registers.json` with a `registers` map; see
`docs/scanner-validation.md`. CI runs
`test_sane_register_fixture_if_present` when that file exists.

Do not overwrite `tests/traces/python/8200i/1800_rgb16_setup.json`. Home/feed
sequences should be separate fixtures once those paths are stable.

SANE anchors: `gl846.cpp` optical init, `genesys.cpp` offset/gain then
dark/white shading order — see `docs/sane-opticfilm.md`.
