# CURRENT

**Status: Phase 2 complete; frozen at `v0.2.0`.** Read `AGENTS.md` before
starting anything below. Each item states what "done" looks like, because
"improve the road data" is not a task.

> **The freeze rule (Phase 2 item 34).** Do not add data-processing
> abstractions until a real downstream need exposes a gap. The two known gaps
> are not abstractions: one is a registration form, the other is a proxy.

Phase 2's outcome is in `reports/PHASE2_INTEGRATION_READINESS.md`. In short:
five committed bundles, **0 ERROR findings in all five**, two real Korean
geographies in different belt CRSs, a versioned contract that cannot drift from
its own data, and 31 decision records. 446 tests pass.

### The two things actually blocking further data work

1. **Aggregate population — nothing, anywhere.** KOSIS and SGIS are
   `PROXY_FAILURE` from this environment and OSM has no `population` tag in
   either study box. `FORECAST_VALUE` is `INCOMPLETE` solely because of this,
   and the settlement-dependent road diagnostics (single-egress candidates,
   critical links) are **structurally unavailable** on both real bundles.
2. **임상도 is unblocked and unretrieved.** The authoritative Korean forest
   stand map needs a free account on `www.bigdata-forest.kr` and an answer to
   its licence contradiction — its page states CC-BY *and* "all rights
   reserved". A human with a browser, not a better fetcher. Fully documented in
   `reports/KOREAN_FUELS_CANDIDATES.md`.

### Ported-in work identified but not done

`reports/LEGACY_DATA_PIPELINE_COMPARISON.md` classified two components of the
main repository as `PORT_WITH_FIXES` and neither has been ported:

- **`measure_osm_completeness.py`** — measures the thing `F-RD-3` leaves at
  `UNKNOWN`. Better than anything here.
- **Multi-tile mosaicking** — a real capability this repository lacks; refusing
  a straddling request is the right default but not a complete answer.
- A **nodata-fraction gate** (refuse a raster over 50% nodata) is also
  classified `PORT_WITH_FIXES` and deliberately not adopted: promoting a
  WARNING to an ERROR is a scientific change and needs its own reviewed commit.

### Open questions recorded rather than answered

- **Naju's 7 road components are not audited** to the depth of the Uljin
  report. 98.7% of edges are in one component and the other six total 4.95 km
  of 184, but nothing claims they are real rather than clip artifacts.
- **The Copernicus DEM's temporal validity is `UNKNOWN`** on purpose, so
  `PRV-010` fires on every terrain layer in every real bundle. How long a DSM
  stays valid is genuinely open: its bare-earth component is static and its
  canopy component is not.
- **The benchmark tolerance disagreement** (`reports/BENCHMARK_CROSS_CHECK.md`):
  1.11 × 10⁻⁷ degrees of float32 storage against a 1 × 10⁻⁹ tolerance. Not
  changed here; the recommendation belongs to the benchmark repository.

## 1. Replace or cross-check the road source with authoritative Korean data

**Why it is first.** The real bundle's road network is OpenStreetMap, whose
rural Korean completeness is **UNKNOWN** (`FAILURE_MODES.md` F-RD-3). Every
egress and connectivity diagnostic inherits that limitation, and those are the
diagnostics the wider project most wants. A missing farm track makes a hamlet
look single-egress when it is not.

**What to do.** Obtain NGII / VWorld road data (see
`docs/DATA_PROVENANCE.md` §Access attempts; `vworld.kr` failed here as a proxy
tunnel closure, not a service outage — **re-try it**). Add a source kind to
`study_area/config.py` and a loader, with a real `SourceRecord` including
licence terms you have actually read.

**Done when.** A real study area builds from authoritative Korean road data; its
QA report is compared with the OSM-derived one for the same extent, and the
disagreement is written up (not resolved by preference) in
`docs/DATA_PROVENANCE.md`.

## 2. Obtain a DTM, or quantify the DSM error

**Why.** Copernicus GLO-30 is a digital *surface* model, so slope over Korean
forest is canopy slope (F-TER-3). A 20 m canopy step across one 30 m cell
fabricates a ~34° slope. This is currently documented but not measured.

**Done when.** Either a Korean DTM is ingested and the two slope fields are
compared over forested and non-forested cells with the difference reported; or,
if no DTM is obtainable, a written estimate of the bias with its method, added
to `FAILURE_MODES.md` F-TER-3.

## 3. Ingest 임상도, the Korean forest stand map

**Status: unblocked, and the highest-value action left.** Superseded in part —
ESA WorldCover now provides *global* land cover for the real bundle (D-0024),
so the fuels slot is no longer empty. But WorldCover has no species, age or
density, and class 10 `tree_cover` is a Korean pine plantation and a riparian
broadleaf stand alike. Those are different fuels.

**What to do.** 임상도 (1:5,000, EPSG:5179, 51 species groups, diameter/age/
crown-density classes) is fully documented in
`reports/KOREAN_FUELS_CANDIDATES.md` and is `REGISTRATION_REQUIRED` — a free
account on `www.bigdata-forest.kr`, not a technical barrier. **Resolve its
licence contradiction first**: the product page states CC-BY *and* "all rights
reserved", so the effective redistribution licence is `UNKNOWN` and nothing
derived from it may be committed until that is settled, preferably by getting
the data from 산림청 directly.

