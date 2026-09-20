# wildfireguardian-data

**The geospatial data foundation for the WildfireGuardian research project.**
It turns raw Korean geospatial data into reproducible, provenance-preserving,
quality-controlled study-area packages — and nothing else.

WildfireGuardian studies wildfire evacuation and assisted-rescue decisions for
mobility-limited residents in Korean rural communities. This repository is
responsible only for **trustworthy landscape and infrastructure inputs**, and is
usable entirely on its own: it has no dependency on any other WildfireGuardian
repository and exports no schema to one.

## The question this repository answers

> Can raw Korean geospatial data be transformed into reproducible,
> provenance-preserving, quality-controlled study-area packages **without
> introducing silent spatial, temporal, CRS, resolution, or missing-data
> errors**?

The operative word is *silent*. Clipping, reprojection and resampling all
destroy information; the claim is that every such loss is recorded,
attributable, and detectable by an independent reader. See
[`docs/RESEARCH_QUESTION.md`](docs/RESEARCH_QUESTION.md).

## Three rules that shape the whole API

1. **CRS mismatch raises.** Nothing reprojects implicitly. Korea's common CRSs
   differ by metres to hundreds of metres — enough to move a village across a
   ridge line, not enough to look broken. ([D-0002](docs/DECISIONS.md))
2. **Missing data stays missing.** Never silently zero. A derivative cell beside
   a void is `nodata`, not an extrapolation. ([D-0005](docs/DECISIONS.md))
3. **`UNKNOWN` is a value.** A fact the source does not supply is recorded as
   the literal `"UNKNOWN"` and counted by validation — never guessed.
   ([D-0009](docs/DECISIONS.md))

And one rule about what it will not say: **no function, field, or metric here
asserts that any road, shelter, refuge, or area is safe or passable.** Those are
downstream modelling conclusions. See [`docs/SCOPE.md`](docs/SCOPE.md).

## Install

```bash
pip install -e .            # core: numpy, pyproj, shapely, networkx, pyyaml
pip install -e '.[geo]'     # adds rasterio/geopandas for GeoTIFF + OGR I/O
pip install -e '.[dev]'     # adds pytest and ruff
```

The scientific core — slope, aspect, statistics, clipping, graph QA, provenance,
validation — runs **without GDAL** ([D-0003](docs/DECISIONS.md)). A `.npz` +
JSON sidecar format exists so a bundle can be written and read with no GDAL at
all.

## Use

```bash
# Build a study area from synthetic fixtures (no network needed)
wg-data build-study-area configs/uljin_valley_synthetic.yaml

# Build from real open data (Copernicus DEM + WorldCover + OpenStreetMap)
wg-data build-study-area configs/uljin_real_complete.yaml --allow-network

# Check a bundle on disk, including file checksums against provenance
wg-data validate-study-area data/study_areas/uljin_valley_synthetic_v1
wg-data validate-study-area <bundle> --strict --json

# Describe what a bundle contains, including its gaps
wg-data summarize-study-area <bundle>

# Report where every layer came from, and what is not known about it
wg-data provenance <bundle> [--layer NAME] [--unknown-only]

# Report which consumers' declared inputs this bundle satisfies.
# READY means "the named inputs are present with interpretable provenance".
# It is NOT a claim that the data is accurate (D-0028).
wg-data compatibility <bundle>

# Ingest a file you downloaded by hand, with provenance supplied explicitly.
# Every provenance fact is required; the checksum decision cannot be skipped.
wg-data import-fuels FILE --out DIR --scheme NAME <provenance...>

# Write the synthetic fixtures out for inspection
wg-data make-fixtures /tmp/fixtures
wg-data list-fixtures
```

Exit codes and the `--json` contract: [`docs/INTERFACES.md`](docs/INTERFACES.md).

### What a summary looks like

```
study area : uljin_real_v2
CRS        : EPSG:5187
bounds     : (227500.0, 478500.0) - (231500.0, 482500.0)  [4000 x 4000 m]
components : facilities, fuels, roads, terrain

layers:
  dem            raster 139x138 @ 30m  unit=m     missing=0/19182    [derived/static]
  slope_deg      raster 139x138 @ 30m  unit=deg   missing=550/19182  [derived/static]
  aspect_deg     raster 139x138 @ 30m  unit=deg   missing=550/19182  [derived/static]
  roads          vector 31 features ['LineString']                   [derived/observation_time]
  fuels          raster 139x138 @ 30m  unit=class missing=0/19182    [derived/annual]
  facilities     vector 3 features ['Point']                         [derived/observation_time]

terrain:
  elevation  : 35.0 - 605.0 m (mean 285.6, relief 570.0 m)
  slope      : mean 22.43 deg, max 51.11 deg (550 cells not computable)
  aspect     : circular mean 99.2 deg (resultant 0.148; 550 cells ignored)

roads (topology QA only - no safety claim):
  55 nodes, 68 edges, 28.78 km, 2 component(s)
  dead ends 13, isolated segments 0, exit nodes 4
  single-egress candidates 0, no-egress components 0
  articulation points 18, bridges 22, critical links 0, unnoded crossings 0

facilities (source-declared roles, unassessed):
  other=3
  sourced capacity 0/3, suitability assessed 0/3

! no layer, field, or metric in this bundle asserts that any road, shelter,
! refuge, or area is safe or passable (docs/SCOPE.md)
```

