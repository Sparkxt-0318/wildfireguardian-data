# ASSUMPTIONS

Every assumption below is **explicit and changeable, but never silently**.
Changing any line in this file requires a corresponding entry in `DECISIONS.md`
and, where the assumption is enforced in code, a failing-then-passing test.

Where a value is genuinely not known, this file says `UNKNOWN`. `UNKNOWN` is a
first-class value in this repository, not a placeholder to be filled with a
plausible guess.

## Coordinate reference systems

| # | Assumption | Enforcement |
|---|---|---|
| A-CRS-1 | The analysis CRS for a Korean study area is **projected**, metre-based, and stated per study area in its config. There is no repository-wide default analysis CRS. | `study_area.config` requires `crs`; `crs.require_projected_metre_crs` rejects geographic CRS for length/slope work. |
| A-CRS-2 | `EPSG:5179` (Korea 2000 / Unified CS, metres) is the *preferred* analysis CRS for national-extent Korean work; `EPSG:5186` (Korea 2000 / Central Belt 2010) and `EPSG:32652` (WGS 84 / UTM 52N) are accepted alternatives. Preference is not enforcement. | `crs.KOREAN_CRS_NOTES` documents each; no code silently substitutes one for another. |
| A-CRS-3 | Two CRSs are "the same" only if `pyproj.CRS.equals` says so, or if both are `None`-free and their authority codes match exactly. Axis-order-only differences are **not** treated as sameness. | `crs.crs_equal`, `crs.require_same_crs`. |
| A-CRS-4 | Reprojection is never implicit. A caller that wants a reprojection calls a reprojection function, which appends a `Transformation` to provenance. | `terrain.reproject`, `vector.reproject_vector_layer`. |
| A-CRS-5 | A layer with `crs = None` (unknown CRS) may be *loaded* but may never participate in a cross-layer operation. | `crs.require_same_crs` raises `UnknownCRSError` on `None`. |

## Elevation, slope, and aspect

| # | Assumption | Enforcement |
|---|---|---|
| A-TER-1 | A DEM's vertical unit is **declared by the caller, never defaulted**, and is cross-checked against the file's band unit tag where one exists. The vertical datum is `UNKNOWN` unless the source states it, and provenance names *where* it was verified. | `read_geotiff` requires `value_unit` and raises on disagreement (D-0016); `SourceSpec.require_declared_semantics`; `ProvenanceRecord.vertical_datum`. |
| A-TER-2 | A DEM is a **surface model unless the source says otherwise**. Copernicus GLO-30 is a DSM (includes canopy and buildings), not a DTM. Slope derived from a DSM over forest is *canopy* slope, not ground slope. | Recorded in provenance `notes`; `docs/FAILURE_MODES.md` F-TER-3. |
| A-TER-3 | Slope and aspect are computed by **Horn's (1981) 3×3 method**, the same estimator as GDAL/ArcGIS defaults, on a **north-up, axis-aligned, projected** grid. | `terrain.derivatives`; `DECISIONS.md` D-0004. |
| A-TER-4 | Slope is reported in **degrees** by default (0–90). Percent-rise and radians are available by explicit request. The unit is carried on the returned layer. | `SlopeUnit`, `terrain.derivatives.slope`. |
| A-TER-5 | Aspect is **degrees clockwise from grid north (0–360)**, giving the **downslope-facing** direction, on **grid north, not true north**. Grid-versus-true north convergence is not corrected and can reach several degrees in Korea. | `terrain.derivatives.aspect`; `FAILURE_MODES.md` F-TER-5. |
| A-TER-6 | Aspect is **undefined where slope is exactly zero** and is returned as `NaN` there, never as `0` (which would falsely mean "faces north") and never as `-1`. | `terrain.derivatives.aspect`, `tests/test_terrain_derivatives.py`. |
| A-TER-7 | Derivative cells whose 3×3 neighbourhood touches the array edge or any `nodata` cell are `nodata`. The valid region therefore shrinks by one cell per derivative pass. Edge values are **not** replicated, mirrored, or extrapolated. | `terrain.derivatives`; `DECISIONS.md` D-0005. |
| A-TER-8 | Horizontal and vertical units must be the **same length unit** for slope to be meaningful. Mixing metre horizontal with foot vertical is an error, not a scaling opportunity. | `terrain.derivatives` raises `UnitMismatchError`. |

