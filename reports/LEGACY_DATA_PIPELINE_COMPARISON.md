# Comparison with the main WildfireGuardian repository's data pipeline

Phase 2 item 4. Read-only comparison against
`Sparkxt-0318/wildfireguardian` at `f6641bc`, cloned shallowly. **Nothing in
that repository was modified**, and this repository imports nothing from it
(`AGENTS.md` §6, Phase 2 item 22).

The brief's two constraints shaped this: *"Do not blindly replace the new
implementation"* and *"Avoid duplicating already-correct data acquisition
logic."* Both cut both ways, so each component below is classified on evidence
rather than on which repository it lives in — and the classification went
against this repository twice.

## Summary

| Component | Classification |
|---|---|
| `scripts/measure_osm_completeness.py` | **PORT_WITH_FIXES** — closes an open gap here |
| `scripts/get_fuel.py` (multi-tile WorldCover mosaic) | **PORT_WITH_FIXES** — capability this repository lacks |
| `scripts/acquire_region_dem.py` (nodata-fraction gate) | **PORT_WITH_FIXES** — the gate, not the loader |
| `scripts/acquire_region_osm.py` (osmnx acquisition) | **PORT_WITH_FIXES** — `retain_all` must be set |
| `data_io/raster.py` xarray + netCDF cache | **REFERENCE_ONLY** |
| `data_io/raster.py::load_landcover` (`me_korea` branch) | **REFERENCE_ONLY** — names a source lead |
| `data_io/raster.py::_srtm_dem_for_region` | **REJECT** — destroys missing data |
| `data_io/raster.py::compute_slope_aspect` | **REJECT** — measured, below |
| `spread_v2/`, `routing/`, `spread_model/`, `smoke_dispersion/` | **REJECT** — out of scope, not a judgement of quality |
| `scripts/register_refuge_population.py` | **REJECT** — that repository's own numbers registry |

---

## REJECT — `_srtm_dem_for_region`: missing data becomes sea level

```python
arr[arr == _SRTM_NODATA] = 0   # -32768 -> 0
arr[arr < 0] = 0               # bathymetry -> 0
```

