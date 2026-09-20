# Phase 2 integration readiness

Phase 2 item 33. Written at the `v0.2.0` freeze point.

**Short answer:** this repository is ready to be *depended on* for terrain,
land cover, roads and facility existence, across two real Korean geographies
and a deterministic CI fixture, behind a versioned contract that cannot drift
from its own data. It is **not** ready for anything needing aggregate
population, and it has never carried real population data at all.

## State at the freeze

| | |
|---|---|
| Tests | **446 passed**, 3 deselected (network-marked); all 3 also pass |
| Lint | `ruff check src tests` clean |
| Committed bundles | 5 |
| Bundles with ERROR findings | **0** |
| Decision records | 31 (`D-0001`–`D-0031`) |
| Validation checks | 53, catalogue pinned to the code by a test |
| Reports | 7, this one included |

| Bundle | Layers | ERROR | WARNING | INFO |
|---|---|---|---|---|
| `uljin_real_v2` | terrain, roads, fuels, facilities | **0** | 12 | 14 |
| `naju_real_v1` | terrain, roads, fuels | **0** | 11 | 11 |
| `uljin_real_v1` | terrain, roads | **0** | 9 | 6 |
| `uljin_valley_synthetic_v1` | all five (synthetic) | **0** | 23 | 19 |
| `wg_integration_fixture_synthetic_v1` | all five (synthetic) | **0** | 10 | 19 |

---

## The twelve questions

### 1. Is the road graph trustworthy, and is its fragmentation understood?

**Yes, and the cause was ours.** `uljin_real_v1` reported 22 components. Phase 1
wrote that up as likely OSM rural sparsity. That was wrong, and
`reports/ULJIN_ROAD_AUDIT.md` establishes the real cause three independent ways:
35 of 41 shared OSM node IDs were *interior vertices* of a way and the builder
noded only at endpoints; shared-vertex noding (D-0020) collapses 22 components
to **2 with identical total length** (28.784 km); and an independent OSMnx
pipeline over the same ground agrees to ~12% on edge count. Class filtering and
clipping were both excluded.

The strongest evidence that the diagnostics now measure the world rather than
the pipeline is that the same check returns opposite answers in two places, each
verifiable: Uljin reports **0** crossings-without-a-node post-fix, while Naju
reports **10 of 10** carrying a `bridge`, `tunnel` or `layer` tag — real grade
separation over a railway and a river.

**Caveat, unresolved:** Naju's 7 components are **not** audited to that depth.
98.7% of edges are in one component and the other six total 4.95 km of 184, but
nothing claims they are real rather than clip artifacts.

### 2. Is there a useful real Korean bundle?

**Yes — `uljin_real_v2`, with four of five layers.** Terrain (Copernicus
GLO-30), land cover (ESA WorldCover 2021 v200), roads and facilities (OSM). It
satisfies `OSSE` and `ASSISTED_DISPATCH` at `READY_WITH_LIMITATIONS`;
`FORECAST_VALUE` is `INCOMPLETE` and names population as the only thing it
lacks.

"Useful" needs one qualification stated plainly: its **facilities layer contains
zero refuge candidates**. All three OSM facility elements map to `other`,
because two of the three are a gazebo and the site of a demolished school (Q7).

### 3. Does the schema generalise beyond one place?

**Yes for terrain, land cover, roads, CRS handling, the grid contract and the
manifest. Not tested for population or facilities.**
`reports/SECOND_GEOGRAPHY.md`: `naju_real_v1` differs from Uljin by roughly an
order of magnitude in relief (52 m vs 570), mean slope (2.40° vs 22.43°) and
road density (1324 edges vs 68), and sits in a **different belt CRS** (5186 vs
5187). It built with 0 ERRORs and populated the same manifest.

Three code paths reached real data for the first time there, most notably
**flat-ground aspect**: Uljin has *zero* flat cells, Naju has **619**, and every
one is `NaN` — never `0`. Until that bundle existed, D-0006 was tested only
against a synthetic flat fixture.

**The honest limit:** neither geography has ever carried real population data,
and only Uljin carries real facilities. So "the schema generalises" is
established for four slots and *asserted, untested* for population.

