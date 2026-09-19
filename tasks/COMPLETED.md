# COMPLETED

## Phase 1 — Foundation (2026-09-19)

Built the standalone geospatial data foundation. Every item below is exercised
by the test suite or by a committed bundle; nothing here is aspirational.

### Context system

`README.md`, `AGENTS.md`, and `docs/`: `PROJECT_CONTEXT`, `RESEARCH_QUESTION`,
`SCOPE`, `ASSUMPTIONS`, `DECISIONS`, `DATA_PROVENANCE`, `INTERFACES`,
`VALIDATION`, `FAILURE_MODES`, `GLOSSARY`; plus `tasks/`, `tests/`,
`experiments/`.

`AGENTS.md` requires every future agent to read `PROJECT_CONTEXT`,
`RESEARCH_QUESTION`, `SCOPE`, `ASSUMPTIONS`, `DECISIONS` and `tasks/CURRENT.md`
before editing code, and forbids silent changes to scientific assumptions,
units, CRS semantics, time semantics, schemas, missing-data semantics, or
validation severity.

`docs/ASSUMPTIONS.md` records 40 numbered assumptions, each with where it is
enforced. `docs/DECISIONS.md` holds 15 decisions with the alternatives rejected
and the consequence a future reader must live with.

### Installable package

`src/wildfireguardian_data/`, installable with `pip install -e .`, entry point
`wg-data`. Core depends only on numpy, pyproj, shapely, networkx and pyyaml —
the scientific core runs without GDAL (D-0003), with rasterio/geopandas behind a
`[geo]` extra and a `.npz` + JSON sidecar fallback format.

| Module | What was built |
|---|---|
| `errors` | 22 named exceptions, so every failure mode this repository cares about is loud and specific |
| `units` | length / slope / time / area units as data, with the non-linear percent-rise conversion and a refusal to map the US survey foot onto the international foot |
| `crs` | parse, compare, require-same, require-projected-metre; Korean CRS notes **verified against the installed PROJ database by a test**; axis order decided from axis *direction*, which is what makes the EPSG:5179/5186/5187 `X`-is-northing trap detectable |
| `bounds` | CRS-carrying boxes; inverted boxes rejected, zero-extent boxes allowed but refused where an extent is required |
| `raster` | `GridTransform` that cannot express rotation, and `RasterLayer` carrying array + grid + CRS + nodata + kind + units + provenance |
| `vector` | `Feature` / `VectorLayer`, GeoJSON I/O that refuses to default an undeclared CRS to EPSG:4326, explicit recorded reprojection |
| `provenance` | `SourceRecord`, `Transformation`, `ProvenanceRecord`; `UNKNOWN` and `not_applicable` as distinct required literals; timezone-aware timestamps only; SHA-256 over files and over arrays including dtype and shape; JSON sidecars |
| `terrain` | GeoTIFF and npz I/O, explicit reprojection, grid-preserving clipping, Horn (1981) slope and aspect, nodata-aware statistics, circular aspect means with a resultant length |
| `roads` | multigraph construction with union-find endpoint clustering; components, degrees, dead ends, isolated segments, articulation points, multiplicity-aware bridges, settlement-to-exit critical links, single-egress candidates, unnoded-crossing diagnostics |
| `fuels` | generic ingestion; `FuelClassScheme` that refuses to exist without a source unless declared synthetic; nearest-only resampling; rasterisation filling with nodata rather than class 0 |
| `population` | aggregate models with half-open age strata; load-time privacy guard over 20 person-level and medical name patterns |
| `facilities` | source-declared roles, `UNKNOWN` operational status, unassessed suitability, capacity never estimated from footprint |
| `study_area` | strict YAML config (unknown keys rejected), build pipeline, bundle, serialisation with checksums, summary |
| `validation` | 46 severity-graded findings across CRS, raster, vector, provenance, population, facility, road and bundle-integrity checks |
| `cli` | `build-study-area`, `validate-study-area`, `summarize-study-area`, `make-fixtures`, `list-fixtures`, `version`, with documented exit codes and a stable `--json` contract |
| `sources` | Copernicus DEM GLO-30 windowed COG reads and OpenStreetMap API fetches, both opt-in on `allow_network` |
| `fixtures` | 13 deterministic synthetic generators, all labelled `SYNTHETIC` in provenance |

