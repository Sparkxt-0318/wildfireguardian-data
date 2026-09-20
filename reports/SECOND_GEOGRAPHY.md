# Does this schema only work on Uljin?

Phase 2 item 21. A pipeline validated on exactly one place has been validated
against its own assumptions.

**Answer: no, with two caveats and one honest surprise.** Every layer, check and
schema field behaved on a geography chosen to be as different as Korea allows,
and three code paths were exercised for the first time on real data.

## The criterion was fixed before any output was looked at

Item 21 says not to optimise the second area for an interesting result, so the
selection rule was written down first and applied mechanically:

1. **A different Korean belt CRS** — EPSG:5186 (Central Belt 2010, central
   meridian 127°E) rather than Uljin's EPSG:5187 (East Belt, 129°E). The CRS
   layer is then exercised, not re-run.
2. **Low relief** rather than a steep valley, exercising terrain derivatives at
   the opposite end of the slope range.
3. **The same 4 km box**, so differences are the geography and not the extent.

Four candidate boxes in the 5186 belt were screened on **relief only**:

| Candidate | Relief (m) | Mean elevation (m) |
|---|---|---|
| 126.60 E, 34.60 N | 490.8 | 144.2 |
| 126.70 E, 34.80 N | 567.9 | 66.3 |
| 126.90 E, 35.80 N | 126.0 | 14.7 |
| **126.75 E, 35.05 N** | **73.3** | **15.8** |

The lowest-relief box won. **Road topology was not looked at during
selection** — so whatever the road QA reports below is a finding about the
pipeline, not a choice.

## The place, identified from data rather than memory

`AGENTS.md` §4 forbids inventing Korean place names, so the box was identified
from OpenStreetMap's own tags for that extent, not from recall:

- `addr:province` = 전라남도 (Jeollanam-do)
- `addr:city` = 나주시 (Naju-si)
- `addr:subdistrict` = 오강리, 원곡리, 학산리, 장동리 (ri-level units)
- named features include 영산강 (the Yeongsan River), 호남선 (the Honam railway
  line) and 영산강 자전거길 (a riverside cycle route)

So: the Yeongsan river plain in Naju-si, Jeollanam-do, western Korea. Bundle
`naju_real_v1`, config `configs/naju_real_copernicus_osm_worldcover.yaml`.

## Side by side

| | `uljin_real_v1` | `naju_real_v1` |
|---|---|---|
| Province | 경상북도 (east coast) | 전라남도 (west, inland plain) |
| CRS | EPSG:5187 (East Belt) | **EPSG:5186 (Central Belt)** |
| DEM shape | 139 × 138 @ 30 m | 139 × 138 @ 30 m |
| Elevation range | 35.0 – 605.0 m (relief 570) | **3.0 – 54.9 m (relief 52)** |
| Mean elevation | 285.6 m | 12.7 m |
| Mean slope | 22.43° | **2.40°** |
| Median slope | 22.28° | 1.68° |
| Slope < 5° | 2.6% | **87.6%** |
| Slope > 30° | 21.2% | **0.0%** |
| Flat cells (aspect NaN) | 0 | **619** |
| Road edges | 68 | **1324** |
| Road length | 28.784 km | **183.942 km** |
| Road components | 2 | 7 |
| Largest component | 66 of 68 edges | 1306 of 1324 edges (98.7%) |
| Exits, largest component | **1** | 97 |
| Crossings without a node | **0** | 10 |
| Bridge / tunnel edges | 2 / 0 | 17 / 6 |
| Fuels | ABSENT | **ESA WorldCover 2021 v200** |

A factor of 19 in road length, 9 in mean slope, and 11 in relief. That is about
as much contrast as one country provides.

## Three code paths exercised for the first time on real data

1. **Flat-ground aspect.** Uljin has **zero** cells where the surface is flat.
   Naju has **619**, and every one of them is `NaN` in the aspect layer — never
   `0`, never `-1`. D-0006 says aspect is undefined on flat ground and must not
   be given a direction; until now that was only tested on a synthetic flat
   fixture. A consumer that reads aspect `0` as "north-facing" would have
   mis-signed 619 real cells here.
