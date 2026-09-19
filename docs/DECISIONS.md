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
