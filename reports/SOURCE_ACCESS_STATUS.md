# Source access status

**Probed 2026-09-20** from this repository's development container, through its
outbound agent proxy. Phase 2 item 18.

This report exists because "we could not get the data" is three or four very
different findings, and collapsing them loses the only information a future
agent needs. A dataset behind a free registration form is a morning's work. A
dataset whose host is refused by *this container's* proxy is not a fact about
the dataset at all.

## The one rule this report enforces

> **A network failure is never evidence that a service or dataset does not
> exist.** (Phase 2 item 2.)

Accordingly **no source below is marked `NOT_FOUND`**, and none is marked
`UNAVAILABLE` — a status this taxonomy deliberately does not contain, because
it is the word that hides all of the distinctions the rest of the taxonomy
makes.

## Status taxonomy

| Status | Meaning | What a future agent should do |
|---|---|---|
| `RETRIEVED` | Data was actually read into this repository. | Nothing. |
| `CREDENTIAL_REQUIRED` | Reachable; needs a key/token we do not hold. | Obtain a key. |
| `REGISTRATION_REQUIRED` | Reachable; needs a free account, no payment. | Register. |
| `MANUAL_DOWNLOAD_REQUIRED` | Reachable; no machine endpoint, a human must click. | Download, then `wg-data import-*`. |
| `ENDPOINT_MIGRATED` | The service exists; the URL we tried is not its current one. | Find the current endpoint. |
| `TEMPORARY_NETWORK_FAILURE` | Transient failure; a retry may succeed. | Retry. |
| `PROXY_FAILURE` | **This environment's** egress refused the connection. | **Re-probe from a normal network.** Says nothing about the source. |
| `NOT_FOUND` | Positively established not to exist. | — (unused; see above) |
| `UNKNOWN` | Not yet probed, or probed inconclusively. | Probe. |

## Evidence for the `PROXY_FAILURE` classification

Every host so marked returned `curl` exit 35 (`Recv failure: Connection reset
by peer`) or 56, and the proxy's own diagnostic endpoint
(`$HTTPS_PROXY/__agentproxy/status`) independently attributes the same hosts to
`ws_closed_mid_exchange` — for example, for `kosis.kr:443`:

```
tunnel closed (code 1006, Connection ended) after 8s;
517 B sent, 39 B received, client reading, 0 B still queued in the relay
```

Bytes moved in **both** directions before the tunnel died, so the remote host
was answering. The failure is in the relay, not at the origin.

**It is host-specific, not a pattern we can generalise.** `www.uljin.go.kr`
(200) and `www.bigdata-forest.kr` (200) succeed while `kosis.kr` (000) and
`www.kma.go.kr` (000) fail, so this is neither "the `.go.kr` TLD is blocked"
nor "Korean hosts are blocked". Any theory beyond "these specific hosts fail
here" would be a guess, and is not recorded as one.

## Status by source

### Retrieved

| Source | Endpoint | Status | Used for |
|---|---|---|---|
| Copernicus DEM GLO-30 | `copernicus-dem-30m.s3.amazonaws.com` | `RETRIEVED` | terrain (`uljin_real_v1`) |
| ESA WorldCover 10 m 2021 v200 | `esa-worldcover.s3.eu-central-1.amazonaws.com` | `RETRIEVED` | land cover — see D-0024 |
| OpenStreetMap API 0.6 | `api.openstreetmap.org` | `RETRIEVED` | roads (`uljin_real_v1`) |

All three re-verified HTTP 200 on 2026-09-20.

### Reachable, but gated

| Source | Endpoint | Status | Note |
|---|---|---|---|
| 산림빅데이터거래소 (Forest Big Data Exchange) | `www.bigdata-forest.kr` | `REGISTRATION_REQUIRED` | **Browsable without an account; download needs one.** Carries 임상도 (forest type map) — see `reports/KOREAN_FUELS_CANDIDATES.md`. The single most valuable source found in Phase 2. |
| 공공데이터포털 (Korea Open Data Portal), portal | `www.data.go.kr` | `CREDENTIAL_REQUIRED` | Portal browsable (200). Datasets need a per-dataset key application. |
| 공공데이터포털, API host | `apis.data.go.kr` | `PROXY_FAILURE` | So even *with* a key, nothing could be tested from this container. Both statuses are true of different hosts and both are recorded. |
| AI Hub | `www.aihub.or.kr` | `REGISTRATION_REQUIRED` | 200. Not investigated for relevant layers. |
| 울진군 (Uljin County) | `www.uljin.go.kr` | `MANUAL_DOWNLOAD_REQUIRED` | 200. A lead for local facility/shelter lists; not yet examined. |

