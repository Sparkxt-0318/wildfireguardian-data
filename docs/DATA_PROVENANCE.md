# DATA_PROVENANCE

What is recorded for every artifact, what real sources this repository actually
obtained, and what it tried and failed to obtain.

**The rule that governs this file:** nothing goes in it that was not verified.
No dataset name, agency, identifier, URL, or licence term is written here from
recollection. Where a fact could not be checked, it says so
(`AGENTS.md` §3, §4).

## What every artifact records

Each layer carries a `ProvenanceRecord` (`provenance/<layer>.provenance.json`
in a bundle) with:

| Field | Meaning |
|---|---|
| `layer_name` | must match the layer and the sidecar filename |
| `data_class` | `observed` / `modeled` / `derived` / `synthetic` — no default |
| `temporal_class` | `static` / `annual` / `monthly` / `observation_time` / `retrospective` — no default |
| `temporal_reference` | the period the values describe |
| `sources[]` | name, URL or identifier, `source_date`, `acquisition_date`, publisher, licence, licence URL, notes |
| `transformations[]` | ordered operations with their parameters, timestamps, and notes |
| `original_crs` / `output_crs` | authority strings, or `UNKNOWN` |
| `spatial_resolution` + `resolution_unit` | `(x_size, y_size)` as a pair, never one number |
| `value_unit` | unit of the cell or attribute values |
| `vertical_datum` | for elevation; `UNKNOWN` unless the source states it |
| `nodata_representation` | `nan`, a sentinel, or `none_declared` |
| `checksum` + `checksum_algorithm` | SHA-256 of the artifact as written |
| `parents[]` | layers this one was derived from |
| `random_seed` | for any synthetic construction |

`UNKNOWN` is a value, not an omission (`DECISIONS.md` D-0009). `not_applicable`
is distinct from `UNKNOWN`: a synthetic generator has no source date, which is
different from having one nobody looked up.

## Sources actually obtained

### Copernicus DEM GLO-30 — terrain — **obtained**

| | |
|---|---|
| What | 30 m (1 arc-second) global **digital surface model** |
| Access | windowed COG reads over HTTPS from the public AWS bucket `copernicus-dem-30m.s3.amazonaws.com` (verified: HTTP 206 on range requests, and a successful windowed read of tile `N36_00_E129_00`) |
| Native CRS | EPSG:4326 (read from the tile metadata, not assumed) |
| Vertical datum | EGM2008 geoid, per the product specification |
| `source_date` | `2011`, year precision. The product is compiled from TanDEM-X acquisitions spanning **2011–2015**; the range is stated in the source notes rather than collapsed into a single date |
| `temporal_class` | `static` |
| `data_class` | `observed` at fetch; `derived` once reprojected or clipped (see below) |
| Licence | free use with attribution to ESA / Copernicus. **The precise licence document was not retrievable from this environment**, so the record says so and cites the collection-description page that *was* reachable, rather than citing a document this repository has not read |
| Cell geometry caveat | the nominal 30 m is at the equator; at 36.9° N cells are ≈30 m north–south and ≈24 m east–west, so a 30 m metre grid is a **resampling choice**, recorded as one |
| Scientific caveat | **DSM, not DTM**: includes canopy and buildings, so slope over Korean forest is canopy slope (`FAILURE_MODES.md` F-TER-3) |

### OpenStreetMap — roads — **obtained**

| | |
|---|---|
| What | highway ways for a small bounding box |
| Access | `https://api.openstreetmap.org/api/0.6/map?bbox=...` (verified: HTTP 200, ~1.3 MB, 21 ways for a 0.04° × 0.04° Uljin box). Limited by the API to 0.25 square degrees and 50 000 nodes |
| Native CRS | EPSG:4326 |
| `source_date` | the download timestamp: OSM is continuously edited, so the data describes the world *as edited up to that moment* |
| `temporal_class` | `observation_time` — **not** `static`. A road layer downloaded today is not a statement about last year |
| `data_class` | `observed` at fetch; `derived` once reprojected or clipped |
| Licence | **ODbL 1.0** (verified). Share-alike applies to derived databases, and attribution to OpenStreetMap contributors is required. A bundle containing this layer carries those obligations |
| Completeness | **UNKNOWN**. Rural Korean coverage is uneven, so the network is a lower bound on what exists, and any egress count derived from it is a statement about the data (`FAILURE_MODES.md` F-RD-3) |
| Filtering | `highway` values kept are listed in `sources.DEFAULT_OSM_HIGHWAY_VALUES`. `track` and `unclassified` are **included**, because rural Korean access depends on them; footways and paths are excluded as non-vehicle access. That is a filtering decision, not a usability claim |

### In-repository synthetic fixtures — **generated**

`wildfireguardian_data.fixtures.synthetic`. `data_class = synthetic`,
`source_date = not_applicable`, deterministic, seeds recorded. Described in
`fixtures/synthetic.py`'s module docstring and in `GLOSSARY.md`.

## `observed` becoming `derived`