### Mandatory safeguards, as implemented

- **CRS** — `require_same_crs` is the single choke point; mismatch raises
  `CRSMismatchError`, an unknown CRS raises `UnknownCRSError`, and two unknown
  CRSs are **not** treated as equal. Geographic CRSs are refused for slope and
  length work.
- **Units** — carried on every layer; mixing a foot vertical unit with a metre
  grid raises rather than rescaling.
- **Missing values** — never converted to zero, in any path: statistics use the
  valid mask, reprojection carries voids as `NaN` so a sentinel cannot be
  averaged into a real value, an integer layer with no declared nodata refuses
  to reproject, rasterisation fills with the scheme's nodata code, and
  derivative cells beside a void are `nodata` rather than extrapolated.
- **Provenance** — source, URL/identifier, source date, acquisition date,
  transformations with parameters, original and output CRS, spatial resolution,
  and checksum on every artifact.
- **Temporal provenance** — every layer declares `static` / `annual` /
  `monthly` / `observation_time` / `retrospective`, with no default; OSM is
  correctly `observation_time`, and `retrospective` carries an explicit
  data-leakage warning.

### Synthetic fixtures

All eight required fixtures, plus a composite study area: tilted plane (exact
closed-form slope and aspect), flat terrain (slope 0, aspect `NaN`), single-exit,
two-exit and disconnected road networks, an incompatible-CRS pair, a
missing-cells raster in **both** `NaN` and `-9999` form, a village + shelter +
fire-station scene, and `korean_valley_*`.

### Example study-area bundles

| Bundle | Contents |
|---|---|
| `data/study_areas/uljin_valley_synthetic_v1` | complete: terrain (DEM, slope, aspect), roads + QA, fuels + scheme, aggregate population, facilities. Synthetic, deterministic, no network |
| `data/study_areas/uljin_real_v1` | real: Copernicus DEM GLO-30 terrain (35–605 m, mean slope 22.4°) and OpenStreetMap roads (31 segments, 28.8 km, 22 components) for a 4 × 4 km extent in Uljin-gun |

Both validate with **zero ERROR findings**. The synthetic bundle carries
deliberate warnings — inconsistent age strata, a below-threshold count, a
settlement with no egress in its component, an unnoded crossing — so the
validation checks are demonstrably live rather than vacuously passing.

### Tests

**287 tests, passing, offline and deterministic**, plus 3 opt-in tests that fetch from the live sources (`pytest -m network`). Built on closed-form answers rather
than snapshots:

- slope and aspect exact on a tilted plane, to `1e-4` degrees, over six
  gradient combinations;
- four surfaces falling N/E/S/W, which rule out a transposed axis or an upslope
  aspect convention;
- `tan(slope)` halving when the cell size doubles;
- Horn's gradient components recovering a plane's coefficients to `1e-9`, with
  the northward-versus-per-row sign checked directly;
- the same void as `NaN` and as `-9999` summarising **identically**;
- exactly 9 cells lost to one interior void — not 1, not a whole row;
- a two-exit network yielding **zero** critical links where the single-exit
  network yields three;
- parallel edges not reported as bridges;
- byte-identical rebuilds, and tamper detection on read;
- documented Korean CRS facts checked against PROJ rather than trusted;
- every one of the 46 validation findings with a positive case asserting both
  its code and its severity;
- the real-source fetchers: tile naming across all four hemispheres, the
  opt-in network guard, the refusal of non-WGS84 bounds, and the refusal of a
  multi-tile request — plus three opt-in live tests that fetch from Copernicus
  and OpenStreetMap and assert the whole real chain
  (`fetch -> reproject -> clip -> slope`) end to end.

### Real data access

Attempted and **succeeded**: Copernicus DEM GLO-30 (windowed COG reads),
OpenStreetMap API. Attempted and **failed**: VWorld, Korea Forest Service,
KOSIS, SGIS, Overpass, Geofabrik — all as outbound-proxy tunnel closures in this
environment, which is recorded as *environment, not service*, with an explicit
instruction to re-try. The consequence is stated plainly rather than papered
over: the real bundle has no fuels, population or facilities layer, because that
data was not obtained.
