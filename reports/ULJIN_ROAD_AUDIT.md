# ULJIN ROAD AUDIT

**Question.** The Phase 1 real bundle's road graph reported **31 segments in 22
connected components**, with the largest component holding 12.9 % of edges. Is
that real rural sparsity, an OSM data-quality problem, or something this
repository's own pipeline did?

**Answer: overwhelmingly pipeline-induced.** The graph builder connected lines
only where their **endpoints** coincided. OpenStreetMap encodes a junction as a
**shared node**, which is usually an **interior** vertex of a way — a side road
meeting the middle of a through road. Of the 41 OSM node IDs shared between road
ways in this extract, **35 involve an interior vertex**, so the builder discarded
most of the topology the source explicitly carries.

Fixing that (`docs/DECISIONS.md` D-0020) takes the same 31 source ways from **22
components to 2**, with **no change in total length** — 28.784 km before and
after. Nothing was added, removed, or moved; only the topology was read
correctly.

| Metric | Endpoint-only (Phase 1) | Shared-vertex noding (D-0020) |
|---|---:|---:|
| Source ways | 31 | 31 |
| Graph edges | 31 | 68 |
| Graph nodes | 53 | 55 |
| **Total length** | **28.784 km** | **28.784 km** |
| **Connected components** | **22** | **2** |
| Single-edge components | 17 | 0 |
| Largest component, share of edges | 12.9 % | **94.1 %** |
| Largest component, share of length | 27.0 % | **75.4 %** |
| Dead ends | 42 | 13 |
| Isolated segments | 16 | 0 |
| Crossings at a non-node point | 2 | 0 |
| Parallel edge pairs | 0 | 1 |
| Self-loops | 0 | 0 |
| Exit nodes | 5 | 4 |

Reproduce with `python experiments/uljin_road_audit.py`; it needs no network and
writes the CSV, SVG and JSON referenced below.

## Component maps

| | |
|---|---|
| `uljin_road_components_endpoint_only.svg` | 22 components, one colour each — a rainbow of fragments *within a single physical village cluster*, which is the visual signature of a topology bug rather than a sparse network |
| `uljin_road_components_shared_vertex.svg` | 2 components: the whole valley system in one colour, plus one genuinely separate road in the north-west |

Per-component tables: `uljin_road_components_endpoint_only.csv`,
`uljin_road_components_shared_vertex.csv`.

## The thirteen questions

### 1. Which OSM highway classes were requested?

Sixteen, from `sources.DEFAULT_OSM_HIGHWAY_VALUES`: `motorway`, `trunk`,
`primary`, `secondary`, `tertiary`, `unclassified`, `residential`,
`living_street`, `service`, `track`, `road`, and the five `*_link` variants.

### 2. Which were excluded?

`footway`, `path`, `steps`, `cycleway`, `pedestrian`, `bridleway`, and
`construction`/`proposed` — as non-vehicle access. **This excluded nothing
here:** the extract's 42 ways contain only four highway classes, all of them
requested (`service` 15, `track` 12, `unclassified` 3, `residential` 1). The
other 11 ways carry no `highway` tag at all. `classes_present_but_excluded` is
empty.

### 3. Were service roads included? — **Yes**, and they are the largest class (15 of 31).

### 4. Were residential roads included? — **Yes** (1 way; rural Uljin has almost none).

### 5. Were tracks/pathways included? — **Tracks yes** (12 ways). Footways and paths are excluded by class, but none exist in this bbox.

### 6. Were bridges/tunnels handled?

Two ways carry `bridge=yes` with `layer=1`; no way carries `tunnel`. **Both
bridges cross streams, not roads** — verified against the cached extract: way
`1195992934` crosses `waterway=stream` `580098047`, and way `1238511109` crosses
a stream and one untagged way. So **this extract contains no road-over-road
grade separation at all**, and no correct graph should show a road-road crossing
here.

That matters for interpreting the "crossings" count, and corrects a guess made
in the Phase 1 write-up. The two crossings the endpoint-only graph reported were
**not** the bridges: both crossing pairs carry `bridge=null` on both edges, and
both pairs **share an OSM node**. They were *missing junctions* — the very defect
this audit found. Under D-0020 they are correctly noded and the count is 0.

### 7. Were road segments broken at bbox edges? — **No.**

`clip_mode: intersects` keeps whole features, so nothing was cut. Provenance
records `features_in: 31, features_out: 31, features_truncated: 0`. The road
layer's bounds (227430–233628 E) legitimately extend past the study bounds
(227500–231500 E) as a result. That is the A-RD-4 choice: truncating would turn
every cut end into a new degree-1 node that would then read as a dead end.

### 8. Were geometries lost during clipping? — **No.**

