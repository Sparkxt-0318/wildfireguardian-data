# FAILURE_MODES

Ways this repository's outputs can still be wrong, or be right and misread.

Three categories, and the distinction matters:

- **CAUGHT** — the code detects it and raises or reports a finding. Listed so a
  reader knows the protection exists and what its limits are.
- **NOT CAUGHT** — the code cannot detect it. These are the dangerous ones.
- **BY DESIGN** — behaviour that looks like a bug and is not. Do not "fix" these
  without a `DECISIONS.md` entry.

Each entry has a stable ID, referenced from code and docstrings.

---

## CRS

### F-CRS-1 — Silent reprojection (CAUGHT)
Combining layers in different CRSs raises `CRSMismatchError`; nothing reprojects
implicitly (D-0002). **Limit:** the check is on the *declared* CRS. A layer
labelled with the wrong CRS passes every check — see F-CRS-2.

### F-CRS-2 — A layer labelled with the wrong CRS (NOT CAUGHT)
Korean data is frequently mislabelled between `EPSG:5174` (Korean 1985) and
`EPSG:5186` (KGD2002): the datum shift is of order 100–200 m, enough to move a
village to the wrong side of a ridge, and small enough that nothing looks
broken. This repository treats them as distinct CRSs and never equates them, but
it cannot tell that a *file's* label is a lie.
**Mitigation:** check a few known points against an independent reference before
trusting a new Korean source. A 100–200 m systematic offset is the signature.

### F-CRS-3 — Transposed coordinates from the axis-order trap (CAUGHT, and worth understanding)
`EPSG:5179`/`5186`/`5187` declare authority axis order **(Northing, Easting)**
and abbreviate their axes `X` and `Y`, where **`X` is the northing** (the
East-Asian survey convention). Reading `X` as "the x coordinate" transposes
every Korean coordinate pair while leaving plausible-looking numbers behind.
This package pins tuple order to (easting, northing) everywhere, uses
`always_xy=True` transformers, decides axis order from axis *direction* rather
than abbreviation, and reports the authority order as validation finding
`CRS-004` so a consumer is warned.
**Limit:** a consumer that ignores `CRS-004` and reads in authority order will
still transpose. `Bounds` rejects an inverted box, which catches the common case.

### F-CRS-4 — A geographic CRS reaching length or slope code (CAUGHT)
`GeographicCRSError`. At 36.9° N a naive degree-to-metre slope is wrong by about
a factor of 1.25 between the x and y directions — a systematic slope bias of
tens of percent, in the direction that would mislead a fire-spread reader
(D-0010).

---

## Terrain

### F-TER-1 — Slope computed at the study-area edge (CAUGHT / BY DESIGN)
A 3×3 estimator has no answer at the array edge, so those cells are `nodata`
(D-0005). If a study area is clipped with no buffer, its boundary has no slope.
`clip_buffer_cells >= 1` is enforced in config when derivatives are requested,
and `BND-003` fires if slope somehow has no more missing cells than its DEM.

### F-TER-2 — Slope invented next to a data void (BY DESIGN, prevented)
Computing a derivative from the *available* subset of a 3×3 window is the most
tempting shortcut here and the most dangerous: it produces plausible slope values
along every nodata boundary, which is exactly where real DEM voids live (water,
radar shadow in steep Korean valleys). Any cell whose neighbourhood touches
missing data is `nodata`.

### F-TER-3 — Canopy slope mistaken for ground slope (NOT CAUGHT)
Copernicus GLO-30 is a **digital surface model**. Over Korean forest, the
surface is the canopy. At a stand edge a 20 m canopy-height step across one 30 m
cell produces a spurious slope of ~34°, and across a clear-cut boundary the
aspect will point off the canopy edge rather than down the hill. Nothing in the
data distinguishes this from real terrain.
**Mitigation:** the DSM status is recorded in provenance and in the source notes.
A study needing ground slope needs a DTM — for Korea, NGII's product rather than
a global DSM.

### F-TER-4 — Arithmetic mean of aspect (CAUGHT, by omission)
The arithmetic mean of 350° and 10° is 180° — due south, the exact opposite of
the correct due north. No arithmetic mean of aspect is offered anywhere;
`terrain.stats.circular_mean_deg` is the only path, and it reports
`resultant_length` so a meaningless mean (opposing flanks cancelling) is visible
as such. Values below about 0.1 mean "no dominant aspect".

