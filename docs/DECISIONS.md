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

---

## D-0020 | 2026-09-20 | accepted | Lines are connected where they share a vertex, not only at endpoints

**Decision.** `roads.build_road_graph(node_shared_vertices=True)` — now the
default — splits lines at vertices that two different lines genuinely share, so
a side road meeting the middle of a through road becomes a junction.

**Rejected.** (a) Endpoint-only connection, the Phase 1 behaviour; (b) full
intersection noding, which D-0007 rejects and still rejects.

**Why.** Found by the Phase 2 road audit
(`reports/ULJIN_ROAD_AUDIT.md`). OpenStreetMap — and most road GIS — encodes a
junction as a **shared node**, which is usually an **interior** vertex of a way.
In the Uljin extract, 35 of 41 shared OSM node IDs involved an interior vertex,
so endpoint-only connection recovered 6 of 41 real junctions and turned the rest
into fragment boundaries: 22 components, 17 of them single edges, largest
holding 12.9 % of edges. With shared-vertex noding the same 31 source ways form
2 components, 94.1 % of edges in the largest, and **total length is unchanged at
28.784 km** — no geometry added, removed or moved.

**Why this does not reopen D-0007.** The two rules test different things, and
OSM distinguishes them precisely. A *shared vertex* is the source placing one
coordinate in two lines' coordinate lists — an explicit assertion that they
meet. A *geometric crossing with no shared vertex* is how a bridge or tunnel is
represented. So this recovers stated junctions while still refusing to fabricate
unstated ones, and the un-noded-crossing diagnostic still reports the latter.

**Consequence.** Edge counts rise (31 → 68 for Uljin) because a way carrying
several junctions becomes several edges; that is what makes degree, bridge and
articulation-point analysis meaningful. Comparison is by rounded coordinate
(9 decimal places) — float-noise tolerance, not a snapping distance. Setting
`node_shared_vertices=False` reproduces Phase 1 behaviour for comparison.

---

## D-0021 | 2026-09-20 | accepted | An exit is a departure node, and a boundary crossing is an exit

**Decision.** `roads.identify_exit_nodes` records three separately-labelled
kinds of exit — `from_source_tags`, `from_boundary_crossing` (an edge's geometry
crosses the study-area boundary ring), and `from_boundary` (node proximity) —
and in each case marks the **departure** node: the endpoint(s) lying inside the
study area.

**Rejected.** (a) Node proximity alone, the Phase 1 behaviour; (b) marking both
endpoints of a leaving edge.

**Why.** Two errors in opposite directions, both found by the road audit. With
`clip_mode="intersects"` whole features are kept, so a road leaving the area has
no *node* near the boundary: proximity alone found **1** exit on the Uljin
bundle where **4** roads actually cross the boundary — a large under-count of
egress, the quantity this project most cares about. Conversely, marking both
endpoints of a tagged edge over-counts, and a single tagged through-road would
contribute two exits and stop a genuinely single-egress component being reported
as one.

**Consequence.** `ExitNodeSet` gained fields, so the road-QA JSON gained keys
(report `schema_version` 1.0.0 → 1.1.0). `crosses` rather than `intersects` is
the crossing predicate: an edge merely *touching* the ring at its own endpoint
has not left the area, and that endpoint is already covered by proximity.

---

## D-0022 | 2026-09-20 | accepted | Road attributes are carried when present and absent when not

**Decision.** `roads` carries `highway`, `oneway`, `lanes`, `surface`, `bridge`,
`tunnel`, `layer`, `maxspeed`, `access`, `width` and every other source tag as
**opaque properties**, and `roads.qa` reports per-attribute availability. No
attribute is defaulted by road class.

**Rejected.** Filling missing attributes with per-class defaults (for example
`lanes=2` for `unclassified`, `surface=paved` for `residential`), which is what
a routing consumer will eventually want.

**Why.** Those defaults are a *modelling* choice, and their right values depend
on the analysis. Writing them into the data layer would make a fabricated value
indistinguishable from a sourced one in exactly the field a travel-time model
multiplies by. The Uljin extract makes the stakes concrete: `oneway`, `lanes`,
`surface`, `maxspeed`, `access` and `width` are absent from **every** kept way,
so a defaulting pipeline would have produced a fully-attributed road layer of
which none of the attributes came from the source.

**Consequence.** A downstream routing model must supply its own defaults, and
should do so in a separate, declared parameter layer so its assumptions stay
visible. `roads.qa.attribute_availability` gives it the per-attribute counts to
decide with.