31 in, 31 out, 0 truncated, and `ways_skipped_incomplete: 0` at fetch. Total
length is identical before and after the topology fix, which is the strongest
available check that the audit changed no geometry.

### 9. Were disconnected components caused by missing intersections? — **Yes. This is the cause.**

41 OSM node IDs are shared between kept road ways. Only **6** are
endpoint-to-endpoint; **35 involve an interior vertex**. 26 of the 31 ways have
interior vertices, one with 544 of them. Endpoint-only connection therefore
recovered 6 of 41 real junctions, and the other 35 became fragment boundaries.
Splitting at shared vertices made **37 splits** and collapsed 22 components
into 2.

### 10. Are ways geometrically crossing but not topologically connected?

**Not any more, and not for the reason first assumed.** There are exactly two
road-road crossings in the source, and **both share an OSM node** — real
junctions. After D-0020 both are noded, and the diagnostic correctly reports 0.

This audit also fixed the diagnostic itself. It previously skipped any edge pair
that shared a node *anywhere*, so a bridge whose far end rejoins the same road
— the clearest real case of grade separation — was exactly the case it missed.
It now tests whether the **crossing point** is a graph node, and reports the
`bridge`/`tunnel`/`layer` tags of both edges so a reader can tell grade
separation from a missing junction.

### 11. How many components contain only one edge?

**17 of 22** under endpoint-only. **0 of 2** after the fix. A network where 77 %
of components are single edges is a topology artifact, not a road network.

### 12. What fraction of the network lies in the largest component?

| | Endpoint-only | Shared-vertex |
|---|---:|---:|
| By edge count | 12.9 % | **94.1 %** |
| By length | 27.0 % | **75.4 %** |

The remaining component after the fix is real: 4 edges, 7.09 km, in the
north-west, with **3 boundary-crossing exits** — a road that leaves the extent
in three places and simply does not meet the valley network inside this 4 × 4 km
window. That is a statement about the extent, not about the road system.

### 13. How does the result change under broader road-class inclusion?

**It does not change at all.** Every highway class present in the bbox was
already requested, and the 11 remaining ways have no `highway` tag. Broadening
the filter to footways and paths would add zero features here. This is worth
stating plainly: class filtering was **not** a contributing cause, and a reader
who assumed it was would have been chasing the wrong thing.

## Two further findings

**Exit detection was badly under-reporting.** With `clip_mode: intersects`, a
road leaving the study area has no *node* near the boundary, so node-proximity
found **1** exit where **4** roads actually cross the boundary. Exit detection
now also tests whether an edge's **geometry crosses the boundary ring**, and
records that separately from node proximity (D-0021). Related: a source-tagged
exit edge previously marked *both* its endpoints as exits, which over-counts and
could stop a genuinely single-egress component being reported as one; exits are
now the *departure* nodes — the endpoints inside the study area.

**A finding that survives the fix.** The main component — 50 nodes, 64 edges,
21.7 km, containing the whole village cluster — has **exactly one boundary
crossing** within this extent. Under this graph definition that is a single
topological egress for the entire valley network. It is reported as such and
nothing more: with no settlement layer for this bundle there is no
`single_egress_candidate`, and the count is as much a property of where the
4 × 4 km box was drawn as of the road system. Widening the extent is the way to
test whether it is stable (`docs/FAILURE_MODES.md` F-RD-2).

## What is still genuinely unknown

- **Completeness.** OSM rural Korean coverage remains `UNKNOWN`
  (F-RD-3). The west and south of the extent are empty of roads; that is
  consistent with steep forested terrain, but this audit cannot distinguish
  "no road there" from "no road mapped there". Authoritative NGII/VWorld data
  is the way to settle it (`tasks/CURRENT.md` item 1).
- **Attributes.** Of the attributes downstream models would want, the extract
  carries almost none: `bridge` on 2 ways and `layer` on 2. **`oneway`,
  `lanes`, `surface`, `maxspeed`, `access`, `width` and `tracktype` are absent
  from every kept way.** They are recorded as absent rather than defaulted
  (`docs/DECISIONS.md` D-0022).
- **Directionality.** With no `oneway` tag anywhere, the undirected graph
  (A-RD-1) loses nothing here. That is luck, not a general result.

## Conclusion

The fragmentation was **not** an OSM quality problem and **not** a class-filter
problem. It was this repository's graph builder reading endpoint coincidence
where OSM states junctions as shared nodes. After the fix the same 31 source
ways form 2 components holding 94 % of edges in one, with identical total
length, and the one remaining split is a real property of the extent.

The Phase 1 QA report was not wrong about what it measured — it measured
endpoint connectivity accurately. It was wrong to present endpoint connectivity
as the road network's connectivity.