### F-TER-5 — Grid north versus true north (NOT CORRECTED, but quantified)
Aspect is measured from the projected CRS's `+y` axis, not from true north. The
difference is the meridian convergence, which is zero on the projection's
central meridian and grows away from it. Comparing this aspect to a true-north
wind direction without correcting is a real error.

**Measured, so it is no longer an unquantified hazard.**
`crs.meridian_convergence_deg(crs, x, y)` returns the correction, verified
against the closed form `atan(tan(Δλ)·sin(φ))`:

| CRS | where | convergence |
|---|---|---|
| EPSG:5187 | shipped Uljin study areas | **+0.19° to +0.21°** |
| EPSG:5179 | across South Korea | **about ±1.1°** |

So for a 4 km study area on the right belt it is a fifth of a degree — below the
noise of any aspect estimator — and on the nationwide CRS it is over a degree,
which is not. Apply it as
`true_north_azimuth = grid_azimuth + meridian_convergence_deg(...)`.

**Why it is still not corrected in the layer:** the correction varies across the
raster, and applying it would make the stored aspect neither grid-referenced nor
consistently true-referenced. The layer states its reference frame; the consumer
converts if it needs to.

### F-TER-6 — Slope from a resampled DEM is not the slope of the source (NOT CAUGHT)
Bilinear reprojection smooths, so slope from a reprojected DEM is
systematically *gentler* than slope computed in the source grid, most visibly on
ridges. This repository reprojects before deriving (because a degree grid cannot
give a valid slope at all), and records the resampling method and both cell
sizes. The magnitude of the smoothing is not quantified.

### F-TER-8 — Sibling rasters on different grids (PREVENTED, and caught if it happens)
Reprojecting two rasters independently derives each target grid from that
layer's own extent, so a DEM and a fuel raster arriving in different Korean
belts land on origins offset by a fraction of a cell — every fuel value
displaced relative to the DEM cell a consumer indexes it by. The build pipeline
now warps non-terrain rasters **onto the DEM's grid** (D-0018), so it does not
arise there; `RAS-006`/`RAS-007` still catch it for a bundle assembled by hand,
reporting the offset magnitude.

### F-TER-7 — Non-square cells after reprojection (CAUGHT as a warning)
`RAS-005`. A reprojection with no explicit target resolution generally produces
non-square cells, which makes slope anisotropic with respect to cell count.
Always pass `target_resolution_m` for a study area.

---

## Missing data

### F-MD-1 — Missing data becoming zero (BY DESIGN, prevented)
The single most damaging silent error available here: a missing elevation
becoming `0` puts sea level in the middle of a mountain; a missing fuel class
becoming `0` invents a land-cover type; an unburned rasterisation cell becoming
`0` invents coverage. Prevented in every path: statistics use the valid mask,
reprojection carries missing cells as `NaN` (so a sentinel cannot be averaged
into a real value) and refuses an integer layer with no declared nodata,
rasterisation fills with the scheme's nodata code, and GeoTIFF/npz round trips
preserve the nodata declaration. `np.nan_to_num` appears nowhere.

### F-MD-2 — A sentinel contaminating a statistic (CAUGHT)
`-9999` left in an array drags a 400 m mean elevation to something absurd —
and a *less* extreme sentinel produces a plausible wrong answer instead.
Statistics are computed over valid cells only and report how many were ignored.
The test that makes this real asserts that the same void marked as `NaN` and as
`-9999` summarises **identically**.

### F-MD-3 — An undeclared nodata value (CAUGHT as a finding)
`RAS-001`. A raster with `nodata = None` states nothing about whether cells are
missing (A-RAS-3). WARNING for float, where `NaN` is still detected; **ERROR**
for integer, where there is no such fallback and missing cells would be
indistinguishable from real values.

### F-MD-4 — A void that is real data (NOT CAUGHT)
A cell legitimately equal to a sentinel-like value (an elevation of exactly
`-9999` does not occur, but a population of `0` or a fuel class of `0` can be
real) will be masked if that value is declared as nodata. This is why
`FuelClassScheme` refuses a `nodata_code` that collides with a real class, and
why a population count of `0` is distinguished from `None`.

---

## Roads

