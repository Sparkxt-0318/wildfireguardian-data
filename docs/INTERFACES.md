# INTERFACES

## Summary

This repository has **one** stable public interface — its command-line tool —
and **no** stable data contract. `StudyAreaBundle` and the on-disk bundle layout
are **internal and unstable**. See `DECISIONS.md` D-0001.

## Stable: the CLI

```bash
wg-data build-study-area CONFIG.yaml [--out DIR] [--allow-network] [--force]
wg-data validate-study-area BUNDLE_DIR [--strict] [--json]
wg-data summarize-study-area BUNDLE_DIR [--json]
wg-data make-fixtures OUT_DIR [--only NAME]
wg-data version
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | success; for `validate`, no ERROR-severity findings |
| 1 | validation found ERROR findings, or `--strict` and WARNINGs exist |
| 2 | usage error (bad arguments, missing config) |
| 3 | input data error (CRS mismatch, unit mismatch, unreadable source) |
| 4 | optional dependency missing for the requested operation |
| 5 | network fetch required but not permitted or not available |

`--json` output is machine-readable and is the recommended way for any script to
consume results. Its top-level keys (`schema_version`, `status`, `findings`,
`summary`) are stable; nested finding detail is not.

## Unstable: the Python API

```python
from wildfireguardian_data import (
    RasterLayer, VectorLayer, StudyAreaBundle, ProvenanceRecord,
)
```

These are importable and documented, and the test suite uses them. They may
change without a deprecation cycle until D-0001 is superseded. Any other
repository that imports them today is accepting that.

## Unstable: the on-disk bundle layout

```text
<bundle_dir>/
  manifest.json                  # bundle-level metadata + layer index + checksums
  provenance/
    <layer_name>.provenance.json # one record per layer
  terrain/
    dem.tif | dem.npz            # + .sidecar.json when .npz
    slope_deg.tif | .npz
    aspect_deg.tif | .npz
  roads/
    roads.geojson
    roads_qa.json
  fuels/
    fuels.tif | .npz  (optional)
  population/
    villages.geojson  (optional)
  facilities/
    facilities.geojson (optional)
  validation/
    report.json
```

`manifest.json` carries `schema_version`. A reader that does not recognise the
version must fail, not guess.

## Why no data contract is published yet

The downstream repositories (forecast comparison, evacuation routing, rescue
routing, OSSE) do not exist. A schema written now would encode this
repository's current internal convenience as an inter-repository standard, and
the first real consumer would discover that, for example:

- routing needs a **directed** graph with turn restrictions, which this
  repository deliberately does not model (A-RD-1);
- fire behaviour needs fuel parameters this repository will not invent (A-FU-1);
- rescue-time analysis needs slope-corrected, surface-aware travel costs, not
  the planar lengths here (A-RD-5).

Publishing a contract before those needs are known would either be wrong or
would force those repositories to work around it.

## What a downstream consumer must do today

1. Read `manifest.json` and check `schema_version`.
2. Read the provenance record for every layer it uses, and **fail** — not warn —
   if a layer's `crs`, `temporal_class`, `data_class`, or units are not what the
   consumer requires.
3. Never assume: the road graph is directed; slope came from a bare-earth DTM;
   an `UNKNOWN` field is benign; a facility is operational; a population count
   is present-population.

## Interface negotiation, when the time comes

When a second repository exists, the process is: that repository states its
required fields and semantics in writing; this repository records the agreement
as a new `DECISIONS.md` entry that supersedes D-0001; a versioned export module
is added, with the internal model free to diverge from it. Not before.
