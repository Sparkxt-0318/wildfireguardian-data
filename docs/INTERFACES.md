# INTERFACES

## Summary

This repository has **two** stable public interfaces — its command-line tool
and `bundle_manifest.json`, the canonical bundle contract (`DECISIONS.md`
D-0027). `StudyAreaBundle` and the rest of the on-disk bundle layout are
**internal and unstable**. See `DECISIONS.md` D-0001.

A downstream repository codes against `bundle_manifest.json`. It does not read
`manifest.json`, the provenance sidecars, or the Python objects.

## Stable: the CLI

```bash
wg-data build-study-area CONFIG.yaml [--out DIR] [--allow-network] [--force]
wg-data validate-study-area BUNDLE_DIR [--strict] [--json]
wg-data summarize-study-area BUNDLE_DIR [--json]
wg-data provenance BUNDLE_DIR [--layer NAME] [--unknown-only] [--json]
wg-data compatibility BUNDLE_DIR [--profile NAME] [--json]
wg-data import-fuels      FILE --out DIR --scheme NAME <provenance...>
wg-data import-population FILE --out DIR --aggregation-level LEVEL <provenance...>
wg-data import-facilities FILE --out DIR --kind-map JSON <provenance...>
wg-data import-roads      FILE --out DIR <provenance...>
wg-data make-fixtures OUT_DIR [--only NAME]
wg-data version
```

The `import-*` commands ingest a file downloaded by hand, for the sources that
cannot be fetched from code (`reports/SOURCE_ACCESS_STATUS.md`). Every
provenance fact is a **required** argument — `--source-name`, `--source-url`,
`--source-date`, `--licence`, `--temporal-class` — because a file on disk states
none of them and a filename is not evidence (`AGENTS.md` §3). One of
`--expect-sha256` or `--no-expected-checksum` is also required: the checksum
decision is never skipped, only explicitly waived. A mismatch is exit 3 and
writes nothing.

Declare `--licence UNKNOWN` if you have not read the licence, or if the source
states contradictory terms. An unverified licence recorded as fact is worse than
a gap — 임상도's product page states CC-BY *and* "all rights reserved".

`provenance` exits 1 only when a layer has `UNKNOWN` in one of the three fields
without which its values cannot be interpreted (`output_crs`, `value_unit`,
`nodata_representation`) — the same condition `validate-study-area` reports as
`PRV-001`. Every other `UNKNOWN` is a finding, not a failure (D-0009).

`compatibility` exits 0 whatever it finds: `INCOMPLETE` is a true answer about a
bundle, not an error in producing one. Gate on `--json` instead.

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
  bundle_manifest.json           # STABLE: the downstream contract (D-0027)
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

## Why the published contract is narrow

**Superseded in part.** A contract *is* now published — `bundle_manifest.json`,
above, since D-0027. What has not changed is the reasoning that kept it narrow,
so it is kept here rather than deleted.

The argument against publishing anything was that a schema written before a real
consumer existed would encode this repository's internal convenience as an
inter-repository standard, and the first real consumer would discover that, for
example:

- routing needs a **directed** graph with turn restrictions, which this
  repository deliberately does not model (A-RD-1);
- fire behaviour needs fuel parameters this repository will not invent (A-FU-1);
- rescue-time analysis needs slope-corrected, surface-aware travel costs, not
  the planar lengths here (A-RD-5).

Every one of those is still true. So the contract publishes **what each layer is
and where it came from**, and publishes the refusals as data: each consumer
profile in `wg-data compatibility` carries a machine-readable
`not_supplied_here` list naming exactly these gaps (D-0028). A consumer reads
what it may rely on *and* what it must supply itself, from the same artifact.

What remains unpublished, and deliberately: `StudyAreaBundle`, the Python API,
`manifest.json` and the provenance sidecars (D-0001).

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

## Stable: `bundle_manifest.json`

The canonical bundle contract (`DECISIONS.md` D-0027). Carries its own
`bundle_schema_version`, independent of `manifest.json`'s `schema_version` and
of each provenance record's `schema_version` — three versions, because they
change for three different reasons and a consumer cares about one of them.

```json
{
  "bundle_schema_version": "1.0.0",
  "bundle_id": "...",
  "created_at": "<ISO-8601, when the bundle was built>",
  "study_area": {"study_area_id": "...", "bounds": {...}, "extent_m": [...]},
  "crs": "EPSG:5187",
  "canonical_grid": {"status": "SHARED | MISMATCHED | NO_RASTERS", ...},
  "layers": {
    "terrain":    {"status": "PRESENT | ABSENT", "reason": "...", "layers": [...]},
    "roads":      {..., "graph": {...}},
    "fuels":      {..., "class_scheme": {...}},
    "population": {...},
    "facilities": {...}
  },
  "provenance": {...},
  "validation": {"status": "...", "counts": {...}},
  "checksums": {"<layer>": "<sha256>"},
  "caveats": [...]
}
```

Guarantees a consumer may rely on:

- **All five `layers` slots are always present.** A layer this repository does
  not have is `status: "ABSENT"` with a `reason` — never a missing key. An
  `ABSENT` slot whose reason is `UNKNOWN` means the gap was not documented,
  which is itself a finding.
- **Every present layer carries** `data_class`, `units`, `licence`, `checksum`,
  `valid_from`/`valid_to`, `surface_model`, its `sources` (with `source_date`
  and `acquisition_date` kept separate), and machine-readable `limitations`.
- **`data_class` is uppercase here**, as research governance requires
  (`OC-029`), and lowercase in this repository's own artifacts. The
  `RETROSPECTIVE` axis collapse (D-0023) happens at this boundary only.
- **It cannot drift.** It is derived from the bundle, and `validate-study-area`
  re-derives it — a mismatch is `BND-023`, an ERROR.
- **It asserts nothing about validity or safety.** It says what each layer is
  and where it came from. It does not claim the bundle is scientifically valid,
  fit for a purpose, or that anything in it is safe, passable or usable
  (`AGENTS.md` §5). Readiness, reported separately by `wg-data compatibility`,
  is a statement about *completeness* only — see `DECISIONS.md` D-0028.
