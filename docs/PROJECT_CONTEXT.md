# PROJECT_CONTEXT

**Repository:** `wildfireguardian-data`
**Role in the WildfireGuardian project:** independent geospatial *data foundation* only.
**Status:** Phase 2 complete, frozen at `v0.2.0` — see
`reports/PHASE2_INTEGRATION_READINESS.md` for what may be depended on,
`tasks/COMPLETED.md` for what was built and what turned out to be wrong, and
`tasks/CURRENT.md` for what is left.

## What WildfireGuardian is

WildfireGuardian is a research project studying **wildfire evacuation and
assisted-rescue decisions for mobility-limited residents in Korean rural
communities**. The wider project asks three questions:

1. How accurate and timely must a wildfire forecast be before acting on it
   improves protective decisions over a strong trigger/buffer policy?
2. For residents unable to self-evacuate, what is the latest time a responder
   can be dispatched and still complete ingress → pickup → egress?
3. Longer term, which sensing, responder-staging, refuge, communications, or
   egress interventions increase robust protectability?

## What this repository is responsible for

**Trustworthy landscape and infrastructure inputs.** Nothing else.

This repository turns raw Korean (and, for testing, synthetic) geospatial data
into **reproducible, provenance-preserving, quality-controlled study-area
packages**, so that any downstream analysis is arguing about *models*, not about
whether the terrain was silently resampled or the road network silently
reprojected.

## What this repository is explicitly NOT responsible for

This repository does **not** contain, and must not grow:

- wildfire spread prediction or forecasting;
- forecast-versus-trigger comparison;
- evacuation routing or rescue routing;
- dispatch-time or latest-safe-departure computation;
- OSSE (observing-system simulation experiment) machinery;
- decision policies, utilities, or cost functions;
- any claim that a road, a shelter, or a person is *safe*.

See `SCOPE.md` for the boundary rules and `INTERFACES.md` for why no schema is
exported to other repositories yet.

## Operating principle

The repository is usable **entirely by itself**. It has no dependency on any
other WildfireGuardian repository, and it does not import from, write to, or
assume the existence of one.

## Honesty rules that outrank convenience

- If a fact is not known, the artifact says `UNKNOWN`. It does not guess.
- Missing data stays missing. It is never silently filled with `0`.
- Coordinate reference systems are never silently unified. Mismatch is an error.
- Units are carried explicitly on every quantity that has one.
- Synthetic data is labelled `synthetic` everywhere it appears, including in the
  provenance record of every artifact derived from it.