## D-0023 | 2026-09-20 | accepted | `DataClass` carries governance's seven classes, and `RETROSPECTIVE` stays on two axes

**Decision.** `DataClass` is extended from this repository's original four
values to all seven classes in `wildfireguardian-research-governance`
`governance/DATA_CLASSES.md` (`OBSERVED`, `MODELED`, `DERIVED`, `SYNTHETIC`,
`ASSUMED`, `RETROSPECTIVE`, `ORACLE_ONLY`), governance's class algebra is
implemented in `combine_data_classes`, and `governance_class_name` emits the
uppercase spelling their `OC-029` requires at an integration boundary. This
repository still only *produces* `OBSERVED`, `DERIVED`, `MODELED` and
`SYNTHETIC`; the other three exist so an inbound value can be labelled and
refused.

**The conflict this resolves.** Governance treats `RETROSPECTIVE` as a *data
class* — one slot in the same enum as `OBSERVED`. This repository already
treats retrospectiveness as a *temporal* property, `TemporalProvenance.RETROSPECTIVE`,
orthogonal to whether a value was measured or modelled. Both readings are
defensible and they disagree: a burn-scar polygon digitised after a fire is,
in our terms, `data_class=OBSERVED` **and**
`temporal_class=RETROSPECTIVE` — two facts governance can only express as one.

**Resolution.** Keep both axes internally; collapse them only at the export
boundary, with `RETROSPECTIVE` dominant. A layer whose `temporal_class` is
`RETROSPECTIVE` exports as governance class `RETROSPECTIVE` regardless of how
its values were obtained, because that is the direction that cannot cause
leakage: over-reporting retrospectiveness makes a planner refuse data it could
legally have used, while under-reporting it hands a planner a fact from the
future.

**Rejected.** (a) Dropping `TemporalProvenance.RETROSPECTIVE` to match
governance's single axis — it would lose the ability to say *both* that a layer
is an observation and that it was compiled after the fact, which is precisely
the distinction `docs/ASSUMPTIONS.md` A-T-1 exists to keep. (b) Ignoring
governance's class and exporting `OBSERVED` — that is the leakage direction.

**Consequence.** `DataClass.planner_legal` reports `RETROSPECTIVE` and
`ORACLE_ONLY` as never planner-legal, and reports `SYNTHETIC` as not
planner-legal *from here*, since governance permits synthetic input only through
a declared observation operator and this repository provides none. The
export-boundary collapse is a lossy projection: a consumer that needs both axes
must read the bundle's own provenance, not only its governance class.

## D-0024 | 2026-09-20 | accepted | ESA WorldCover is carried as land cover, and this repository does not convert it to a fuel model

**Decision.** `fuels.source.kind: esa_worldcover` fetches ESA WorldCover 10 m
2021 v200 and carries its published 11-class legend as a
`SchemeKind.SOURCE_CLASS` scheme (`esa_worldcover_v200`). It is warped onto the
DEM's grid with **nearest-neighbour** resampling (D-0018, A-FU-3) and stored as
a categorical raster. No crosswalk from those classes to a fire-behaviour fuel
model (Anderson 13, Scott & Burgan 40, or a Korean equivalent) is shipped,
computed, or implied.

**Rejected.** Shipping a land-cover-to-fuel-model lookup table, which is what a
fire-spread consumer actually needs and what would make the layer immediately
useful.

**Why.** Such a table is a *modelling* artifact, not a property of the data: the
same WorldCover class 10 (`tree_cover`) is a different fuel in a Korean pine
plantation than in a riparian broadleaf stand, and the mapping depends on
species composition, stand age, and season — none of which WorldCover encodes.
Publishing a crosswalk here would make a modelled parameter indistinguishable
from an observed class, in the field a rate-of-spread model reads directly. That
is the `SOURCE_CLASS` versus `MODELED_CROSSWALK` distinction, and it is why
`VegetationClassScheme` raises if a `MODELED_CROSSWALK` scheme is declared
`OBSERVED`.

**Naming.** The layer is vegetation/land cover. `FuelClass` and `FuelClassScheme`
remain as aliases of `VegetationClass` and `VegetationClassScheme` for callers,
but the primary names say what the data is.

**Consequence.** A downstream fire-behaviour model must supply its own crosswalk
and declare it `MODELED_CROSSWALK` with its own provenance. That work belongs in
a fuels repository, not here (`docs/SCOPE.md`). Two WorldCover limitations carry
straight into Korean study areas and are recorded on the scheme: mountain
shadows are sometimes classified as water, and the product's 2021 vintage
predates the 2022 Uljin fire, so it is pre-fire land cover for that event and
must not be read as post-fire state.