Three things in that output are worth reading carefully.

The **550 cells with no slope** are the array edge. A 3×3 estimator has no
answer there, and the edge is left `nodata` rather than extrapolated
([D-0005](docs/DECISIONS.md)).

The **2 components** are not what OSM's rural coverage looks like — they are
what it looks like *once noded correctly*. This bundle first reported 22, and
the cause was this repository's own graph builder connecting lines only at their
endpoints when 35 of 41 shared OSM nodes were interior vertices of a way. The
fix collapses 22 to 2 with identical total length. The investigation is
[`reports/ULJIN_ROAD_AUDIT.md`](reports/ULJIN_ROAD_AUDIT.md), and it is the
reason `RD-001` now says a component count is *often a missing junction rather
than a real disconnection*.

**`other=3`** means all three facilities carry no refuge role, and that is a
finding rather than missing work: one is a `shelter_type=gazebo` and one is the
*site of* a demolished school.

## What it does

| Area | Provides |
|---|---|
| **Terrain** | DEM ingestion, explicit reprojection, grid-preserving clipping, Horn (1981) slope and aspect, nodata-aware statistics with circular aspect means |
| **Roads** | Undirected graph QA: components, node degrees, dead ends, isolated segments, articulation points, multiplicity-aware bridges, settlement-to-exit critical links, single-egress *candidates*, unnoded-crossing diagnostics |
| **Fuels** | Ingestion with declared class schemes, and ESA WorldCover 2021 wired in as **land cover**. **No fuel-model crosswalk is shipped or implied** — land cover is not a fire-behaviour fuel model (D-0024) |
| **Population** | Aggregate settlement data — totals, half-open age strata, village geometry, settlement centroids — with a load-time privacy guard |
| **Facilities** | Generic loaders for shelters, temporary-refuge candidates, responder bases, fire stations. Presence in a dataset is never fitness for purpose |
| **Provenance** | Source, dates, transformations, original and output CRS, resolution, units, vertical datum, nodata representation, SHA-256 checksum, temporal class |
| **Validation** | 51 severity-graded checks across CRS, raster, vector, provenance, population, facility, road and bundle integrity |

## What it deliberately does not do

No wildfire prediction, no forecast comparison, no evacuation routing, no rescue
routing, no dispatch timing, no OSSE machinery, no safety adjudication, no
person-level or medical data. A pull request adding any of those should be
rejected as out of scope regardless of quality ([`docs/SCOPE.md`](docs/SCOPE.md)).

## Example study areas

| Bundle | Sources | Network |
|---|---|---|
| `uljin_real_v2` | Copernicus DEM + ESA WorldCover + OSM roads and facilities | required to rebuild |
| `naju_real_v1` | the same three, a different geography and belt CRS | required to rebuild |
| `uljin_real_v1` | Copernicus DEM GLO-30 + OpenStreetMap | required to rebuild |
| `uljin_valley_synthetic_v1` | synthetic fixtures only | none |
| `wg_integration_fixture_synthetic_v1` | synthetic fixtures only | none |

All five validate with **zero ERROR findings**.

**No bundle has a population layer.** Every authoritative Korean source is
unreachable from the development environment or needs credentials
([`reports/SOURCE_ACCESS_STATUS.md`](reports/SOURCE_ACCESS_STATUS.md)), and OSM
carries no `population` tag in either study box. Nothing was estimated: the
layer is `ABSENT` and says why, in the bundle's own contract
([D-0031](docs/DECISIONS.md)).

`uljin_real_v2`'s facilities layer contains **zero refuge candidates**, which is
a finding rather than a gap. Two of the three OSM facility elements in that box
would mislead any pipeline that trusted the tag — one `amenity=shelter` is a
`shelter_type=gazebo`, and one `amenity=school` is the *site of* a demolished
school. See [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md) F-FAC-3.

`naju_real_v1` exists to test whether any of this is Uljin-specific
([`reports/SECOND_GEOGRAPHY.md`](reports/SECOND_GEOGRAPHY.md)), and
`wg_integration_fixture_synthetic_v1` is 83 KB with closed-form expected
answers, for another repository's CI.

**Attribution obligations** travel with every real bundle: terrain © ESA /
Copernicus Programme (Copernicus DEM GLO-30); land cover © ESA WorldCover 2021
v200 (CC-BY 4.0); roads and facilities © OpenStreetMap contributors (ODbL 1.0,
share-alike on derived databases).

## Synthetic fixtures

Deterministic, analytically checkable test inputs — the basis of the strongest
tests here:

