# DECISIONS

Append-only decision log. Each entry records the decision, the alternatives that
were rejected, and the consequence a future reader has to live with.
**Do not edit a past decision — supersede it with a new one.**

Format: `D-NNNN | date | status | title`

---

## D-0001 | 2026-09-19 | accepted | This repository is standalone and exports no schema

**Decision.** `wildfireguardian-data` has no dependency on any other
WildfireGuardian repository, and publishes no data contract for them.
`StudyAreaBundle` is explicitly internal.

**Rejected.** Defining a shared interchange schema now, so downstream routing
and forecasting repositories could code against it.

**Why.** A schema written before its second consumer exists encodes one
consumer's accidental needs as a standard. The downstream repositories do not
exist yet, so any schema published now would be a guess — and this repository's
core rule is not to guess.

**Consequence.** Downstream integration will require an explicit interface
negotiation later, recorded in `INTERFACES.md`. Until then, bundle layout may
change without a deprecation cycle.

---

## D-0002 | 2026-09-19 | accepted | CRS mismatch is an error, not a coercion

**Decision.** Any operation combining two layers calls
`crs.require_same_crs(...)`, which raises `CRSMismatchError`. An unknown
(`None`) CRS raises `UnknownCRSError`.

**Rejected.** (a) Auto-reprojecting the second layer to the first; (b) warning
and continuing.

**Why.** Auto-reprojection is the single most common silent-error source in
applied GIS work, and a warning in a pipeline log is not read. Korea's common
CRSs (EPSG:5179, 5186, 5174, 4326) differ by metres to hundreds of metres —
enough to move a village across a ridge line but not enough to look obviously
broken.

**Consequence.** Callers must reproject explicitly. Pipelines are more verbose.
That is the intended trade.

---

## D-0003 | 2026-09-19 | accepted | Core numerics do not depend on GDAL

**Decision.** `RasterLayer` and `VectorLayer` are plain dataclasses over NumPy
arrays and Shapely geometries. `rasterio`/`pyogrio` are imported **lazily**,
inside I/O functions only. Slope, aspect, statistics, clipping, and all graph QA
run without GDAL.

**Rejected.** Making `rasterio` a hard import of the package root.

**Why.** The scientific core must be testable in any environment, including one
where a GDAL wheel will not build. It also forces the numerics to be written
against explicit array + transform + CRS + nodata + units, which is exactly the
state whose mishandling this repository exists to prevent.

**Consequence.** GeoTIFF read/write and raster reprojection require the
`[geo]` extra and raise `OptionalDependencyError` with an install hint when
absent. A NumPy `.npz` + JSON sidecar fallback format exists so a bundle can
still be written without GDAL.

---

## D-0004 | 2026-09-19 | accepted | Horn (1981) 3×3 for slope and aspect

**Decision.** Slope and aspect use Horn's finite-difference estimator over the
3×3 neighbourhood — the GDAL `gdaldem` and ArcGIS default.

**Rejected.** (a) Zevenbergen–Thorne (1987); (b) simple 2-cell central
differences; (c) plane fitting by least squares over the 3×3 window.

**Why.** Horn is the convention downstream GIS readers will expect, and it is
exact for a planar surface — which makes the analytical synthetic-plane test in
`tests/test_terrain_derivatives.py` a real test of correctness rather than a
tolerance-tuning exercise. Zevenbergen–Thorne is also exact on a plane but is
noisier on real DEMs; the choice is conventional, not forced.

**Consequence.** Slope is smoothed relative to 2-cell differences, which matters
if a downstream repository compares this slope to a different estimator's. The
estimator name is recorded in the output layer's provenance transformation.

---

## D-0005 | 2026-09-19 | accepted | Derivative edges and nodata neighbours become nodata

**Decision.** A slope/aspect cell is `nodata` if the cell or any of its eight
neighbours is `nodata`, or if the cell is on the array edge.

**Rejected.** (a) Edge replication; (b) mirroring; (c) computing from the
available subset of neighbours.

**Why.** All three rejected options invent elevation data. Option (c) is the
most tempting and the most dangerous: it produces plausible slope values along
every nodata boundary, which is exactly where real DEM voids (water, radar
shadow in steep Korean valleys) live, and exactly where a fire-behaviour reader
would be most misled.

**Consequence.** The valid extent shrinks by one cell per derivative pass.
Callers who need slope over a given study area must clip the DEM with a buffer
of at least one cell — `terrain.clip` accepts `buffer_cells`.

---

## D-0006 | 2026-09-19 | accepted | Aspect is NaN on flat ground

**Decision.** Where slope is exactly zero, aspect is `NaN`.

