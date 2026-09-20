# Korean fuel / vegetation source candidates

Phase 2 item 6. Fuels is the highest-priority missing layer in this repository.
Every candidate below is documented on the nine axes item 6 requires: source,
spatial resolution, date vintage, classification scheme, units, coverage,
licence, provenance, and known limitations.

**Verified 2026-09-20.** Facts marked `UNKNOWN` were not verified. They are not
filled in from what is typical (`AGENTS.md` §3).

## The distinction this document turns on

`SOURCE_CLASS` vs `MODELED_CROSSWALK` (`fuels/classes.py`, `SchemeKind`):

- A **`SOURCE_CLASS`** scheme is the legend the publisher actually assigned.
  "This polygon is 소나무, 영급 4, 밀도 상" is a datum.
- A **`MODELED_CROSSWALK`** maps those classes onto a fire-behaviour fuel model
  (Anderson 13, Scott & Burgan 40, or a Korean equivalent). That is a *model*,
  and its right answer depends on the analysis.

**No candidate below is a fuel model.** All are land cover or forest stand
attributes. This repository ships no crosswalk and computes none (D-0024). A
consumer that needs one must supply it, declare it `MODELED_CROSSWALK`, and
carry its own provenance.

---

## Candidate 1 — 임상도 (Forest Type Map) 1:5,000 — **recommended**

The authoritative Korean forest stand map. Substantially richer than any global
product: it carries species, age, diameter and crown density, which is most of
what a fuel-model crosswalk actually needs.

| Axis | Value |
|---|---|
| **Source** | 산림빅데이터거래소 (Forest Big Data Exchange), `www.bigdata-forest.kr`, product `FRT001003`, "임상도(1:5,000)_시군구". Seller of record ㈜시선아이티 (SEESUN IT). Underlying producer: 산림청 / Korea Forest Service. |
| **Spatial resolution** | 1:5,000 map scale. **Vector polygon**, not a raster — so there is no native cell size, and any grid the consumer wants is a rasterisation *it* chose. Minimum mapping unit `UNKNOWN`. |
| **Date vintage** | 2022 reference year (`2022년 기준`). Product registered 2019-11-22, last modified 2023-12-20. Distribution cycle: annual (`데이터 배포 주기: 년`) → `TemporalProvenance.ANNUAL`. |
| **Classification scheme** | Attribute columns, all coded: `입목존재코드` (stocked/unstocked), `임종코드` (artificial vs natural forest), `임상코드` (conifer / broadleaf / mixed / bamboo), `수종그룹코드` (**51 species groups**, incl. 소나무 *Pinus densiflora*, 잣나무 *P. koraiensis*, 리기다소나무 *P. rigida*), `경급코드` (diameter class), `영급코드` (age class), `밀도코드` (crown density), `임상나무높이` (stand height), `지형지물표준코드`, `맵라벨코드`, `기타특이사항내용`. Code→label dictionaries were **not** obtained and are `UNKNOWN`; they must come from the publisher, not be reconstructed from sample values. |
| **Units** | All of the above are **codes**, not measured quantities. `임상나무높이` is the one apparently-physical field and its unit is **`UNKNOWN`** — metres is plausible and is exactly the kind of guess §3 forbids. |
| **Coverage** | Nationwide, distributed as one archive per 시군구 (county). Uljin-gun (울진군) is therefore a single file. |
| **CRS** | **EPSG:5179** (단일평면직각좌표계 UTM-K, false origin 100000/200000) — stated explicitly by the provider, not inferred from coordinate ranges. Note this repository's axis-order finding: EPSG:5179's authority axis order is (northing, easting). |
| **Format** | Shapefile (`.shp/.shx/.dbf/.prj`) in a zip, 52.28 MB, polygons. |
| **Licence** | Stated as **Creative Commons Attribution**, access level `public`, base fee ₩0, usage period 20191214–99991231. **Conflict recorded, not resolved:** the same page also carries "Copyright 2019. SEESUNIT CO., LTD. all rights reserved", which contradicts CC-BY. The effective redistribution licence is **`UNKNOWN`** until the publisher confirms which governs. Do not commit the data or a derivative to this repository until it is. |
| **Provenance** | Produced by interpreting digital aerial photography supplied by 국토지리정보원 (NGII), followed by field verification (`현지대조`), then stand delineation and attribute assignment. Described by the publisher as one of the major national thematic maps alongside topographic, soil and geological maps. |
| **Access status** | `REGISTRATION_REQUIRED` — browsable anonymously, download needs a free account. See `reports/SOURCE_ACCESS_STATUS.md`. |