**Done when.** A sourced `VegetationClassScheme` exists with the publisher's own
code dictionaries (not reconstructed from sample values), `임상나무높이`'s unit
is established rather than assumed, per-polygon interpretation vintage reaches
`valid_from`/`valid_to`, and the layer is ingested via `wg-data import-fuels`.
**Do not** add a Korean fuel scheme from memory, and do not crosswalk it to a
fire-behaviour fuel model here (`docs/SCOPE.md`, D-0024).

## 4. Aggregate population for the real study area

**Why.** `uljin_real_v1` has no population layer, so its road QA has no
settlement nodes and therefore reports no single-egress candidates or critical
links — the diagnostics are structurally unavailable without settlements.

**Done when.** Village-level (`ri`) aggregate counts from KOSIS/SGIS are
ingested with `count_basis` and `reference_date` recorded, the privacy guard
passes without being bypassed, and the real bundle's QA report gains settlement
nodes. Note that `POP-003` will likely fire for small hamlets: that is the
intended behaviour, not a problem to suppress.

## 5. Multi-tile DEM mosaicking

**Why.** `fetch_copernicus_dem` refuses an extent spanning more than one 1°
tile (F-BND-5), which limits study areas to roughly 100 km and forbids some
otherwise sensible extents.

**Done when.** Mosaicking works, the seam and the per-tile provenance are
recorded (each tile is a separate `SourceRecord`), and a test covers an extent
that deliberately straddles a tile boundary.

## 6. Turn two documented failure modes into measured quantities

- **F-RD-2 (egress counts as clip artifacts).** Add a reporting mode that
  rebuilds the QA at two or three study-area sizes and reports whether the
  egress count is stable. Done when a bundle can carry that sensitivity result.
- **F-RD-4 (transitive snapping).** Report the distribution of intra-cluster
  spans, not only the maximum. Done when the QA report carries it.

## 7. Close the three items the review round left open

The phase-1 audit and verification fixed 20 defects (see `COMPLETED.md`). Three
of their observations were deliberately **not** turned into code, and are
recorded here so they are not lost:

- **`terrain.reproject_raster` has never been cross-checked against
  `gdalwarp`.** Its missing-data behaviour is tested, but the warp's numerics
  are not compared with an independent implementation the way slope and aspect
  now are. Done when a synthetic surface reprojected by both agrees within a
  stated tolerance, or the disagreement is reported as a quantity.
- **The real-data bundle's reproducibility is untested**, because rebuilding it
  needs network access (D-0012) and the AWS tile list is mutable. Done when a
  network-marked test rebuilds `uljin_real_v1` and compares layer checksums
  against the committed manifest, reporting a difference as a finding about the
  *source* rather than a test failure.
- **The privacy guard is name-based only** (F-POP-1). Done when either a
  value-level heuristic exists with its false-positive rate stated, or a
  `DECISIONS.md` entry records that name-based is the deliberate ceiling.

## 8. Continuous integration

**Why.** The synthetic bundle build needs no network and is deterministic, so it
is a natural CI job; nothing currently runs the suite automatically.

**Done when.** CI runs `python -m pytest -q`, builds
`configs/uljin_valley_synthetic.yaml`, and runs
`wg-data validate-study-area --strict` against a bundle built from a config with
no deliberate warnings. Note the committed example bundle *does* carry
deliberate warnings, so `--strict` against it is expected to fail — use a clean
config for the strict gate, or gate on ERROR only.

---

## Known limitations carried forward, not bugs

These are decided behaviour. Do not "fix" them without a `DECISIONS.md` entry.

| Limitation | Where |
|---|---|
| Lines are connected where they share a *vertex*, not only an endpoint | D-0020 |
| Lines merely *crossing* with no shared vertex are still not connected | D-0007, F-RD-1 |
| Derivative edges and nodata neighbours are `nodata` | D-0005, F-TER-1/2 |
| Aspect is `NaN` on flat ground, never `0` | D-0006 |
| Aspect is from grid north, not true north | A-TER-5, F-TER-5 |
| The privacy guard is a name-based tripwire, not a boundary | D-0011, F-POP-1 |
| Small aggregate counts are flagged, not suppressed | D-0011, F-POP-2 |
| `single_egress_candidates` is component-level; read `critical_links` too | D-0008, F-RD-6 |
| Edge lengths are planar, not travel distances | A-RD-5, F-RD-7 |
| No data contract is exported to other repositories | D-0001 |
| Resampling within one CRS is refused rather than implicit | F-BND-6 |

## Do not do

- Do not integrate with any other WildfireGuardian repository yet (D-0001).
- Do not add prediction, routing, dispatch, or OSSE code (`docs/SCOPE.md`).
- Do not add a field, metric, or log line asserting that anything is safe or
  passable (`AGENTS.md` §5).
- Do not write a dataset name, agency, identifier, or licence term you have not
  verified (`AGENTS.md` §4).