**Rejected.** (a) `0` (GDAL's behaviour with `-zero_for_flat`); (b) `-1`
(GDAL/ArcGIS default sentinel); (c) `-9999`.

**Why.** `0` is a legal aspect meaning "faces north" and is indistinguishable
from a real value; `-1` and `-9999` are in-band sentinels that survive
arithmetic and eventually reach a mean. `NaN` propagates loudly.

**Consequence.** Consumers must use `np.nanmean` and friends, and any exported
GeoTIFF must declare `nodata=NaN`. Circular statistics on aspect require the
explicit `terrain.stats.circular_mean_deg`, which ignores `NaN` and reports the
count it ignored.

---

## D-0007 | 2026-09-19 | accepted | No automatic intersection noding of road lines

**Decision.** Road lines are connected only at coincident endpoints (within
`snap_tolerance_m`). Lines that cross mid-segment are **not** joined.

**Rejected.** Planarising the network by splitting every line at every
intersection.

**Why.** In real networks a mid-segment crossing is frequently a bridge or an
underpass, and auto-noding fabricates a junction that does not exist — which
would turn a single-egress community into a two-egress one and silently destroy
the very property this repository is built to measure. Under-connection is
detectable (it shows up as extra components and dead ends in the QA report);
over-connection is not.

**Consequence.** A network digitised without junction nodes will report as
fragmented. `roads.qa.RoadNetworkQA.crossing_without_node_count` reports
suspected missing junctions as a diagnostic so the analyst can decide, and
`roads.graph.build_road_graph(node_crossings=True)` is available as an explicit,
provenance-recorded opt-in.

---

## D-0008 | 2026-09-19 | accepted | "Single egress" is topological and named as a candidate

**Decision.** The QA report field is `single_egress_candidates`. A component
qualifies when it contains at least one settlement node and exactly one exit
node.

**Rejected.** `single_egress_communities`, or any boolean `is_trapped`.

**Why.** The graph knows about topology, not about whether the one egress is a
1-lane farm track that a fire engine cannot enter, nor whether a second egress
exists outside the clipped extent. Calling the result a "community" or
"trapped" would launder a data property into a claim about people.

**Consequence.** Downstream users must do their own adjudication. The QA report
carries `clip_boundary_warning = True` whenever exits were inferred from the
study-area boundary rather than from source tags, because a clip can both create
and destroy egress.

---

## D-0009 | 2026-09-19 | accepted | UNKNOWN is a required literal, not an optional field

**Decision.** Provenance fields that a source does not supply are set to the
string `"UNKNOWN"`. They are not omitted, not `None`, and not inferred.

**Rejected.** Optional fields defaulting to `None`.

**Why.** `None` in a serialised record is ambiguous between "the source does not
say" and "nobody filled this in". `"UNKNOWN"` is unambiguous, greppable, and
countable — `validation` reports the UNKNOWN count per layer so incompleteness
is a visible metric rather than a silence.

**Consequence.** Provenance records are verbose. `validation` emits a WARNING,
not an ERROR, for each `UNKNOWN`: incompleteness is honest, whereas a wrong
value is not.

---

## D-0010 | 2026-09-19 | accepted | Slope and length work require a projected metre CRS

**Decision.** `terrain.derivatives` and `roads.graph` length computation raise
`GeographicCRSError` when handed a geographic (degree-based) CRS.

**Rejected.** Applying a cos(latitude) scale factor to convert degrees to
metres.

**Why.** The scale-factor trick is accurate enough to pass a smoke test and
wrong enough to matter: at 36.9° N, a naive degree-to-metre slope is wrong by
roughly a factor of 1.25 between the x and y directions. On a 30 m Korean DEM
that is a systematic slope bias of tens of percent, in the exact direction that
would bias a fire-spread reader.

**Consequence.** The example pipeline reprojects the Copernicus DEM from
EPSG:4326 to the study area's projected CRS before computing any derivative, and
records the resampling method and the resulting cell size in provenance.

---

## D-0011 | 2026-09-19 | accepted | Population privacy is enforced at load time, by field name

**Decision.** `population.io` rejects a layer whose attributes match
person-level or medical patterns (resident registration number, personal name,
diagnosis, care grade, dwelling-level occupant identity), raising
`PrivacyGuardError`.

**Rejected.** Documenting the prohibition without enforcing it.

**Why.** The wider project studies mobility-limited residents, so the temptation
to carry a "who needs help" list into the data layer is structural, not
hypothetical. A load-time guard makes the prohibited path fail loudly for
whoever tries it, including a future agent.

**Consequence.** The guard is name-based and therefore defeatable by renaming a
column; it is a tripwire, not a security boundary. `docs/FAILURE_MODES.md`
F-POP-1 states this limitation explicitly. Legitimate aggregate strata (for
example a village-level count of residents aged 65+) are permitted, and a
k-anonymity floor (`min_aggregate_count`, default 5) is reported as a WARNING
rather than silently suppressed, because a genuinely 3-person Korean hamlet is a
real study object.

---

## D-0012 | 2026-09-19 | accepted | Real external data is fetched into `data/raw/` and never committed

**Decision.** Real source data (Copernicus DEM tiles, OSM extracts) is fetched
by an explicit, time-boxed fetch step into `data/raw/`, which is
git-ignored. Only the *derived, clipped, documented* study-area bundle and its
provenance are committed, and only when small enough to be reviewable.

**Rejected.** Committing raw tiles for reproducibility.

**Why.** Licence terms differ per source (Copernicus: free with attribution;
OSM: ODbL, share-alike on derived databases), and a 12 MB raw tile in git
history is unreviewable and unremovable. Provenance plus checksum plus a
re-fetch command is the reproducibility mechanism.

**Consequence.** `wg-data build-study-area` on a real-data config requires
network access. Bundles built from synthetic fixtures require none, and those
are what CI runs. Licence and attribution for every real source are recorded in
`DATA_PROVENANCE.md`.

---

## D-0013 | 2026-09-19 | accepted | A layer transformed by this repository is DERIVED

**Decision.** Any layer this repository transforms becomes
`DataClass.DERIVED`, even when its source was `OBSERVED`. A clipped, reprojected
Copernicus DEM is `derived`, with the Copernicus `SourceRecord` retained and the
full transformation chain recorded.

**Rejected.** (a) Keeping `OBSERVED` through transformations, since the
measurements did come from an instrument; (b) keeping `OBSERVED` through a pure
subset (clip) and switching to `DERIVED` only on resampling.

**Why.** A reprojected DEM's values have been resampled: they are no longer the
measured values at those locations, and calling them observed would overstate
them. Option (b) is more informative but puts the observed/derived boundary at a
place a reader has to reason about, and the same information is already
recoverable exactly: `transformations[]` shows whether `reproject_raster` (values
changed) or only `clip_raster` (values untouched) was applied.

**Consequence.** `data_class` alone does not tell a consumer whether the ultimate
origin was an observation — they must read `sources[]`, which is retained
precisely for that. Recorded in `docs/DATA_PROVENANCE.md`.

---

## D-0014 | 2026-09-19 | accepted | Validate the written bundle, not the in-memory one

**Decision.** `wg-data build-study-area` writes the bundle, then validates the
**directory**, then writes the report to `<bundle>/validation/report.json`.

**Rejected.** Validating the in-memory bundle and embedding the report as the
bundle is written.

**Why.** Checksums exist only after writing, so an in-memory validation reports
every layer as unchecksummed (`PRV-008`) and can never catch a write that went
wrong or a manifest that disagrees with its files. Validating the directory
exercises the read path as well, which is the only way the round trip is
actually tested on every build.

**Consequence.** The manifest always reserves the `validation_report` extra, and
`BND-016` skips that one entry — the report is written after validation by
definition, so its absence during the run says nothing. `validate_bundle` on an
in-memory bundle remains available for tests and library use.

---

## D-0015 | 2026-09-19 | accepted | Real data comes from global open sources, with gaps left empty

**Decision.** The committed real-data study area (`uljin_real_v1`) uses
**Copernicus DEM GLO-30** for terrain and **OpenStreetMap** for roads, and has
**no** fuels, population, or facilities layer.

**Rejected.** (a) Waiting for authoritative Korean sources (NGII, VWorld, Korea
Forest Service, KOSIS/SGIS) before shipping any real bundle; (b) filling the
missing components with plausible synthetic layers so the bundle looks complete.

**Why.** The authoritative Korean sources were not reachable from this
repository's environment — and specifically, they failed as outbound-proxy
tunnel closures, which is not evidence that the services are unavailable
(`docs/DATA_PROVENANCE.md` §Access attempts). Shipping nothing real would have
left the pipeline unexercised against real data, where the interesting problems
are: a degree-grid source, a DSM, uneven rural coverage, non-square cells. Option
(b) is the failure this whole repository exists to prevent: a bundle that looks
complete and is partly invented.

**Consequence.** `uljin_real_v1` is terrain and roads only, and its road
completeness is UNKNOWN. A reader must not treat the absence of a fuels layer as
"no fuel here". Replacing OSM with NGII/VWorld data, and adding real vegetation
and aggregate population, are the first items in `tasks/CURRENT.md`.

---

## D-0016 | 2026-09-19 | accepted | A DEM's vertical unit is declared, never defaulted

**Decision.** `terrain.read_geotiff` takes `value_unit` as a **required**
argument, and when the file declares a band unit that disagrees with it, raises.
A study-area config using a local raster must declare `value_unit`.

**Rejected.** Defaulting to metres (the previous behaviour), which is right for
almost every GeoTIFF DEM.

**Why.** Found by audit. A DEM in feet read as metres yields a slope wrong by a
factor of 3.28 in `tan(theta)`, biased **steep**, on a layer whose provenance
asserts metres — and A-TER-8's enforcement in `terrain.derivatives` can never
fire, because the mislabelling happened at ingest. Worse, the previous code
*read* the file's unit tag and used it only in a free-text note, so the same
provenance record could state `value_unit = "m"` while quoting the file saying
`ft`. That is precisely the falsification condition in
`docs/RESEARCH_QUESTION.md`.

**Consequence.** Every `read_geotiff` call site must state the unit. That is the
intended cost: the caller knows something the file does not necessarily say, and
now has to say it.

---

## D-0017 | 2026-09-19 | accepted | A local-file source declares its temporal class in the config

**Decision.** `SourceSpec.require_declared_semantics` raises `ConfigError`
unless a `geotiff` or `geojson` source declares `temporal_class` (and
`value_unit` for a raster). The build pipeline no longer supplies a per-component
default.

**Rejected.** Per-component defaults (terrain → `static`, roads →
`observation_time`, and so on), which is what the pipeline previously did.

**Why.** Found by audit. A-T-1 says there is no default, and the *loaders*
honoured that — but the build pipeline, which is the only path a CLI user has,
filled it in. A retrospective road layer compiled from post-fire imagery was
silently labelled `observation_time`, which `docs/INTERFACES.md` instructs
consumers to act on. For the wider project's forecast-versus-trigger question,
that is textbook data leakage.

**Consequence.** Configs are more verbose, and every existing config using a
local file needs the key added. Fixtures and the network fetchers are unaffected,
because they know their own source's temporal character.

---

## D-0018 | 2026-09-19 | accepted | Sibling rasters are warped onto the DEM's grid

**Decision.** `terrain.reproject_raster` accepts `target_grid`/`target_shape`,
and the build pipeline uses it to warp a non-terrain raster directly onto the
DEM's grid rather than reprojecting it independently and clipping afterwards.

**Rejected.** Reprojecting each raster independently and relying on the
`RAS-007` warning to report the resulting misalignment.

**Why.** Found by audit. Independent reprojection derives each layer's target
grid from that layer's own extent, so a DEM and a fuel raster arriving in
different Korean belts ended up with origins offset by ~11 m and ~13 m on a 30 m
grid — a third of a cell. Every fuel value was displaced relative to the DEM
cell a consumer would index it by. Validation did report it, but at WARNING, so
a default `validate-study-area` still exited 0; and a pipeline should not be
*producing* the condition it then warns about.

**Consequence.** `target_grid` and `dst_resolution` are mutually exclusive.
Warping onto a fixed grid can leave nodata at the margins where the source does
not cover the DEM's extent, which is correct: that is missing data, not a
misalignment to be smoothed away.

---

## D-0019 | 2026-09-19 | accepted | Bundle schema 1.1.0: everything written is checksummed, licences reach the manifest

**Decision.** The manifest gains `extras_checksums_sha256`,
`provenance_checksums_sha256`, an `unchecksummed` block, a `licences` list
aggregated from the layer provenance, and `crs_wkt`. Schema version 1.1.0, and
comparison stays exact.

**Rejected.** (a) Leaving only layer files checksummed; (b) accepting a 1.0.0
bundle silently, since the change is additive.

**Why.** Found by audit and verification. Only the layer rasters and vectors
were checksummed, so editing `roads_qa.json` — the artifact a downstream reader
is *most* likely to consume without re-deriving it — went undetected, as did
editing a provenance sidecar. Separately, the ODbL share-alike obligation on the
committed real bundle was recorded per layer but absent from `manifest.json`,
which `docs/INTERFACES.md` names as a consumer's step 1. And `crs_to_string`
emits a `wkt:`-prefixed string for a CRS with no authority code, which
`parse_crs` could not read back — so a custom projection could not survive a
round trip.

**Consequence.** A 1.0.0 bundle is now refused on read; both committed bundles
were rebuilt. The validation report itself cannot be checksummed, because it is
written after validation (D-0014); that is stated in the manifest's
`unchecksummed` block rather than left implicit.