### Known limitations

1. **Mixed interpretation vintage inside a single file.** Sample rows carry
   `기타특이사항내용 = "2017영상판독"` (2017 image interpretation) alongside rows
   with no such note. So a "2022" file contains polygons interpreted in
   different years, and the per-polygon year is only sometimes recorded. This is
   the same mixed-provenance hazard already documented for Copernicus GLO-30,
   and it must reach `valid_from`/`valid_to` per-feature rather than being
   flattened to one date for the layer.
2. **The recorded quality measurement does not cover this edition.** Quality
   testing is logged as 20191115–20191130 by 시선아이티, result 정상, scope
   "코드" (codes). That is the 2019 edition; the data is 2022-reference and was
   modified 2023-12-20. There is **no** quality record for the current edition.
3. **Licence contradiction**, above.
4. **Code dictionaries not obtained**, above. Sample rows show
   `입목존재/임종/임상/수종그룹` all `0` with `경급` 91/93 and map labels
   J00091/J00093, i.e. non-forest polygons — but we cannot confirm that reading
   without the dictionary, so it is not asserted.
5. **Pre-fire for Uljin.** A 2022-reference edition may or may not postdate the
   March 2022 Uljin fire. Which, is `UNKNOWN`. Until established, it must not be
   used as post-fire state, and equally must not be assumed pre-fire.
6. **Reseller in the chain.** The path is Korea Forest Service → ㈜시선아이티 →
   exchange. The extraction/processing step (`시군구별 추출/가공`) is the
   reseller's, and is not documented in detail.

---

## Candidate 2 — ESA WorldCover 10 m 2021 v200 — **implemented**

Wired in as `fuels.source.kind: esa_worldcover` (D-0024). Not the best Korean
option; it is the best *unblocked* one.

| Axis | Value |
|---|---|
| **Source** | ESA WorldCover, `esa-worldcover.s3.eu-central-1.amazonaws.com`, v200, 2021 |
| **Spatial resolution** | 10 m, native raster |
| **Date vintage** | 2021, annual |
| **Classification scheme** | 11 classes, `SchemeKind.SOURCE_CLASS`, codes 10–100, nodata 0 |
| **Units** | class codes (categorical; must never be resampled by averaging) |
| **Coverage** | global, 3° tiles |
| **Licence** | CC-BY 4.0 — unambiguous, which is why this one is committed-safe |
| **Provenance** | Sentinel-1 + Sentinel-2, ESA WorldCover consortium |

### Known limitations

1. **Mountain shadows are sometimes classified as water** (product user manual
   §4) — directly consequential in steep Korean valleys, which is the terrain
   this repository targets.
2. **No species, age, or density.** Class 10 `tree_cover` covers a Korean pine
   plantation and a riparian broadleaf stand alike; those are different fuels.
   This is the gap 임상도 fills.
3. **2021 vintage predates the March 2022 Uljin fire**, so for that event it is
   pre-fire land cover and must not be read as post-fire state.
4. 10 m cells against a 30 m DEM: co-registration is by nearest-neighbour onto
   the DEM grid, which **downsamples by class selection, not by majority vote**.
   A 30 m cell takes one 10 m class, not the modal one. Defensible (it never
   invents a class) but it is a choice, and it is recorded here.