### F-RD-1 — Under-connection from unnoded crossings (BY DESIGN, reported)
Lines crossing without a shared endpoint are **not** connected (D-0007), because
a mid-segment crossing is frequently a bridge or an underpass and auto-noding
would fabricate a junction. A network digitised without junction nodes therefore
reports as fragmented. `RD-006` reports suspected missing junctions so the
analyst decides; `build_road_graph(node_crossings=True)` is the explicit,
recorded opt-in.
**Why this direction:** under-connection is detectable (extra components, extra
dead ends); over-connection is not, and would silently turn a single-egress
community into a two-egress one.

### F-RD-2 — Egress counts that are artifacts of the clip (CAUGHT as a finding)
Clipping a road network both **creates** exits (every cut end near the boundary)
and **destroys** them (a real second egress just outside the extent). `RD-005`
fires whenever any exit was inferred from the boundary rather than from a source
tag, and `ExitNodeSet` keeps the two kinds separate.
**Mitigation:** re-run with a larger study area and check whether the egress
count is stable. An unstable count is a property of the extent, not of the
village.

### F-RD-3 — Incomplete road data read as complete (NOT CAUGHT)
OSM rural Korean completeness is **UNKNOWN**. A missing farm track makes a
hamlet look single-egress when it is not; a missing link makes a component look
disconnected. No check can establish completeness.
**Mitigation:** recorded as UNKNOWN in provenance. Authoritative NGII/VWorld
data would be better and is the first item in `tasks/CURRENT.md`.

### F-RD-4 — Transitive endpoint snapping merging distant nodes (CAUGHT as a note)
Endpoint clustering is transitive, so a chain of endpoints each 0.9 m from the
next collapses into one node spanning several metres even at a 1.0 m tolerance.
`RoadGraph.max_intra_cluster_distance_m` reports the largest such span, and the
QA report adds an explicit note when it exceeds the tolerance.

### F-RD-5 — A duplicated road read as a critical link (CAUGHT)
Two distinct roads between the same junctions are two ways out. Collapsing them
to one edge would make that edge look like a bridge whose loss disconnects the
junction. Bridge detection is multiplicity-aware: an edge is a bridge only if it
is a bridge in the simple graph **and** has no parallel twin.

### F-RD-6 — A settlement that is effectively single-egress but is not flagged as one (BY DESIGN)
`single_egress_candidates` is a **component-level** property: a component with
one exit node. A hamlet on a dead-end spur off a trunk road with two exits is
*not* a single-egress candidate, because its component has two exits — even
though losing one spur segment isolates it. That case is reported instead by
`critical_links`, which names the edges whose removal disconnects a settlement
from every exit. **Read both fields**; neither alone describes egress fragility.

Note what `critical_links` deliberately does **not** include: a settlement that
has no exit in the intact graph at all. "Cuts off" is a change of state, so such
a settlement cannot be newly disconnected by removing an edge; it is reported
once, as `no_egress_components`. An earlier implementation attributed it to every
bridge anywhere in the graph, which inflated the count in proportion to how
fragmented the road data was — worst exactly where the data is weakest.

### F-RD-7 — Planar length read as travel distance (BY DESIGN)
Edge lengths are planar 2-D lengths in the projected CRS: not slope-corrected,
not geodesic, not travel distances. On a 30° Korean hillside the true surface
length is about 15% longer. The QA report states this in-band with every length.

---

## Vectors

### F-VEC-1 — Projected GeoJSON read as longitude/latitude (CAUGHT)
RFC 7946 mandates WGS 84 longitude/latitude for GeoJSON. This package writes
GeoJSON in the **study area's own projected CRS** and declares that CRS in a
non-standard `"crs"` member as well as in the provenance sidecar.

**Why that trade.** Silently reprojecting every vector layer to EPSG:4326 on
write and back on read would put two datum-dependent conversions into every
round trip; refusing to write projected GeoJSON would force every bundle through
a lossy conversion. Staying in the analysis CRS avoids both, at the cost of a
file that a standards-strict reader will misplace.

**How it is caught.** `read_geojson` refuses to default an undeclared CRS to
EPSG:4326 — an unlabelled file raises rather than being read as degrees. Korean
metre coordinates read as degrees would land near the Gulf of Guinea, which is
obvious; the dangerous version is a *small* projected extent being read as a
*small* degree extent, so the default is refusal rather than a guess. If the
caller passes a CRS and the file declares a different one, that also raises:
two disagreeing beliefs are a real conflict, not something to resolve by
precedence.