## D-0025 | 2026-09-20 | accepted | Provenance schema 1.1.0: temporal validity, surface model, and explicit migrations

**Decision.** `ProvenanceRecord` gains three fields and
`PROVENANCE_SCHEMA_VERSION` becomes `1.1.0`:

- **`valid_from` / `valid_to`** — the interval over which the values are claimed
  to describe the world. Validated by `validate_temporal_string`, so they keep
  their precision (`"2021"` stays a year), accept `UNKNOWN` and
  `not_applicable`, and an inverted interval raises.
- **`surface_model`** — a closed set: `"dsm"`, `"dtm"`, `not_applicable`,
  `UNKNOWN`. An unrecognised spelling raises.

**Why `valid_from`/`valid_to` are not `temporal_reference`.** They answer
different questions. `temporal_reference` says *when the data is from*;
the validity interval says *when it is true of*. A 2021 land-cover product has
`temporal_reference="2021"`, but whether it is still valid in 2023 is a separate
claim that the product does not make. Collapsing them would mean a consumer
asking "is this pre-fire?" had to answer it from an acquisition date, which is
the reasoning that puts post-event data into a pre-event analysis.

**Why `surface_model` is structured.** It was already in the Copernicus notes
prose. A consumer cannot be expected to grep a free-text field for a fact that
decides whether a forested slope is terrain or canopy — and a 20 m canopy step
across one 30 m cell is a ~34° slope that no terrain has (F-TER-3). It is now a
field, and `PRV-009` reports it as INFO on every DSM-derived layer.

**What is deliberately *not* filled in.**

- The Copernicus DEM's validity is `UNKNOWN`, **not** its 2011–2015 acquisition
  window. How long a DSM stays valid is a real open question: the bare-earth
  component is effectively static while the canopy component is not, and this
  product does not separate them. Writing the acquisition window into a
  *validity* interval would assert an expiry date we invented.
- The 1.0.0 → 1.1.0 migration sets all three fields to `UNKNOWN`. It does **not**
  infer `surface_model="dsm"` from a Copernicus source name, even though that
  happens to be true, because afterwards an inference would be indistinguishable
  from a fact somebody verified (`AGENTS.md` §3).
- Synthetic fixtures get `surface_model="dtm"` and `not_applicable` validity.
  `"dtm"` is accurate, not a placeholder — a synthetic surface has no canopy —
  and it is what lets the analytic tests assert that a computed slope is terrain
  slope. `not_applicable` rather than `UNKNOWN` because a synthetic construct
  describes no moment in the world, and only `UNKNOWN` counts as a gap (D-0009).

**Migrations are explicit** (Phase 2 item 26). `_PROVENANCE_MIGRATIONS` maps
each version to its successor and a function; `migrate_provenance_payload`
applies the chain one step at a time, so a two-version-old record passes through
every intermediate migration rather than a hand-written shortcut. A version with
no registered migration is **refused** — including one *newer* than this reader,
because a newer writer may have changed the meaning of a field this reader
thinks it understands. Every migration appends a note saying what it filled and
that it was not inferred.

**Consequence.** The committed `uljin_real_v1` bundle was written at 1.0.0 and
still reads, through the migration, with its three new fields `UNKNOWN` — which
is the honest state for a bundle nobody established those facts for. `PRV-010`
now fires on it as a WARNING. That is the check doing its job, not a regression.

**Rejected.** Bumping the version without a migration and regenerating the
committed bundles. Rebuilding `uljin_real_v1` needs network access and the AWS
tile list is mutable (D-0012), so "just rebuild it" is not available — and a
schema that cannot read its own published output is not a schema, it is a
breaking change with a version number on it.

## D-0026 | 2026-09-20 | accepted | Reprojection records the co-registration contract

**Decision.** `reproject_raster` records `raster_kind`,
`categorical_or_continuous`, `source_grid` and `target_grid` (as
`GridTransform` dicts, not bare affine tuples) in its `Transformation`
parameters, alongside the CRSs, resolutions and resampling method it already
recorded.

**Why.** The guard that refuses an averaging resampler on a `CATEGORICAL` layer
already existed and raises (A-FU-3). But a refusal leaves no trace, so there was
no way to establish *after the fact* — from a bundle alone, without re-running
anything — that class codes were never averaged, or that a layer claiming to
share the DEM's grid actually landed on it. Two of the things Phase 2 item 16
asks for were enforced but not auditable.