### Proxy-blocked from this container — re-probe elsewhere

Every one of these is a **plausible or likely authoritative source** for a layer
this repository is still missing. None of them has been shown to be unsuitable.

| Source | Endpoint | Would provide |
|---|---|---|
| 국토지리정보원 / NGII | `map.ngii.go.kr`, `www.ngii.go.kr` | authoritative Korean roads, terrain, base mapping |
| VWorld (NGII open API) | `www.vworld.kr` | authoritative roads and administrative boundaries |
| 국가공간정보포털 / NSDI | `www.nsdi.go.kr`, `openapi.nsdi.go.kr` | national spatial data (CONNECT tunnel failed, 502) |
| 산림청 / Korea Forest Service | `www.forest.go.kr`, `map.forest.go.kr`, `nifos.forest.go.kr` | 임상도 at source, forest road network |
| 통계청 KOSIS | `kosis.kr` | aggregate population by administrative unit |
| SGIS (Statistical Geographic Information Service) | `sgis.kostat.go.kr` | 집계구-level aggregate population + boundaries |
| 소방청 / National Fire Agency | `www.nfa.go.kr` | fire station locations |
| 생활안전지도 / SafeMap | `www.safemap.go.kr` | designated shelters, safety facilities |
| 도로명주소 / juso | `www.juso.go.kr`, `business.juso.go.kr` | address and 사물주소 facility points |
| 기상청 / KMA | `www.kma.go.kr` | weather (out of scope here; recorded for completeness) |
| Overpass API | `overpass-api.de` | OSM without the main API's bbox size limit |
| Geofabrik | `download.geofabrik.de` | whole-country OSM extract |

### Endpoint status corrected from Phase 1

Phase 1 recorded `map.ngii.go.kr` as HTTP 400 and inferred "the endpoint probed
was wrong". Today the same host is `PROXY_FAILURE` (000). The Phase 1
`ENDPOINT_MIGRATED`-style reading is therefore **not confirmed** and is
downgraded to `PROXY_FAILURE`: we no longer have evidence about which endpoint
is correct, only that we cannot reach the host.

## Credential handling (Phase 2 item 5)

- No credential value appears in any tracked file, and none is printed by this
  repository or by this report.
- **No `POTENTIALLY_EXPOSED_CREDENTIAL` finding.** Unlike the earlier check,
  this one is conclusive: the working clone now carries **full history** (10
  commits, no `.git/shallow`), and no credential-shaped path
  (`.env`, `*.pem`, `*.key`, `.netrc`, `*credential*`, `*secret*`, `*token*`)
  was ever added in any commit on any branch.
- `.gitignore` now ignores `.env`, `.env.*`, `*.pem`, `*.key` and `.netrc`. It
  previously did not. The ignore is in place **before** such a file exists,
  because a key committed once is committed forever.
- Keys, when a future agent obtains them, are read from the environment. This
  repository provides no mechanism for writing one to disk.
- This report does **not** state whether any Korean API key is present in this
  container's environment: inspecting the environment for credential-shaped
  variables was refused by this session's permission layer, so the answer is
  `UNKNOWN` rather than "none". Recorded as a gap, not as an absence.

## What this means for the bundles, stated plainly

- `uljin_real_v1` has terrain and roads from `RETRIEVED` sources, and can now
  gain an ESA WorldCover land-cover layer.
- It still has **no** population layer, **no** facilities layer and **no** fire
  stations, because every authoritative source for those is `PROXY_FAILURE` or
  `CREDENTIAL_REQUIRED` from here. Those layers are **absent**, not estimated.
- The best available Korean vegetation source, 임상도, is
  `REGISTRATION_REQUIRED` — a free account, not a technical barrier. It is the
  highest-value unblocked action left in Phase 2 and needs a human with a
  browser, not a better fetcher.