The docstring states this plainly ("clipping nodata to 0 and bathymetry ... to 0
(sea surface)"), so it is a deliberate choice, not an oversight. It is still the
single defect this repository exists to prevent: an SRTM void in mountainous
terrain becomes 0 m, and the cell beside it stays at 600 m. Horn's estimator
over that pair returns a slope no terrain has, and nothing downstream can tell
it from a real cliff.

**Not theoretical in that repository either.** `acquire_region_dem.py`'s own
header records the consequence: *"elevation outside the raster for 405 of 7,300
walk nodes, read nodata, and times the edge FLAT"*. That is the same failure
from the other end — a nodata read silently becoming flat ground.

This repository's position (A-RAS-3, F-MD-1, `AGENTS.md` §2): missing stays
missing, NaN and a sentinel must summarise identically, and a nodata value is
never converted to 0. Nothing here is portable.

## REJECT — `compute_slope_aspect`: `np.gradient` is not Horn, and it was measured

The legacy docstring claims `np.gradient` is *"equivalent at interior cells to
Horn (1981) eq. 14–15 with a 3 × 3 kernel for sufficiently smooth terrain"*.
That claim is **true on a plane and false on terrain**, which is exactly why it
survived: a test on a planar surface cannot distinguish the two estimators.

Both implementations were run over this repository's `korean_valley_dem`
fixture, 42,436 comparable cells:

| | Result |
|---|---|
| Agreement on a tilted plane | **exact** — both 12.6044° |
| Slope difference, real terrain | mean **1.97°**, p95 4.85°, max **10.45°** |
| Cells differing > 1° in slope | **68.5%** |
| Aspect difference, real terrain | mean **21.2°**, p95 75.4° |
| Cells differing > 5° in aspect | **76.6%** |

`np.gradient` takes a two-cell central difference along each axis and ignores
the diagonals; Horn's kernel weights all eight neighbours. They are different
estimators, and on real terrain they disagree for two-thirds of cells.

**Stated fairly:** the large aspect differences are concentrated where aspect is
genuinely ill-conditioned, not spread evenly, and the 180° cases are not a sign
error:

| Slope band | Mean aspect difference |
|---|---|
| 0–2° | 64.6° |
| 2–5° | 33.4° |
| 5–10° | 15.8° |
| 10–20° | 8.0° |
| > 20° | **5.1°** |

Every cell differing by more than 90° has slope below 8°, mean 2.1°. On steep
ground — the ground that matters for fire behaviour — the two estimators agree
to about 5°. So the honest conclusion is not "the legacy slope is wrong
everywhere"; it is that the two differ materially on gentle terrain and modestly
on steep terrain, and that the documented equivalence claim does not hold.

Two further differences are categorical rather than numerical, and both are
decided behaviour here:

- **Flat ground.** `np.gradient` gives `arctan2(0, 0)` = **0.0**, so every flat
  cell reads as **due north**. Verified: on this repository's `flat_terrain`
  fixture the legacy code returns aspect `0.0` for every cell, where this
  repository returns `NaN` (D-0006). This is not academic — the second
  geography, `naju_real_v1`, has **619 real flat cells**, every one of which
  would have been reported as north-facing.
- **Edges.** `np.gradient` uses one-sided differences at the boundary, so all
  208 edge cells of the test DEM get a slope value. This repository returns
  nodata for all 208 (D-0005), because a 3×3 estimator has no 3×3 neighbourhood
  there and extrapolating is inventing data.

## REJECT — the modelling packages, on scope not quality

`spread_v2/`, `spread_v2_arme/`, `spread_v2_armd/`, `spread_v2_xgb/`,
`spread_model/rothermel/`, `routing/`, `smoke_dispersion/`, `vulnerability/`,
`delivery/`: fire spread, Rothermel fuel models, rescue and evacuation routing,
smoke plumes, alerting. `docs/SCOPE.md` and `AGENTS.md` §6 put all of it outside
this repository, and `spread_model/rothermel/fuel_model.py` is specifically the
land-cover-to-fuel-model crosswalk D-0024 refuses to ship here.

**REJECT here means "belongs elsewhere", not "is wrong."** These are that
repository's purpose.

## REFERENCE_ONLY — the xarray + netCDF cache

`data_io/raster.py` wraps every raster as an `xr.DataArray` with the affine and
CRS as `attrs`, and caches to netCDF keyed on region, source, cell size and
kind. The caching design is sound and the provenance instinct is right —
`_netcdf_safe_attrs` exists precisely so provenance survives serialisation.

Not adopted, because `attrs` is a free-form dict. This repository needs
`ProvenanceRecord` to be a *validated* schema that raises on a missing
`temporal_class` and refuses an unrecognised `surface_model`, with an explicit
migration between versions (D-0025). A dict of attributes cannot enforce that,
and `RasterLayer` already carries unit, CRS and kind on the data itself
(`AGENTS.md` §7).

## REFERENCE_ONLY — `load_landcover`, which names a lead

```python
elif source == "me_korea":
    raise NotImplementedError(
        "ME 토지피복 ingestion is a Session 3 task. ..."
    )
```

Nothing to port — it is unimplemented. But it names a Korean source this
repository's own survey had not: the **환경부 (Ministry of Environment)
토지피복지도** land-cover map. Recorded in
`reports/KOREAN_FUELS_CANDIDATES.md` as a lead with every attribute `UNKNOWN`,
because the only thing verified about it is that another repository intends to
use it. That is not evidence of its resolution, vintage, licence or
classification scheme, and none of those are guessed.

## PORT_WITH_FIXES — `measure_osm_completeness.py`, the best candidate here

This is the component worth taking, and it closes a gap this repository has
documented and not filled. `docs/FAILURE_MODES.md` F-RD-3 says OSM rural
completeness is `UNKNOWN`, and `tasks/CURRENT.md` item 1 makes it the top
priority — but "UNKNOWN" is where this repository stopped. The legacy script
measures it: road density, node density, share of edges with real geometry,
share with a `highway` tag, and POI density, on each region's own denominator.

Its own header states the reasoning better than a paraphrase would: *"A region
whose roads are less completely mapped will look worse on any routing metric,
and that has nothing to do with its terrain or its fire."* And it carries the
right caveat, in the right place: *"⚠ THE DEPOT COLUMN IS NOT A SCORE"*, with a
worked example of a box containing zero mapped fire stations while a larger box
around it contains six.

**Fixes required before porting:** the metrics must be recorded as
`DataClass.DERIVED` with a `Transformation` naming the denominator used, and the
density figures must carry units explicitly rather than in a column name. This
repository's `roads/qa.py::network_density` and `attribute_availability` already
compute two of the five; the remaining three, and the cross-region comparability
framing, are the part to port.

## PORT_WITH_FIXES — multi-tile WorldCover mosaicking

`scripts/get_fuel.py` is 37 lines and does something `fetch_esa_worldcover`
explicitly **refuses**: it mosaics two 3° WorldCover tiles with
`rasterio.merge`, for a box that straddles 129°E. This repository raises
`IngestError` on a multi-tile request rather than returning part of it
(F-BND-5), which is the right default and is also a real limitation — the same
one `tasks/CURRENT.md` item 5 records for the DEM. A study area that crosses a
tile boundary cannot be built here.

**Fixes required:** the seam must be recorded, each tile must become its own
`SourceRecord` (a mosaic has two provenances, not one), and the merge must be
proven not to resample across the seam for a categorical raster.

**And the bug to not port.** The script writes to
`data/raw/firms/yeongdeok_2025_fuel.tif` — a **2021** product, under a
**`firms/`** directory (FIRMS is NASA's fire-detection product, not land cover),
named **2025**. Nothing downstream reading that path could recover the real
vintage. It is a compact illustration of why `AGENTS.md` §3 forbids inferring a
source date from a filename: here the filename would have been wrong by four
years and the product type wrong entirely.

## PORT_WITH_FIXES — the nodata-fraction gate

`acquire_region_dem.py` does two things this repository does not:

1. it records `raster_nodata_fraction` in its provenance output, and
2. it **refuses** a raster more than 50% nodata: *"that is not a usable
   raster"*.

This repository reports a missing fraction over a threshold as `RAS-003`
(WARNING) and has `RAS-002` (ERROR) only for the all-missing case. A gate
between those — mostly-missing is an ERROR, not a warning — is a defensible
position and is currently absent.

**Fix required:** the threshold is a scientific judgement, so it needs a
`DECISIONS.md` entry and a test, not a constant. **Not adopted in this pass** —
promoting a WARNING to an ERROR is a scientific change (`AGENTS.md` §2) and
belongs in its own reviewed change, not appended to a comparison report.

The **loader** around that gate is `REJECT` for the reason above: it feeds the
SRTM path that zeroes nodata. The gate is portable; the thing it gates is not.

## PORT_WITH_FIXES — osmnx acquisition, with `retain_all` set

`acquire_region_osm.py` uses `ox.graph_from_bbox(bbox=..., network_type=kind)`
and records `osmnx_version`, the bbox, and the literal query string in
provenance. That provenance discipline is good and is worth matching.

**The fix is one keyword, and it matters.** `retain_all` is **not set anywhere**
in that repository, so osmnx's default of `False` applies: it returns only the
largest weakly connected component and **silently discards every other one**.

That is why the legacy pipeline appeared to produce a perfectly connected
network over the same Uljin ground where this repository found 22 components —
and it is why the agreement between them (60 edges against 68, ~12%) was
evidence about coverage and not about connectivity. A pipeline that deletes
disconnected components cannot report fragmentation, cannot report an isolated
segment, and cannot report a component with no egress. Those are three of this
repository's road findings (`RD-001`, `RD-002`, `RD-004`), and all three are
structurally unavailable there.

`network_type='drive'` versus this repository's explicit
`DEFAULT_OSM_HIGHWAY_VALUES` is a second difference worth keeping in mind:
osmnx's `drive` filter excludes `track`, and rural Korean access depends on
tracks (`test_default_osm_filter_keeps_rural_access_classes`).

---

## What this comparison changed about *this* repository

The brief warns against blindly replacing the new implementation. It is worth
recording that the comparison went the other way twice:

1. **`measure_osm_completeness.py` is better than anything here** on a gap this
   repository documented and left open. `UNKNOWN` was honest; it was not
   sufficient, and a component to fix it already existed.
2. **Multi-tile mosaicking is a real capability this repository lacks**, and
   refusing the request is the correct *default* rather than a complete answer.

And one claim in this repository's own Phase 2 write-up is now better
supported: the legacy pipeline's apparent perfect connectivity is an artifact of
`retain_all=False`, which is confirmed by its absence from the source rather
than inferred from behaviour.

## What was not examined

- `spread_v2/grid.py` and the CRS handling inside the modelling packages, beyond
  noting they use EPSG:5179 nationwide. This repository uses belt CRSs (5186 /
  5187) because 5179's meridian convergence reaches ±1.1° nationwide against
  +0.19–0.21° for 5187 over Uljin — a real difference for aspect, but comparing
  their grid code is not needed to classify a data component.
- `data_io/weather.py`, out of scope here.
- Whether the legacy SRTM and this repository's Copernicus GLO-30 agree over the
  same ground. That is a worthwhile measurement and a different task: SRTM and
  GLO-30 are different products with different vintages and both are surface
  models, so a disagreement would need decomposing before it meant anything.
