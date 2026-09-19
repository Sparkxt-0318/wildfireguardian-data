# GLOSSARY

Terms are defined **as this repository uses them**. Where a term is used
differently elsewhere in wildfire science or GIS, the difference is stated.

## Data character

**observed** — A measurement of the physical world by an instrument or a survey,
carrying the instrument's error characteristics. Example: a lidar DEM, a field
road survey.

**modeled** — Output of a physical or statistical model, whose values did not
come from measuring the cells they describe. Example: an interpolated canopy
layer, a gap-filled DEM.

**derived** — Computed by this repository from another layer by a documented
transformation. Slope derived from a DEM is `derived`; its `parents` field names
the DEM. Derived data inherits the worst reliability of its parents.

**synthetic** — Constructed for testing, with no claim about any real place. All
fixtures in `src/wildfireguardian_data/fixtures/` are synthetic. Synthetic is
*not* the same as **simulated**: a synthetic tilted plane is a test input with an
analytically known answer, whereas a simulated observation is an attempt to
mimic a real observing system. This repository produces the former and, by
`SCOPE.md`, not the latter.

## Temporal classes

**static** — Assumed not to change over the study period *for data-preparation
purposes*. Terrain is `static`. This is an approximation, false after a
landslide or a road cut.

**annual** — One value per year; the year is the layer's temporal reference.
Example: a forest-type map compiled for 2023.

**monthly** — One value per calendar month.

**observation_time** — Valid at a specific timestamp, which the layer carries.
Example: a road-closure snapshot at 2026-03-14T09:00+09:00.

**retrospective** — Compiled after the fact about a past period, and therefore
not available in real time. A retrospective burn perimeter is *not* a layer a
real-time decision could have used. Confusing `retrospective` with
`observation_time` is the temporal analogue of data leakage.

## Spatial terms

**study area** — A named, bounded region with one declared analysis CRS, for
which a bundle is built. Identified by `study_area_id`.

**bounds** — `(min_x, min_y, max_x, max_y)` in the layer's own CRS. Always
stated with its CRS; a bounds without a CRS is meaningless and is rejected.

**cell size / spatial resolution** — `(x_size, y_size)` as positive lengths in
the raster's CRS units. Reported as a pair, never as a single number, because
non-square cells occur after naive reprojection.

**north-up** — An affine transform whose rotation terms are zero and whose
`y` step is negative (row index increases southward). Required; see A-RAS-1.

**aligned** — Two rasters are aligned iff same CRS, same cell size, same grid
origin modulo cell size, and same shape. See A-RAS-5.

**grid north vs true north** — Aspect here is measured from **grid** north (the
`+y` axis of the projected CRS). True north differs by the meridian convergence,
which is nonzero away from the projection's central meridian. Not corrected.

## Terrain terms

**DEM** — Digital elevation model, used here as the neutral term.

**DSM** — Digital *surface* model: includes vegetation canopy and buildings.
Copernicus GLO-30 is a DSM.

**DTM** — Digital *terrain* model: bare earth.

A slope computed from a DSM over Korean forest is the slope of the canopy
surface. Over a 30 m cell with a 20 m canopy-height step at a stand edge, this
can produce a spurious 30°+ slope. See `FAILURE_MODES.md` F-TER-3.

**slope** — Magnitude of the surface gradient, in degrees from horizontal
(default), percent rise, or radians. Always carries its unit.

**aspect** — Compass direction the slope faces (downslope), degrees clockwise
from grid north, `NaN` where slope is zero.

## Road-graph terms

**node** — A junction or line endpoint after snapping within
`snap_tolerance_m`.

**edge** — A road segment between two nodes, carrying planar length in metres.

**connected component** — A maximal set of mutually reachable nodes in the
undirected graph.

**isolated segment** — An edge in a component that contains no other edge and no
exit node: a stub disconnected from the rest of the network as digitised.

**dead end** — A node of degree 1 that is not an exit node.

**exit node** — A node tagged as an exit by the source, or within
`boundary_tolerance_m` of the study-area boundary. A clip creates exit nodes.

**settlement node** — A node associated with a populated place, either tagged in
the source or matched to a settlement centroid within `settlement_snap_m`.

**single-egress candidate** — A component containing ≥1 settlement node and
exactly 1 exit node. A topological property of the data, not a claim about
people or about safety. See D-0008.

**articulation point** — A node whose removal increases the number of connected
components.

**bridge** (graph theory) — An edge whose removal increases the number of
connected components. **Not** a structural bridge over a river; the field is
named `bridge_edges` and the structural sense never appears in this repository's
vocabulary.

**chokepoint / critical link** — An edge whose removal disconnects at least one
settlement node from every exit node. A cut property, reported with the
settlement nodes it would cut off, and with no travel-time or safety meaning.

## Provenance terms

**source** — Where the data came from: a named organisation or dataset, with a
URL or identifier where one exists.

**source_date** — When the data describes the world.

**acquisition_date** — When this repository obtained it.

**transformation** — One recorded operation (reproject, clip, resample, derive,
rasterise, snap), with its parameters, applied in order.

**checksum** — SHA-256. For a file, over its bytes. For an in-memory array, over
`dtype`, `shape`, and the `C`-contiguous buffer.

**UNKNOWN** — The literal string used where a source does not supply a fact. A
required, countable value; never a guess. See D-0009.

## Units

**distance** — metres (`m`) canonical; kilometres and feet convertible.

**elevation** — metres above the stated vertical datum; the datum is part of
provenance and may be `UNKNOWN`.

**slope** — degrees (default), percent rise, or radians.

**time** — seconds canonical; timestamps are timezone-aware, Korean local time
is `Asia/Seoul` (UTC+09:00, no DST).

**area** — square metres.

## Things this repository never says

**safe**, **passable**, **usable**, **trapped**, **at risk**. These require
assumptions this repository does not hold. See `SCOPE.md`.
