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

# Build from real open data (Copernicus DEM GLO-30 + OpenStreetMap)
wg-data build-study-area configs/uljin_real_copernicus_osm.yaml --allow-network

# Check a bundle on disk, including file checksums against provenance
wg-data validate-study-area data/study_areas/uljin_valley_synthetic_v1
wg-data validate-study-area <bundle> --strict --json

# Describe what a bundle contains, including its gaps
wg-data summarize-study-area <bundle>

# Write the synthetic fixtures out for inspection
wg-data make-fixtures /tmp/fixtures
wg-data list-fixtures
```

Exit codes and the `--json` contract: [`docs/INTERFACES.md`](docs/INTERFACES.md).

### What a summary looks like

```
study area : uljin_real_v1
CRS        : EPSG:5187
bounds     : (227500.0, 478500.0) - (231500.0, 482500.0)  [4000 x 4000 m]

layers:
  dem            raster 139x138 @ 30m  unit=m    missing=0/19182    [derived/static]
  slope_deg      raster 139x138 @ 30m  unit=deg  missing=550/19182  [derived/static]
  aspect_deg     raster 139x138 @ 30m  unit=deg  missing=550/19182  [derived/static]
  roads          vector 31 features ['LineString']                  [derived/observation_time]

terrain:
  elevation  : 35.0 - 605.0 m (mean 285.6, relief 570.0)
  slope      : mean 22.43 deg, max 51.11 deg (550 cells not computable)
  aspect     : circular mean 99.2 deg (resultant 0.148; 550 cells ignored)

roads (topology QA only - no safety claim):
  53 nodes, 31 edges, 28.78 km, 22 component(s)
  dead ends 43, isolated segments 17, exit nodes 1
  single-egress candidates 0, no-egress components 0
  articulation points 9, bridges 31, critical links 0, unnoded crossings 2
```

The 550 cells with no slope are the array edge — a 3×3 estimator has no answer
there, and the edge is left as `nodata` rather than extrapolated. The 22
components are what OpenStreetMap's rural coverage actually looks like, reported
rather than smoothed over.

## What it does

| Area | Provides |
|---|---|
| **Terrain** | DEM ingestion, explicit reprojection, grid-preserving clipping, Horn (1981) slope and aspect, nodata-aware statistics with circular aspect means |
| **Roads** | Undirected graph QA: components, node degrees, dead ends, isolated segments, articulation points, multiplicity-aware bridges, settlement-to-exit critical links, single-egress *candidates*, unnoded-crossing diagnostics |
| **Fuels** | A generic ingestion architecture with declared class schemes. **No Korean fuel dataset or crosswalk is invented** |
| **Population** | Aggregate settlement data — totals, half-open age strata, village geometry, settlement centroids — with a load-time privacy guard |
| **Facilities** | Generic loaders for shelters, temporary-refuge candidates, responder bases, fire stations. Presence in a dataset is never fitness for purpose |
| **Provenance** | Source, dates, transformations, original and output CRS, resolution, units, vertical datum, nodata representation, SHA-256 checksum, temporal class |
| **Validation** | 43 severity-graded checks across CRS, raster, vector, provenance, population, facility, road and bundle integrity |

## What it deliberately does not do

No wildfire prediction, no forecast comparison, no evacuation routing, no rescue
routing, no dispatch timing, no OSSE machinery, no safety adjudication, no
person-level or medical data. A pull request adding any of those should be
rejected as out of scope regardless of quality ([`docs/SCOPE.md`](docs/SCOPE.md)).

## Example study areas

| Bundle | Sources | Network |
|---|---|---|
| `data/study_areas/uljin_valley_synthetic_v1` | synthetic fixtures only | none |
| `data/study_areas/uljin_real_v1` | Copernicus DEM GLO-30 + OpenStreetMap | required to rebuild |

`uljin_real_v1` has **no** fuels, population, or facilities layer, because that
data was not obtained. Those components are absent rather than filled with
plausible values ([D-0015](docs/DECISIONS.md)); the attempts and their outcomes
are recorded in
[`docs/DATA_PROVENANCE.md`](docs/DATA_PROVENANCE.md#access-attempts--what-was-tried-and-what-happened).

**Attribution obligations** travel with `uljin_real_v1`: terrain © ESA /
Copernicus Programme (Copernicus DEM GLO-30, free use with attribution); roads ©
OpenStreetMap contributors (ODbL 1.0, share-alike on derived databases).

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
python -m pytest -q          # 277 tests, no network, no fixed random state
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
| [`docs/INTERFACES.md`](docs/INTERFACES.md) | the stable CLI, and why no data contract is published yet |
| [`docs/GLOSSARY.md`](docs/GLOSSARY.md) | terms as this repository uses them |
| [`tasks/`](tasks/) | roadmap, work in flight, and what is finished |

## Status

Phase 1 (foundation) is complete: installable package, working CLI, provenance
system, CRS validation, terrain preprocessing, road QA, synthetic fixtures, two
example study-area bundles, 277 automated tests, and documented failure modes.
See [`tasks/COMPLETED.md`](tasks/COMPLETED.md) for what was built and
[`tasks/CURRENT.md`](tasks/CURRENT.md) for what should happen next.

`StudyAreaBundle` and the on-disk bundle layout are **internal and unstable**.
No downstream WildfireGuardian repository should depend on them yet
([D-0001](docs/DECISIONS.md)).

## Licence

MIT for the code. Data carries its own source licences — see
[`docs/DATA_PROVENANCE.md`](docs/DATA_PROVENANCE.md).