**Consequence.** `target_grid` is asserted in tests to equal the output layer's
own transform, so the record cannot drift from the raster it describes.

## D-0027 | 2026-09-20 | accepted | Two manifests: the writer's record, and the downstream contract

**Decision.** A bundle carries two manifests.

- `manifest.json` (`BUNDLE_SCHEMA_VERSION`, currently 1.1.0) is the **writer's
  record**: file paths, formats, dtypes, cell counts, per-file checksums. A
  reader needs it to load the bundle back.
- `bundle_manifest.json` (`BUNDLE_CONTRACT_SCHEMA_VERSION`, 1.0.0), built by
  `integration/manifest.py`, is the **contract**: the only thing another
  WildfireGuardian repository is permitted to code against. It carries
  `bundle_id`, `bundle_schema_version`, `created_at`, `study_area`, `crs`,
  `canonical_grid`, a fixed five-slot `layers` object, `provenance`,
  `validation` and `checksums`.

**Rejected.** One manifest doing both jobs, which is fewer files and less code.

**Why two.**

1. **Absence must be representable.** The contract's `layers` object always has
   all five slots — `terrain`, `roads`, `fuels`, `population`, `facilities` —
   each with a `status` and, when `ABSENT`, a reason. The writer's record lists
   only the layers that exist, so "no population layer" is a *missing key*,
   which a consumer reads as easily as an oversight as a finding. An absent
   layer with no documented reason reports its reason as `UNKNOWN`, which is
   itself the finding: a gap nobody wrote down.
2. **The contract must be stable when storage is not.** Phase 2 item 34 freezes
   this schema. If the contract were the writer's record, changing how rasters
   are stored would break a downstream dependency for no scientific reason.
3. **Three versions, three reasons to change.** `BUNDLE_SCHEMA_VERSION`,
   `PROVENANCE_SCHEMA_VERSION` and `BUNDLE_CONTRACT_SCHEMA_VERSION` move
   independently, and a consumer cares about exactly one of them.

**Drift is impossible by construction, and checked anyway.** The contract is
*derived*, never hand-maintained — but derivation only helps if somebody
re-derives it, so `validate-study-area` does: `BND-023` re-derives the contract
and reports any difference as an ERROR. `created_at` and the `validation` block
are excluded from that comparison, the first because it cannot be re-derived and
the second because validation runs after the write (D-0014).

**Derived from the bundle *read back from disk*, not the in-memory one** — for
exactly the D-0014 reason. Checksums exist only once the files do, so the
in-memory bundle's provenance still says `UNKNOWN`; deriving the contract from
it puts an unchecksummed contract next to a checksummed bundle. This is not
hypothetical: `BND-023` caught it in both the library and the CLI path the first
time the check ran. The extra read also proves the write round-trips.

**Road topology comes from the persisted QA report, not the live `RoadGraph`.**
`read_bundle` does not reconstruct the graph object, so a contract sourced from
it would say different things about the same bundle depending on whether it had
just been built or read from disk. A contract that disagrees with itself is not
a contract.

**Consequence.** `data_class` is **uppercase** in the contract and lowercase in
this repository's own artifacts, as research governance requires (their
`OC-029`), and the `RETROSPECTIVE` axis collapse described in D-0023 happens at
this boundary and nowhere else.

## D-0028 | 2026-09-20 | accepted | Readiness is completeness; this repository never claims validity

**Decision.** `wg-data compatibility` reports, per consumer profile
(`FORECAST_VALUE`, `OSSE`, `ASSISTED_DISPATCH`), one of `READY`,
`READY_WITH_LIMITATIONS`, `INCOMPLETE`, or
`OUT_OF_SCOPE_FOR_THIS_REPOSITORY`.

**`READY` means exactly one thing:** every input that consumer named is present
and carries provenance sufficient to interpret it. It does **not** mean the data
is accurate, the resolution adequate, the sources authoritative, or that any
result computed from it would be correct. Phase 2 item 28 requires reporting
readiness "without claiming scientific validity", and this is how.

**The uncomfortable consequence, accepted deliberately.** A bundle of entirely
synthetic fixtures is `READY` for all three profiles. That is correct — it is
what a fixture is *for*, and it is what lets downstream CI run without network
access. So the report puts `data_classes_present`,
`all_layers_planner_legal` and `contains_synthetic` next to the verdict, and a
test asserts that an all-synthetic bundle reporting `READY` also reports
`all_layers_planner_legal: false`. A reader who takes `READY` as a quality
claim has to ignore three adjacent fields to do it.