### 4. Is the manifest and schema stable enough to freeze?

**Yes, and it is a separate artifact from the storage layout for that reason.**
`bundle_manifest.json` (D-0027) carries its own `bundle_schema_version`,
independent of the on-disk layout's and of each provenance record's — three
versions, because they change for three different reasons.

It cannot drift: it is *derived* from the bundle, and `validate-study-area`
re-derives it and reports any difference as **ERROR `BND-023`**. That check
earned its place immediately — it found that the contract was being built from
the in-memory bundle, whose provenance still reads `UNKNOWN` because checksums
exist only once the files do.

### 5. Are missing layers documented, or merely missing?

**Documented, in the artifact a consumer reads.** All five slots are always
present in the contract with a `status`; an absent one carries a `reason`
(D-0031). `uljin_real_v2` states that population is absent because KOSIS and
SGIS are both `PROXY_FAILURE` here, that OSM carries no population tag anywhere
in the box, and that nothing was estimated.

`reason: "UNKNOWN"` now means specifically **"the gap was not documented"**,
which is itself a finding about the bundle's preparation.

### 6. Are the adapter contracts specified?

**Specified, and nothing more — which is the requirement.**
`integration/compatibility.py` describes `FORECAST_VALUE`, `OSSE` and
`ASSISTED_DISPATCH` as required inputs plus a machine-readable
`not_supplied_here` list. That list is the load-bearing half: it turns
`docs/SCOPE.md` from a paragraph somebody must find into a field somebody's code
can read — forecasts and fuel-model crosswalks, the nature model's dynamic state
and observation operators, and all mission logic, routing and travel-time
estimation.

No adapter *code* was written, and none should be until a real consumer exposes
a gap (item 34).

### 7. Can a consumer mistake a facility for a refuge?

**Not from this repository's output, and this is the finding Phase 2 turned up
that mattered most.** It is not hypothetical — see `docs/FAILURE_MODES.md`
F-FAC-3. OSM has three facility-tagged elements in the Uljin box and two would
be badly misread by any tag-trusting pipeline:

- `amenity=shelter` is 구산리청암정 with `shelter_type=gazebo` — a traditional
  pavilion;
- `amenity=school` is "구 노음초등학교 구고분교 **터**" — `터` means *site of*;
  the school does not stand.

So `fetch_osm_facilities` assigns **no role at all**, `kind_map` is required with
no default, and the contradicting evidence survives into the bundle so the
mapping stays auditable. A test asserts `gazebo` and `터` are both still there.

**What is still not prevented:** an operator can write
`"amenity=shelter": shelter` in a config. The guard is that they must write it,
in a reviewed file, with the contradiction beside it.

### 8. Can a consumer mistake land cover for a fuel model?

**Not without ignoring three fields.** `SchemeKind.SOURCE_CLASS` says the
classes are ESA's published legend; `is_fire_behaviour_fuel_model: false` is in
the contract; and D-0024 records that no crosswalk is shipped or implied.
`VegetationClassScheme` raises if a `MODELED_CROSSWALK` scheme is declared
`OBSERVED`.

The best Korean source — **임상도**, 1:5,000, EPSG:5179, 51 species groups with
diameter, age and crown-density classes — is fully documented in
`reports/KOREAN_FUELS_CANDIDATES.md` and **not ingested**, because its product
page states CC-BY *and* "all rights reserved". The licence is `UNKNOWN` until
that is settled, and nothing derived from it may be committed.

### 9. Is temporal leakage possible?

**Structurally discouraged, not prevented.** `temporal_class` distinguishes
`RETROSPECTIVE` from `OBSERVATION_TIME`; `valid_from`/`valid_to` say when values
are *true of*, separately from when the data is *from* (D-0025); `PRV-010` warns
when validity is unestablished; and D-0023 collapses the two axes at the export
boundary with `RETROSPECTIVE` dominant, because over-reporting makes a planner
refuse legal data while under-reporting hands it a fact from the future.

**The gap:** the Copernicus DEM's validity is `UNKNOWN`, deliberately — how long
a DSM stays valid is genuinely open, since its bare-earth component is static
and its canopy component is not. So `PRV-010` fires on every terrain layer in
every real bundle. That is honest and it is also unresolved.

