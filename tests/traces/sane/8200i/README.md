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

For register-only compare (recommended), a file named
`1800_rgb16_setup.registers.json` with a `registers` map is enough; see
`docs/scanner-validation.md`.

CI skips the SANE differential test until that file is present.
