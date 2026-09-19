# SCOPE

## In scope

| Area | In scope | Module |
|---|---|---|
| Terrain | DEM ingestion, reprojection, clipping, slope, aspect, terrain statistics | `terrain/` |
| Roads | Graph ingestion, topology QA, connectivity and chokepoint *diagnostics* | `roads/` |
| Fuels / vegetation | Generic ingestion architecture, class schemes, synthetic fixtures | `fuels/` |
| Population | Aggregate population, age strata, village geometry, settlement centroid | `population/` |
| Facilities | Generic loaders for shelters, refuge candidates, responder bases, fire stations | `facilities/` |
| Study area | Internal bundle assembly, serialisation, summary | `study_area/` |
| Provenance | Source, dates, transformations, CRS, resolution, checksum, temporal class | `provenance/` |
| Validation | CRS, units, missing-data, alignment, bundle-integrity checks | `validation/` |
| CLI | `build-study-area`, `validate-study-area`, `summarize-study-area` | `cli/` |

## Out of scope (hard boundary)

The following are **not** implemented here, and a pull request adding them
should be rejected as out of scope regardless of quality:

- **Wildfire prediction / spread modelling.** No rate-of-spread, no fire
  behaviour fuel models, no weather coupling, no ignition modelling.
- **Forecast comparison.** No forecast skill scoring, no trigger/buffer policy
  evaluation.
- **Evacuation routing.** No shortest paths, no travel times, no capacity
  modelling, no departure scheduling.
- **Rescue routing.** No ingress → pickup → egress computation, no dispatch
  deadlines.
- **OSSE simulation.** No synthetic-observation generation for observing-system
  experiments. (The *synthetic fixtures* here are test inputs, not simulated
  observations of a modelled truth — see `GLOSSARY.md`.)
- **Safety adjudication.** No layer, field, or function asserts that a road,
  shelter, refuge, or route is safe, passable, or usable.
- **Person-level data.** No household-level, individual-level, or medical
  information. See `ASSUMPTIONS.md` §Population.

## Boundary cases and how they are resolved

| Question | Resolution |
|---|---|
| Is slope in scope, since it is a fire-behaviour input? | **Yes.** Slope is a property of the landscape and is computed from the DEM alone. It becomes out of scope the moment it is combined with fuel and wind to produce a spread rate. |
| Are road graph connectivity metrics routing? | **No.** Component counts, degrees, bridges, and articulation points are topology QA of the *input data*. Computing a route, a travel time, or an evacuation time is routing and is out of scope. |
| Is "single-egress community" a safety claim? | **No.** It is a topological statement: this component of the road graph touches the study-area boundary at exactly one node. It says nothing about whether that egress is usable. The field is named `single_egress_candidates`, and "candidate" is load-bearing. |
| Is a chokepoint diagnostic routing? | **No, as implemented.** It reports edges whose removal disconnects a settlement node from every boundary exit node in the graph. That is a cut property of the graph, reported as a diagnostic, with no cost, time, or safety interpretation. |
| May this repository host a Korean fuel-type crosswalk? | Only as a *declared, sourced* class scheme. Inventing a crosswalk that no published source supports is forbidden — see `ASSUMPTIONS.md` §Fuels. |
| May this repository publish a schema for downstream repositories? | **Not yet.** See `INTERFACES.md`. |

## Scope test for any new feature

A feature belongs here if and only if it can be described as:

> "making an input layer's spatial, temporal, unit, missing-data, or topological
> properties more accurately known."

If its description requires the words *predict*, *route*, *dispatch*, *decide*,
*optimise*, or *safe*, it belongs in a different repository.
