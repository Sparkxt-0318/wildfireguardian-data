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
| `validation` | 51 severity-graded findings across CRS, raster, vector, provenance, population, facility, road and bundle-integrity checks |
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

**332 tests, passing, offline and deterministic**, plus 3 opt-in tests that fetch from the live sources (`pytest -m network`). Built on closed-form answers rather
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
- every one of the 51 validation findings with a positive case asserting both
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

### Independent audit and verification round

Phase 1 was reviewed twice before being called done, in the two roles
`AGENTS.md` §9 defines. Both reviews worked against the committed code and were
told not to trust the test suite.

**A scientific / data audit** of the assumptions against the implementation, and
**an independent verification** using synthetic geometries with analytically
known answers, together confirmed **20 reproducible defects**. All are fixed,
each with a test that fails before the fix and passes after it. The most
consequential:

| What was wrong | Why it mattered |
|---|---|
| `critical_links` never compared against the pre-removal state | A settlement with no exit *to begin with* was attributed to every bridge anywhere in the graph. On the example bundle the count read 7 where 2 was correct, and the error grew with road-data fragmentation — worst exactly where the data is weakest |
| `read_geotiff` defaulted `value_unit` to metres and read the file's unit tag only into a note | A DEM in feet was labelled metres: slope 45° where the truth was 16.95°, on a layer whose provenance asserted metres. A-TER-8's enforcement could never fire, because the mislabelling happened at ingest |
| `terrain_statistics` passed the raw aspect array to the circular mean | A declared `-9999` nodata survived and was read as a real north-easterly azimuth, shifting the dominant aspect by 17°. GDAL's own `gdaldem aspect` defaults to `-9999`, so an externally produced layer was the *normal* case |
| The pipeline reprojected sibling rasters independently | A DEM and a fuel raster from different Korean belts landed a third of a cell apart, displacing every fuel value relative to the DEM cell indexing it |
| The build pipeline defaulted `temporal_class` per component | A retrospective road layer was labelled `observation_time` — data leakage, for a project whose first question is forecast-versus-trigger |
| The Copernicus record claimed EGM2008 was "confirmed against the tile metadata" | It is not in the tile. The claim was a false *verification* claim, which this repository's own rules forbid more strictly than a wrong value |

Also fixed: `resolution_unit` hard-coded to metres in four places including a
shipped fixture on a degree grid; an integer nodata outside the array dtype's
range accepted, after which the layer claimed full coverage; `terrain_statistics`
combining up to three layers with no CRS or grid check; a CRS with no authority
code unable to survive a round trip; sub-cell fuel polygons burning nothing while
provenance reported them burned; settlement matching doing a metre distance
comparison with no CRS check; two `except Exception` blocks swallowing failures;
a config docstring example whose coordinates placed the study area 1,000 km
outside its own CRS; a false statement in `convert_slope`'s docstring; `UNKNOWN`
used where `not_applicable` was correct, diluting the incompleteness metric; and
`pytest` not actually excluding the network-marked tests it documented as
excluded.

**Integrity and licensing**, both found by review: only layer files were
checksummed, so editing `roads_qa.json` — the artifact a downstream reader is
most likely to consume without re-deriving it — went undetected, as did editing
a provenance sidecar. Bundle schema 1.1.0 covers the extras and the sidecars,
aggregates per-layer licences into the manifest, and adds `crs_wkt`. A root
`LICENSE` now states that MIT covers the code while the committed data carries
its sources' terms, and the ODbL share-alike obligation reaches the manifest's
caveats.

**Provenance gained facts the reviews turned up**, verified first-hand from the
pages this repository cites: Copernicus GLO-30 is **mixed provenance** (gaps
filled from older models, which concentrate in radar-shadow terrain — steep
Korean valleys); it is an **edited** DSM with flattened water bodies and edited
shorelines; the AWS mirror **trimmed each tile's shared edges**, so these bytes
are not ESA's; and the tile list is **mutable**, so a re-fetch is not guaranteed
to reproduce them.

**What the reviews confirmed correct** is recorded too, because the report is
evidence and not only a defect list: the (easting, northing) guarantee holds end
to end through GeoJSON write/read and reprojection for a (Northing,
Easting)-authority CRS; slope and aspect agree with GDAL's own
`GDALDEMProcessing` to 4e-4 degrees on a tilted plane and converge at textbook
O(h²) on curved surfaces; the aspect convention is confirmed downslope-clockwise-
from-grid-north with six wrong conventions each excluded by at least 90°;
`clip_raster` preserves the parent grid exactly including negative-index windows;
missing-data statistics match hand-computed references to 1e-14 for both `NaN`
and sentinel spellings; and two builds of the example bundle are bitwise
identical while a single flipped bit is detected.

One review finding was left as documentation rather than code: the
grid-north/true-north convergence is still not corrected in the aspect layer,
but it is now **quantified** — `crs.meridian_convergence_deg` returns it, and
the magnitudes for the shipped study areas are in `FAILURE_MODES.md` F-TER-5.