### 10. Has anything been independently checked?

**Yes, and it had never been done.** `reports/BENCHMARK_CROSS_CHECK.md` runs
`WG-BM-001..008` from `wildfireguardian-benchmarks` — 8 cases nobody had ever
pointed at this repository. Terrain: **47 of 52** assertions matched; graph:
**19 of 21** checkable.

Every mismatch is explained and none is a logic defect. All five terrain
mismatches are one number: a **1.11 × 10⁻⁷ degree** float32 storage difference
against a 1 × 10⁻⁹ tolerance. Evaluating Horn's kernel by hand in float64 gives
the benchmark's value **bit-for-bit**, so the arithmetic is exact and the
disagreement is a storage-precision choice — not changed here, because the
source DEM is itself float32 and a float64 slope would advertise precision the
data does not have.

`WG-BM-004`, *"missing terrain stays missing and does not become zero"*, passes
every structural assertion including which 9 cells a 3×3 estimator must abandon
— derived by hand there, reproduced cell-for-cell here.

### 11. What did comparing with the main repository change?

`reports/LEGACY_DATA_PIPELINE_COMPARISON.md`, and it went against this
repository twice, recorded as such:

- **`measure_osm_completeness.py` is better than anything here**, on a gap this
  repository documented (`F-RD-3`) and left at `UNKNOWN`. `PORT_WITH_FIXES`.
- **Multi-tile mosaicking is a real capability this repository lacks.** Refusing
  a straddling request is the right default, not a complete answer.

Two `REJECT`s are argued from measurement: the legacy SRTM loader sets nodata
and bathymetry to 0 (its own sibling script records hitting the consequence —
nodata read as FLAT for 405 of 7,300 nodes), and `np.gradient` differs from Horn
on 68.5% of real cells by more than a degree while agreeing exactly on a plane,
which is why the documented equivalence claim survived.

It also confirmed, from the source rather than from behaviour, that
`retain_all` is unset there — so osmnx silently discards every non-largest
component, which is why the legacy pipeline looked perfectly connected where
this one found 22 components.

### 12. What is the single biggest thing still missing?

**Aggregate population.** Every authoritative source (KOSIS, SGIS) is
`PROXY_FAILURE` from this environment; OSM has no `population` tag in either
study box. `FORECAST_VALUE` is `INCOMPLETE` solely because of it, and the road
QA's settlement-dependent diagnostics — single-egress candidates, critical links
— are **structurally unavailable** on both real bundles without settlement
nodes.

Second biggest: **임상도 is unblocked and unretrieved.** It needs a free account
and a licence answer, not a better fetcher. That is a human with a browser.

---

## What a downstream repository may rely on at `v0.2.0`

1. `bundle_manifest.json`, at `bundle_schema_version` 1.0.0, with all five slots
   always present and absence carrying a reason.
2. The `wg-data` CLI, including `provenance` and `compatibility`.
3. `wg_integration_fixture_synthetic_v1` as a CI dependency: 83 KB,
   deterministic, with closed-form expected answers rather than snapshots.
4. That no field anywhere asserts anything is safe, passable, usable or a viable
   refuge.

## What it may not

1. `StudyAreaBundle`, the Python API, `manifest.json`, or the provenance
   sidecars (D-0001).
2. Any population figure. There is none.
3. `READY` as a quality claim. It means the named inputs are present with
   interpretable provenance, and nothing else — an all-synthetic bundle is
   `READY` for all three profiles, which is what a fixture is for (D-0028).
4. Travel times, one-way semantics, or destination selection. Edge lengths are
   planar geometry; `oneway` is an opaque attribute; the graph is undirected by
   design.

## Freeze

Item 34's conditions are met: road fragmentation understood, a useful real
bundle exists, a second geography validates generality, the manifest and schema
are stable and drift-checked, missing data is documented, adapters are specified
without being built, and all tests pass.

**From here, stop adding data-processing abstractions until a real downstream
need exposes a gap.** The two known gaps are not abstractions — they are a
registration form and a proxy.