A layer transformed by this repository is `derived`, even when its source was
`observed`: a reprojected DEM's values have been resampled and are no longer the
measured values at those locations. Nothing is lost by this — the origin stays
in `sources[]`, and the `transformations[]` chain shows whether values were
resampled (`reproject_raster` present) or merely subset (`clip_raster` only).
See `DECISIONS.md` D-0013.

## Access attempts — what was tried and what happened

Recorded because a failed attempt is a result (`AGENTS.md` §4). Probed
2026-09-19 from this repository's development environment.

**Read this carefully before concluding a source is unavailable.** Several
failures below are `HTTP 000`, meaning the request died in *this environment's*
outbound proxy (the proxy's own status endpoint reports
`ws_closed_mid_exchange` tunnel closures for these hosts). That is **not**
evidence that the service is down, blocked in Korea, or unsuitable. A future
agent on a normal network should re-try every one of them.

| Source | Result | Interpretation |
|---|---|---|
| `copernicus-dem-30m.s3.amazonaws.com` | **200 / 206**, windowed read succeeded | used for terrain |
| `api.openstreetmap.org` | **200**, 21 ways returned for the test box | used for roads |
| `data.go.kr` (Korea Open Data Portal) | 200 reachable | **not used**: the datasets needed require an API key and per-dataset application. Not attempted further; a real deployment should |
| `aihub.or.kr` | 200 reachable | not investigated |
| `portal.opentopography.org` | 200 reachable | not used; Copernicus sufficed |
| `urs.earthdata.nasa.gov` | 200 reachable | not used; requires an account |
| `cloud.sdsc.edu` SRTM path | 401 | requires credentials |
| `map.ngii.go.kr` (National Geographic Information Institute) | 400 | endpoint probed was wrong; NGII is the authoritative Korean mapping agency and **should** be the preferred road/terrain source for real work |
| `vworld.kr` (NGII open API portal) | 000 (tunnel closed) | environment, not service. **Re-try.** Likely the best source for authoritative Korean road and administrative data |
| `forest.go.kr` (Korea Forest Service) | 000 (tunnel closed) | environment, not service. **Re-try.** The likeliest authoritative source for Korean forest-type / vegetation data |
| `kosis.kr` (Statistics Korea) | 000 (tunnel closed) | environment, not service. **Re-try** for aggregate population |
| `sgis.kostat.go.kr` (Statistical Geographic Information Service) | 000 (tunnel closed) | environment, not service. **Re-try** for village-level boundaries and aggregate population |
| `overpass-api.de` and two mirrors | 000 (tunnel closed) | environment, not service. Overpass would lift the OSM bbox size limit |
| `download.geofabrik.de` | 000 (tunnel closed) | environment, not service. Would give a whole-country OSM extract |

> **Superseded for access status.** The table above is the Phase 1 probe.
> `reports/SOURCE_ACCESS_STATUS.md` re-probed every host on 2026-09-20 and
> classifies each under the Phase 2 taxonomy; read that first. It also corrects
> one Phase 1 reading: `map.ngii.go.kr`'s HTTP 400 was taken as evidence that
> the endpoint was wrong, and today the host is proxy-blocked, so we have no
> evidence about the endpoint at all.

### Consequences of those failures, stated plainly

- **No *Korean* vegetation or fuel dataset was obtained.** A global one now is:
  ESA WorldCover 10 m 2021 v200, CC-BY 4.0, carried as **land cover** with ESA's
  own legend (`DECISIONS.md` D-0024). It is not a fuel model and no crosswalk to
  one is shipped (`ASSUMPTIONS.md` A-FU-1).
  The best Korean candidate, 임상도 (forest type map, 1:5,000, EPSG:5179, with
  species/age/diameter/density), was **found and fully documented** but is
  `REGISTRATION_REQUIRED` and carries an unresolved licence contradiction — see
  `reports/KOREAN_FUELS_CANDIDATES.md`. It is not ingested, so nothing derived
  from it is committed.
- **No real aggregate population was obtained.** `uljin_real_v1` has **no
  population layer**.
- **No real facility data was obtained.** `uljin_real_v1` has **no facilities
  layer**.
- **No authoritative Korean road data was obtained.** The real bundle uses OSM,
  whose rural completeness is UNKNOWN. NGII or VWorld data would be
  preferable and is the first item in `tasks/CURRENT.md`.

## Committed bundles

| Bundle | Sources | Committed | Notes |
|---|---|---|---|
| `data/study_areas/uljin_valley_synthetic_v1` | synthetic fixtures only | yes | fully reproducible with no network; what CI builds |
| `data/study_areas/uljin_real_v1` | Copernicus DEM GLO-30 + OpenStreetMap | yes | carries ESA/Copernicus attribution and ODbL obligations; terrain and roads only |
| `data/study_areas/wg_integration_fixture_synthetic_v1` | synthetic fixtures only | yes | the downstream CI fixture (Phase 2 item 29). 83 KB, all five layers, closed-form expected results. No licence obligations: nothing in it came from anywhere |

Raw fetched source data lives in `data/raw/` and is **git-ignored**
(`DECISIONS.md` D-0012). Reproducibility comes from provenance + checksums + a
documented re-fetch command, not from storing tiles in git:

```bash
wg-data build-study-area configs/uljin_real_copernicus_osm.yaml --allow-network
```