**`not_supplied_here` is part of the contract, not a backlog.** Each profile
lists, in machine-readable form, what this repository will not provide:
forecasts and fuel-model crosswalks for `FORECAST_VALUE`; the nature model's
dynamic state and observation operators for `OSSE`; all mission logic, routing
and travel-time estimation for `ASSISTED_DISPATCH`. This turns `docs/SCOPE.md`
from a paragraph somebody has to find into a field somebody's code can read.

**Grid mismatch is scoped to the consumers it affects.** A
`CANONICAL_GRID_MISMATCHED` bundle is a material limitation for
`FORECAST_VALUE` and `OSSE`, which index two or more rasters by the same
(row, col), and not for `ASSISTED_DISPATCH`, which does not. Conversely
`REPROJECTED_ONTO_ITS_OWN_GRID` is recorded per layer but is deliberately *not*
material: it is expected of the layer that *defines* the canonical grid, so
treating it as a problem would flag every bundle for doing the right thing.

**Exit code 0 whatever the readiness.** `INCOMPLETE` is a true and useful answer
about a bundle, not an error in producing one. A CI job that wants to gate on
readiness reads `--json`.

## D-0029 | 2026-09-20 | accepted | The downstream CI fixture is analytic, not realistic

**Decision.** `data/study_areas/wg_integration_fixture_synthetic_v1` is
committed as a dependency for other repositories' CI (Phase 2 item 29). It is
83 KB: a 12×12 study area at 30 m with all five layers, built by
`configs/integration_fixture_synthetic.yaml` from five new fixtures.

**Every expected result is closed-form or hand-derived, never a snapshot.**

- The DEM is a plane rising due east at 10%. Horn's estimator is exact on a
  plane, so slope is `atan(0.10)` = 5.710593° and aspect is 270° at **all 144**
  study-area cells, with zero missing — verified, not asserted.
- Fuels is co-registered cell-for-cell, split west/east into two classes, with
  a 2×2 nodata block.
- Roads is a T: 1 component, 4 nodes, 3 edges, 480 m, 2 exits.

**Why this matters more than realism.** A downstream repository pins its CI to
this bundle. If the expected values were regression snapshots of whatever this
repository last produced, a bug here would propagate there with a green test
suite in between — the exact failure mode `AGENTS.md` §7 rejects ("never write a
test that merely asserts the current output"). A fixture whose right answer is
independently known is worth more than one that looks like Korea.

**The road fixture exists to expose one specific trap.**
`single_egress_candidates` is **empty** — the component has two exits — while
`critical_links` has **one** entry: removing the T's stem cuts the settlement
off from both. A consumer reading only the first field concludes the settlement
is comfortably served, and is wrong. That is D-0008 and F-RD-6 made concrete and
assertable.

Worth recording: the count of critical links was hand-derived as **zero** and
that was **wrong**. The settlement reaches both exits only through the junction,
so the stem is a single point of failure. The metric was right and the reasoning
about it was not — which is a better argument for the fixture than any
successful derivation would have been.

**The DEM is 16×16 for a 12×12 study area**, buffered two cells on every side
exactly as a real fetch is before clipping. Without that margin the estimator's
edge loss falls *inside* the study area: on a 12×12 grid that is 44 of 144
cells, a permanent 30.6% missing-data WARNING that is an artifact of the
fixture's size and nothing else.

**One expected WARNING remains, and is asserted rather than removed.** `RAS-003`
fires on `slope_deg` and `aspect_deg` at 26.5%, which is the retained 1-cell
buffer ring — a large fraction of a small layer, and ~2% on the 204×204 valley
fixture. Resizing the fixture until a true finding stopped firing would be
tuning data to satisfy a check. Instead the config documents it, the test
asserts it fires, and the guidance is explicit: **downstream CI should gate on
ERRORs, of which this bundle has none — not on WARNINGs, which every honest
bundle has.**

**Rejected.** Reusing `uljin_valley_synthetic_v1`. It is 204×204, and it
deliberately carries positive findings — strata that do not sum, a count below
the k-anonymity floor, an orphan track, an unnoded crossing. Those are right for
*this* repository's tests and wrong for a *downstream* fixture, where every
emitted finding is something a consumer must learn to ignore. So the two
fixtures have opposite designs on purpose: that one exercises the unhappy paths,
this one is clean apart from the documented ring.

**It is `READY` for all three consumer profiles and no layer in it is
planner-legal.** Both facts are asserted together, because the first without the
second would read as a quality claim (D-0028).
