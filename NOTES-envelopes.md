# Cached output envelopes - generation notes

Seven new `*.wdgwars.json` files were added under `examples/`, extending
the existing `dump1090_real.wdgwars.json` pattern. Each is the canonical
JSON Muninn emits for its sibling input fixture, intended as a fixed
reference for cross-implementation parity testing.

## CLI invocation

Each envelope was produced by running:

```bash
python3 muninn.py examples/<fixture> --no-version-check
```

This writes `examples/<stem>.wdgwars.json` next to the input (Muninn's
default output behavior. `--out` was not needed). The `.wdgwars.json`
content is exactly what `_to_dump1090_fa(records)` returns in `muninn.py`
serialized with `json.dumps(..., indent=2)`.

## Per-fixture results

| Fixture | Aircraft | Notes |
|---|---:|---|
| `stratux_sample.json`         | 12 | clean |
| `tar1090_chunk_sample.json.gz`| 12 | clean (file renamed, see below) |
| `vrs_sample.json`             | 12 | clean |
| `ndjson_sample.json`          | 12 | clean |
| `sbs1_sample.txt`             |  3 | clean |
| `mayhem_sample.txt`           |  6 | clean |
| `sbs1_real.txt`               | 10 | clean |

All seven aircraft counts match the documented baselines in
`examples/README.md`. No fixture-level issues surfaced; no parser
modifications were made.

## File-naming note (tar1090)

For the gzipped input `tar1090_chunk_sample.json.gz`, Muninn's default
output is `tar1090_chunk_sample.json.wdgwars.json` because `Path.stem`
only strips the final `.gz` extension. The committed file has been
renamed to `tar1090_chunk_sample.wdgwars.json` to keep the cached-envelope
naming convention consistent (one `.json.wdgwars.json` chain instead of
two). The file *content* is exactly what the CLI emits, only the
filename was adjusted post-write.

## Reproducing or extending

To regenerate a single envelope:

```bash
python3 muninn.py examples/sbs1_real.txt --no-version-check
```

To regenerate all seven in one sweep:

```bash
for f in examples/stratux_sample.json \
         examples/tar1090_chunk_sample.json.gz \
         examples/vrs_sample.json \
         examples/ndjson_sample.json \
         examples/sbs1_sample.txt \
         examples/mayhem_sample.txt \
         examples/sbs1_real.txt; do
    python3 muninn.py "$f" --no-version-check
done
mv examples/tar1090_chunk_sample.json.wdgwars.json \
   examples/tar1090_chunk_sample.wdgwars.json
```

The `now` field in each envelope is the wall-clock time of the
generating run, so byte-identical reproduction across runs is not
possible. The aircraft list shape, ordering, and counts are stable as
long as the input fixture and Muninn version are unchanged.