**Limit.** A third-party reader that ignores the `"crs"` member will misplace
the geometry. Any consumer outside this repository must read the provenance
sidecar, as `INTERFACES.md` requires.

---

## Population and facilities

### F-POP-1 — The privacy guard is a tripwire, not a boundary (NOT CAUGHT, by admission)
`PrivacyGuardError` matches **attribute names**. It cannot inspect values, and
renaming a prohibited column to `notes` defeats it entirely. Its purpose is to
make the prohibited path fail loudly for whoever tries it — including a future
agent following an instruction that seemed reasonable in isolation
(D-0011). The prohibition itself lives in `AGENTS.md` §5 and
`ASSUMPTIONS.md` A-POP-1/2, and is a rule about conduct, not a feature.

### F-POP-2 — Disclosive small counts (CAUGHT as a warning, deliberately not suppressed)
`POP-003` flags an aggregate count below the k-anonymity floor (default 5) but
does **not** suppress it: a genuinely 3-person Korean hamlet is a real study
object, and deleting it would falsify the landscape. The finding is the warning
that publishing that figure is disclosive.

### F-POP-3 — The wrong population concept (NOT CAUGHT)
Residential-register counts, census counts, and daytime/present population
differ substantially in rural Korea, where registered residents may not be
present. `count_basis` records which — and is `UNKNOWN` when the source does not
say, which `POP-004` reports.

### F-POP-4 — Strata and totals reconciled instead of reported (BY DESIGN)
When age strata do not sum to the stated total, `POP-001` reports it and nothing
is rescaled. A mismatch usually means the two come from different dates or
definitions, and rescaling one to fit the other destroys that signal.

### F-FAC-1 — A facility assumed usable because it is in a dataset (NOT CAUGHT, by construction)
A building tagged `amenity=shelter` in OSM may be a bus-stop shelter. A fire
station may be unstaffed at night. A "temporary refuge candidate" may be a field
that is unreachable in smoke. `operational_status` defaults to `UNKNOWN`,
`suitability_assessed` defaults to `False`, capacity is `None` unless sourced and
is **never** estimated from footprint area, and `FAC-001`/`FAC-002` report the
gaps. No check can substitute for a site assessment.

---

## Bundles and process

### F-BND-1 — Data drifting from its provenance (CAUGHT)
Every layer file is checksummed into both the manifest and its provenance
sidecar, and `read_bundle` re-checks on every read. A bundle whose data changed
under its provenance raises `BundleError` rather than loading.

### F-BND-2 — An unrecognised bundle or provenance version (CAUGHT)
Both carry `schema_version` and refuse to guess an unrecognised layout.

### F-BND-3 — Synthetic data mistaken for real (CAUGHT as a finding)
Every synthetic layer is `DataClass.SYNTHETIC`, says `SYNTHETIC` in its
provenance notes, and triggers `BND-006`. `BND-007` fires when a bundle
containing synthetic layers is *named* after a real place without `synthetic` in
its id — the fixtures deliberately use realistic Korean coordinates, which makes
a CRS mistake visible but also makes a naming mistake dangerous.

### F-BND-4 — A validation report read as a correctness guarantee (NOT CAUGHT)
A passing report means no named check found a problem. It does not mean the
values are true, the source was appropriate, or the coverage is complete. The
list of what validation does not check is at the end of `VALIDATION.md`.

### F-BND-5 — A single-tile Copernicus limitation (CAUGHT)
`fetch_copernicus_dem` raises if a requested extent spans more than one 1°
tile; mosaicking is not implemented. Returning one tile's worth of a two-tile
request would hand back a study area with an artificial straight edge.

### F-BND-7 — An edited QA report, statistic or provenance sidecar (CAUGHT)
Schema 1.1.0 checksums the manifest extras and the provenance sidecars as well
as the layer files (`BND-017`..`BND-020`). Before that, only the rasters and
vectors were covered — so editing `roads_qa.json`, the artifact a downstream
reader is most likely to consume without re-deriving it, went undetected. The
validation report itself cannot be covered, because it is written after
validation runs (D-0014); the manifest says so in its `unchecksummed` block.

### F-BND-6 — Resampling within one CRS requested implicitly (CAUGHT)
If a source is already in the analysis CRS but at a different resolution than
`target_resolution_m`, the build raises rather than resampling: resampling
changes every value, and doing it because two numbers disagreed in a config
would leave nothing in the output to show which estimator was used.