## Rasters

| # | Assumption | Enforcement |
|---|---|---|
| A-RAS-1 | Rasters are **north-up and axis-aligned** (affine with zero rotation terms). Rotated rasters are rejected rather than approximately handled. | `raster.RasterLayer.__post_init__`. |
| A-RAS-2 | A raster cell's value applies to the **whole cell area**, and the transform maps to the **upper-left corner** of cell (0, 0) (GDAL convention). Cell centres are `+0.5` cell. | `raster.RasterLayer.cell_center_coords`. |
| A-RAS-3 | `nodata` is part of the layer's identity. A raster loaded with `nodata = None` is treated as *having no missing cells declared*, which is different from *having no missing cells*. | `validation.checks.check_raster_nodata_declared` emits a WARNING. |
| A-RAS-4 | Resampling defaults: **bilinear for continuous** (elevation), **nearest for categorical** (fuel class). There is no default for an undeclared layer kind — the caller must declare. | `terrain.reproject`, `fuels.io`. |
| A-RAS-5 | Two rasters are "aligned" only if CRS, cell size, grid origin (modulo cell size), and shape all match. Near-alignment is reported as misalignment, with the offsets. The build pipeline **warps non-terrain rasters onto the DEM's grid** so the condition does not arise there (D-0018). | `validation.checks.check_grid_alignment`; `terrain.reproject_raster(target_grid=...)`. |

## Roads

| # | Assumption | Enforcement |
|---|---|---|
| A-RD-1 | The road network is an **undirected** graph for topology QA. One-way restrictions exist in reality and are out of scope here; a downstream routing repository must not assume this graph is directed-correct. | `roads.graph`; `INTERFACES.md`. |
| A-RD-2 | Two line endpoints are the **same node** iff they coincide within `snap_tolerance_m` (default **1.0 m**). This tolerance is a modelling choice with real consequences and is recorded in the QA report. | `roads.graph.build_road_graph`. |
| A-RD-3 | Lines that **cross without a shared endpoint are not connected** — no automatic intersection noding. Real grade-separated crossings (bridges, tunnels) make this the correct default; it will under-connect a network digitised without nodes at junctions. | `DECISIONS.md` D-0007; `FAILURE_MODES.md` F-RD-1. |
| A-RD-4 | An **exit node** is a graph node that is either (a) explicitly tagged as an exit in the input, or (b) within `boundary_tolerance_m` of the study-area boundary. A network clipped to a boundary therefore gains exit nodes *from the clip itself*. | `roads.qa.identify_exit_nodes`; `FAILURE_MODES.md` F-RD-2. |
| A-RD-5 | Edge length is the **planar 2-D length in the layer's projected CRS**. It is not slope-corrected, not geodesic, and not a travel distance. | `roads.graph`; unit `DistanceUnit.METRE`. |
| A-RD-6 | **No road attribute in this repository means "safe" or "passable".** Width, surface, and class are carried as opaque source attributes. | `roads.qa` returns no safety field; `SCOPE.md`. |

## Fuels / vegetation

| # | Assumption | Enforcement |
|---|---|---|
| A-FU-1 | This repository **does not invent Korean fuel datasets or crosswalks**. A fuel class scheme must either cite a source or be declared `synthetic`. | `fuels.classes.FuelClassScheme` requires `source` or `data_class=SYNTHETIC`. |
| A-FU-2 | Every fuel layer declares a `DataClass`: `observed`, `modeled`, `derived`, or `synthetic`. There is no default. | `fuels.io`; `provenance.models.DataClass`. |
| A-FU-3 | Fuel class codes are **nominal categories**, never ordinal or interval. Arithmetic on fuel codes (mean, interpolation, bilinear resampling) is an error. | `fuels.io` forces nearest-neighbour resampling and rejects continuous statistics. |
| A-FU-4 | Korean forest-type data that this repository has not actually obtained is recorded as `UNKNOWN` availability in `DATA_PROVENANCE.md`, not sketched from memory. | `DATA_PROVENANCE.md`. |

