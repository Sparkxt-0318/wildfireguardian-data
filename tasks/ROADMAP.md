# ROADMAP

Phases for this repository only. Everything downstream — prediction, forecast
comparison, routing, dispatch, OSSE — belongs elsewhere (`docs/SCOPE.md`) and
does not appear here at any phase.

## Phase 1 — Foundation ✅ complete

Installable package, working CLI, provenance system, CRS validation, terrain
preprocessing, road QA, synthetic fixtures, example bundles, automated tests,
documented failure modes. See `COMPLETED.md`.

## Phase 2 — Authoritative Korean sources (next)

The gap that matters most: the real bundle currently rests on two global open
datasets, and the authoritative Korean sources were not reachable from this
repository's development environment (`docs/DATA_PROVENANCE.md` §Access
attempts — note that those failures were outbound-proxy tunnel closures, **not**
evidence the services are unavailable).

1. **NGII / VWorld road and terrain data.** Replace OSM as the primary road
   source, or at minimum cross-check against it. NGII also publishes a DTM,
   which would remove the DSM-versus-DTM problem (`FAILURE_MODES.md` F-TER-3).
2. **Korean vegetation / forest-type data** (Korea Forest Service is the
   likeliest source). Requires a *sourced* `FuelClassScheme`; inventing a
   crosswalk is forbidden (A-FU-1).
3. **Aggregate population** from KOSIS / SGIS at village (`ri`) level, with the
   `count_basis` recorded (register vs census vs present population).
4. **Facilities** — shelters, fire stations, responder bases — from a named
   Korean source, with roles mapped explicitly (A-FAC-2).
5. Two or three **well-audited** Korean study areas. Deliberately a small
   number: `docs/PROJECT_CONTEXT.md` prefers a few audited examples over many
   questionable ones.

## Phase 3 — Deeper QA

1. **Multi-tile DEM mosaicking**, with the seam recorded in provenance
   (currently refused outright — F-BND-5).
2. **Independent cross-checks**: compare slope against an alternative estimator
   and against a different DEM product, and report the disagreement as a
   quantity rather than choosing a winner.
3. **Terrain-void characterisation**: distinguish water, radar shadow and
   genuine no-data in a DEM, since they have different consequences.
4. **Road-network completeness estimation** against an independent source, so
   F-RD-3 becomes a measured quantity rather than an `UNKNOWN`.
5. **Sensitivity reporting**: how egress counts change with the study-area
   extent and the snap tolerance, which turns F-RD-2 and F-RD-4 into numbers.

## Phase 4 — Interface negotiation

Only when a second WildfireGuardian repository exists. That repository states
its required fields and semantics in writing; a new `DECISIONS.md` entry
supersedes D-0001; a versioned export module is added, leaving the internal
model free to diverge. Not before — see `docs/INTERFACES.md`.

## Non-goals, permanently

Prediction, forecast skill, routing, dispatch timing, OSSE simulation, safety
adjudication, and any person-level or medical data. These are not "later
phases"; they are other repositories' work, or in the last case, nobody's.