| Fixture | Known answer |
|---|---|
| `tilted_plane` | Horn is exact on a plane, so slope and aspect are closed-form |
| `flat_terrain` | slope exactly 0; aspect `NaN` everywhere, never `0` (which would mean "faces north") |
| `missing_cells_raster` | the same void as `NaN` **and** as `-9999`, which must summarise identically |
| `single_exit_network` | 1 component, 1 exit, every edge a critical link |
| `two_exit_network` | 1 component, 2 exits, **zero** critical links |
| `disconnected_network` | 2+ components, 1 isolated segment, 1 unnoded crossing |
| `incompatible_crs_pair` | the same geometry in EPSG:5187 and EPSG:4326; any combination must raise |
| `village_shelter_station` | aggregate population and unassessed facilities |
| `korean_valley_*` | composite study area for the example bundle |

## Tests

```bash
python -m pytest -q          # 446 tests, offline and deterministic
python -m pytest -m network  # 3 live-source tests, opt-in
```

The suite is built on closed-form answers rather than snapshots: a tilted
plane's exact slope and aspect, four surfaces falling N/E/S/W to rule out a
transposed axis, `tan(slope)` halving when the cell size doubles, and the
`NaN`-versus-sentinel equality above. Every validation check has a positive case
asserting both its code and its severity. See
[`docs/VALIDATION.md`](docs/VALIDATION.md).

## Documentation

Read in this order — and if you are an agent or a new contributor, **read them
before editing code**, as [`AGENTS.md`](AGENTS.md) requires:

| Document | What it holds |
|---|---|
| [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) | what this repository is and is not for |
| [`docs/RESEARCH_QUESTION.md`](docs/RESEARCH_QUESTION.md) | the claim the code must support, and what would falsify it |
| [`docs/SCOPE.md`](docs/SCOPE.md) | the hard boundary, with the boundary cases resolved |
| [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md) | every scientific assumption and where it is enforced |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | append-only log: decision, alternatives rejected, consequence |
| [`docs/DATA_PROVENANCE.md`](docs/DATA_PROVENANCE.md) | what is recorded, what was obtained, what failed and why |
| [`docs/VALIDATION.md`](docs/VALIDATION.md) | the check catalogue, and what validation does *not* check |
| [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md) | how outputs can still be wrong: caught, not caught, and by design |
| [`docs/INTERFACES.md`](docs/INTERFACES.md) | the stable CLI and the published bundle contract, and what stays unstable |
| [`docs/GLOSSARY.md`](docs/GLOSSARY.md) | terms as this repository uses them |
| [`tasks/`](tasks/) | roadmap, work in flight, and what is finished |

And the investigations, which are evidence rather than narrative:

| Report | What it establishes |
|---|---|
| [`reports/PHASE2_INTEGRATION_READINESS.md`](reports/PHASE2_INTEGRATION_READINESS.md) | what may and may not be depended on at `v0.2.0` |
| [`reports/ULJIN_ROAD_AUDIT.md`](reports/ULJIN_ROAD_AUDIT.md) | the 22 road components were a pipeline defect, not sparse data |
| [`reports/SECOND_GEOGRAPHY.md`](reports/SECOND_GEOGRAPHY.md) | the schema is not Uljin-specific, and where that is still untested |
| [`reports/SOURCE_ACCESS_STATUS.md`](reports/SOURCE_ACCESS_STATUS.md) | every source, classified — and why nothing is marked "unavailable" |
| [`reports/KOREAN_FUELS_CANDIDATES.md`](reports/KOREAN_FUELS_CANDIDATES.md) | 임상도 and the alternatives, on nine axes each |
| [`reports/LEGACY_DATA_PIPELINE_COMPARISON.md`](reports/LEGACY_DATA_PIPELINE_COMPARISON.md) | measured comparison with the main repository's pipeline |
| [`reports/BENCHMARK_CROSS_CHECK.md`](reports/BENCHMARK_CROSS_CHECK.md) | an independent suite's 8 relevant cases, run for the first time |

## Status

**Phase 2 complete, frozen at `v0.2.0`.** Five committed bundles with zero ERROR
findings, two real Korean geographies in different belt CRSs, a versioned
downstream contract that cannot drift from its own data, 31 decision records,
and **446 automated tests** (plus 3 opt-in live-source tests, which also pass).
See [`tasks/COMPLETED.md`](tasks/COMPLETED.md) for what was built — including
what turned out to be **wrong** — and
[`tasks/CURRENT.md`](tasks/CURRENT.md) for what is left.

### What a downstream repository may depend on

`bundle_manifest.json` at `bundle_schema_version` 1.0.0, and the `wg-data` CLI.
That is the contract ([D-0027](docs/DECISIONS.md),
[`docs/INTERFACES.md`](docs/INTERFACES.md)).

`StudyAreaBundle`, the Python API, `manifest.json` and the provenance sidecars
remain **internal and unstable** ([D-0001](docs/DECISIONS.md)).

Two things are worth stating before anyone builds on this. There is **no
population data**, anywhere. And `READY` from `wg-data compatibility` means only
that a consumer's named inputs are present with interpretable provenance — an
entirely synthetic bundle is `READY` for all three consumer profiles, which is
what a fixture is for ([D-0028](docs/DECISIONS.md)).

## Licence

MIT for the code. Data carries its own source licences — see
[`docs/DATA_PROVENANCE.md`](docs/DATA_PROVENANCE.md).