## Population

| # | Assumption | Enforcement |
|---|---|---|
| A-POP-1 | Population data is **aggregate only**, at village / settlement level or coarser. Person-level and household-level records are rejected at load time. | `population.io.load_population_layer` raises `PrivacyGuardError`. |
| A-POP-2 | **No medical, disability-diagnosis, or care-status information about identifiable persons** enters this repository, in any form. Aggregate age strata are permitted; an aggregate count of persons with a named medical condition attached to a single dwelling is not. | `population.io.PRIVACY_FORBIDDEN_FIELD_PATTERNS`. |
| A-POP-3 | A settlement centroid is a **geometric** representative point, not a population-weighted centre of mass, unless the source provides weights. Which one it is, is recorded. | `population.models.SettlementCentroidKind`. |
| A-POP-4 | Age strata are **half-open intervals `[lower, upper)`** in whole years, and strata within a layer must not overlap. `65+` is `[65, None)`. | `population.models.AgeStratum`. |
| A-POP-5 | A population count with value `0` means *zero people counted*. A population count that is **absent** is `None`, never `0`. Strata that do not sum to the stated total are reported, not reconciled. | `validation.checks.check_population_consistency`. |
| A-POP-6 | Population counts are **residential-register or census counts at the stated date**, not daytime or present-population. Which one, and at what date, is provenance, and is `UNKNOWN` if the source does not say. | `ProvenanceRecord`. |

## Facilities

| # | Assumption | Enforcement |
|---|---|---|
| A-FAC-1 | **Presence in a dataset is not fitness for purpose.** A facility record carries `operational_status` and `suitability_assessed`, both defaulting to `UNKNOWN` / `False`. | `facilities.models.Facility`. |
| A-FAC-2 | "Shelter", "refuge candidate", "responder base", and "fire station" are **source-declared roles**, not verified capabilities. A building tagged `amenity=shelter` in OSM may be a bus stop shelter. | `facilities.models.FacilityKind` docstrings; `FAILURE_MODES.md` F-FAC-1. |
| A-FAC-3 | Facility capacity is `None` unless sourced. It is never estimated from footprint area in this repository. | `facilities.models.Facility.capacity_persons`. |

## Time

| # | Assumption | Enforcement |
|---|---|---|
| A-T-1 | Every layer declares a `TemporalProvenance`: `static`, `annual`, `monthly`, `observation_time`, or `retrospective`. There is no default — **including in a study-area config**, where a local-file source must state it. | `ProvenanceRecord` requires it; `SourceSpec.require_declared_semantics` raises rather than letting the build pipeline default it (D-0017). |
| A-T-2 | All timestamps are **timezone-aware**. Korean local time is `Asia/Seoul` (UTC+09:00, no DST since 1988). Naive datetimes are rejected. | `provenance.models` validators. |
| A-T-3 | `source_date` is *when the data describes the world*; `acquisition_date` is *when this repository obtained it*. They are different fields and neither substitutes for the other. | `provenance.models.SourceRecord`. |
| A-T-4 | A `static` layer is one whose change over the study period is assumed negligible **for data-preparation purposes only**. Terrain is `static`; this is false after a landslide, and false for roads after construction. | `GLOSSARY.md`. |

## Reproducibility

| # | Assumption | Enforcement |
|---|---|---|
| A-REP-1 | Synthetic fixtures are **deterministic**: any randomness is seeded and the seed is recorded in provenance. | `fixtures.synthetic`. |
| A-REP-2 | A bundle build is a pure function of (config, input files). Rebuilding from the same inputs reproduces identical arrays. | `tests/test_bundle_roundtrip.py`. |
| A-REP-3 | Checksums are **SHA-256**, computed on the raw bytes of files and on `C`-contiguous array buffers plus their dtype and shape for in-memory layers. | `provenance.checksum`. |