---

## Candidate 3 — 산림청 forest data at source

`www.forest.go.kr`, `map.forest.go.kr`, `nifos.forest.go.kr`:
`PROXY_FAILURE` from this container. Would plausibly serve 임상도 and related
products directly, without the reseller in the chain, and with the publisher's
own licence statement — which would resolve Candidate 1's licence conflict.
**Re-probe from a normal network before using the exchange copy.**

Every other axis is `UNKNOWN`: nothing about this source was verified.

---

## Candidate 4 — 환경부 토지피복지도 (Ministry of Environment land-cover map)

A lead, not an assessment. **Every axis is `UNKNOWN`** and deliberately stays
that way.

Where the name came from: the main WildfireGuardian repository's
`data_io/raster.py` has a `load_landcover(source="me_korea")` branch that raises
`NotImplementedError` with the message *"ME 토지피복 ingestion is a Session 3
task"*. So the only verified fact is that **another repository intends to use a
Ministry of Environment land-cover product**. That is not evidence of its
spatial resolution, vintage, classification scheme, coverage, licence or
provenance, and this document will not supply any of those from memory
(`AGENTS.md` §3, §4).

| Axis | Value |
|---|---|
| **Source** | named as "ME 토지피복" / 환경부 토지피복지도. Publisher, portal and dataset identifier all `UNKNOWN` |
| **Spatial resolution** | `UNKNOWN` |
| **Date vintage** | `UNKNOWN` |
| **Classification scheme** | `UNKNOWN`. Korean land-cover maps are published at more than one classification level; which one, and its code dictionary, is not established here |
| **Units** | `UNKNOWN` |
| **Coverage** | `UNKNOWN` |
| **Licence** | `UNKNOWN` |
| **Provenance** | `UNKNOWN` |
| **Access status** | `UNKNOWN` — not probed. Likely routes are `www.nsdi.go.kr` and `egis.me.go.kr`, and NSDI is `PROXY_FAILURE` from this container |

### Known limitations

1. Everything above is `UNKNOWN`, which is itself the finding: this is a name,
   not a dataset.
2. Worth checking before 임상도 is preferred over it. A Ministry of Environment
   land-cover map and a Korea Forest Service stand map answer different
   questions — land cover versus forest composition — and a fuels consumer
   probably wants both.

---

## Rejected as fuel sources

- **산불발생위치도 (wildfire occurrence location map)** — free on the exchange.
  This is fire *occurrence*, i.e. an outcome, and it is retrospective. Using it
  as a landscape input would be leakage. If ever ingested it is
  `DataClass.RETROSPECTIVE` and not planner-legal (D-0023).
- **`fire.kofpi.or.kr` open APIs (6 endpoints)** — part of an
  "AI-based wildfire prevention decision support system". Its products include
  `입산통제구역` (entry-control zones) and `등산로폐쇄구간` (closed trail
  sections). Those are **downstream modelling conclusions**, and ingesting them
  as data would import someone else's model output while also asserting the kind
  of access/safety claim `AGENTS.md` §5 forbids this repository from making.
  Out of scope. Its `산불대피소` endpoint is noted in the facilities work as a
  *facility existence* lead only — a shelter's designation is a fact about the
  authority's designation, never a finding that the shelter is a viable refuge.

## Recommendation

1. Register on `www.bigdata-forest.kr`, download `FRT001003` for 울진군, and
   ingest via `wg-data import-fuels` with the licence question answered first.
1b. Probe 환경부 토지피복지도 (Candidate 4) from a normal network and fill in its
   nine axes. It may be a better fit than either shipped option, or it may not;
   nobody here knows.
2. Until then, `uljin_real_v1` carries ESA WorldCover, labelled as land cover.
3. Do **not** ship a crosswalk from either to a fire-behaviour fuel model. That
   is `wildfireguardian-fuels`, not this repository (`docs/SCOPE.md`).