2. **A second belt CRS end to end.** EPSG:5186 fetches, reprojects, clips,
   derives, co-registers and round-trips. `CRS-004` correctly reports that
   5186's authority axis order is (Northing, Easting) while this package stores
   (Easting, Northing) — the same hazard as 5187, confirmed to be handled by
   CRS properties rather than by anything Uljin-specific.
3. **WorldCover ingestion on real ground.** The fuels slot is populated for the
   first time, warped onto the DEM grid with nearest-neighbour, all class codes
   validated against the published scheme:

   | Code | Class | Share |
   |---|---|---|
   | 40 | cropland | 46.8% |
   | 10 | tree cover | 22.2% |
   | 50 | built-up | 15.4% |
   | 80 | permanent water bodies | 8.1% |
   | 30 | grassland | 6.8% |
   | 60 | bare / sparse vegetation | 0.6% |
   | 90 | herbaceous wetland | 0.1% |

   A cropland-dominated river plain with a town and a river in it. Plausible,
   and stated as plausible rather than as verified ground truth: nobody has
   checked these classes against the ground, and WorldCover's own accuracy
   figures are the only evidence for them.

   Worth noting which WorldCover limitation does **not** bite here. The product
   manual records that mountain shadows are sometimes classified as water — a
   real hazard in Uljin's steep valleys, and nearly irrelevant on ground whose
   maximum slope is 19°. The 8.1% water is the Yeongsan River. The same
   limitation would need re-examining if WorldCover were used for Uljin.

## The honest surprise: Naju's unnoded crossings are real, and Uljin's were not

This is the reverse of the Phase 2 road audit's finding, and it is the strongest
evidence the diagnostics are measuring the world rather than the pipeline.

- **Uljin, after the shared-vertex fix (D-0020): 0 crossings without a node.**
  Its two apparent crossings turned out to be missing junction nodes, and
  noding at shared vertices connected them. Its 2 `bridge=yes` ways cross
  streams, not other roads.
- **Naju: 10 crossings without a node, and all 10 of 10 carry a `bridge`,
  `tunnel` or `layer` tag on at least one side.** Genuine grade separation — a
  plain with a railway line, a river and 17 bridges and 6 tunnels in 4 km². The
  diagnostic is doing its job: it flags them, and the tags it reports alongside
  let a reader tell grade separation from a data defect **without** the
  diagnostic itself having to guess (D-0007).

Same check, same code, opposite correct answers in two places.

## Caveat 1: 7 components is not re-litigated here

Naju's network is 7 components, but **98.7% of edges and 97.3% of length are in
one of them**. The other six total 4.95 km out of 184 km: one 10-edge
sub-network with its own 4 exits, and five fragments of 1–4 edges. That is a
different situation from Uljin's pre-fix 22 components, where the *largest*
component held a minority of the network.

This has **not** been audited to the depth of `reports/ULJIN_ROAD_AUDIT.md`, and
nothing here claims the six small components are real rather than artifacts. Two
candidate causes are untested: the study-area clip cutting service roads off
from their parents, and OSM ways that genuinely do not connect as digitised.
`RD-001` reports the count and says which, deliberately, it cannot distinguish.

## Caveat 2: this bundle is still missing three layers

Population, facilities and fire stations are **ABSENT** in Naju exactly as in
Uljin, and for the same reason: every authoritative Korean source for them is
`PROXY_FAILURE` or `CREDENTIAL_REQUIRED` from this environment
(`reports/SOURCE_ACCESS_STATUS.md`). Nothing was estimated to fill them.

So item 21's question — "is the schema Uljin-specific?" — is answered for
terrain, land cover, roads, CRS handling, the grid contract and the manifest. It
is **not** answered for population or facilities in either geography, because
neither has ever carried real data for those slots. The contract represents
their absence (`status: ABSENT` with a reason), which is the part that *has*
been tested.

## Verdict

The schema is not Uljin-specific. The evidence is that a geography differing by
an order of magnitude in relief, slope and road density built with **0 ERRORs**,
populated the same manifest, and produced findings that differ from Uljin's in
the directions the geography predicts — including one check returning the
opposite answer for a verifiable reason.
