# RESEARCH_QUESTION

## Core scientific question for this repository

> Can raw Korean geospatial data be transformed into reproducible,
> provenance-preserving, quality-controlled study-area packages **without
> introducing silent spatial, temporal, CRS, resolution, or missing-data
> errors**?

The operative word is **silent**. The repository is not claiming that
transformation is lossless — clipping, reprojection, and resampling all destroy
information. It claims that every such loss is *recorded, attributable, and
detectable by an independent reader*.

## Decomposition into testable sub-questions

| # | Sub-question | How this repository answers it |
|---|---|---|
| Q1 | Are derived terrain quantities correct? | Synthetic surfaces with analytically known slope/aspect (`tests/test_terrain_derivatives.py`); tolerance stated per test. |
| Q2 | Are CRS errors detectable rather than silent? | Every cross-layer operation goes through `crs.require_same_crs`; `tests/test_crs_safeguards.py` asserts that mixing EPSG:5179 with EPSG:4326 raises, never coerces. |
| Q3 | Is missing data preserved as missing? | `nodata` masks propagate through reprojection, clipping, derivatives, and statistics; `tests/test_missing_data.py` asserts no `0` substitution and no mean contamination. |
| Q4 | Is resolution change visible? | Resolution is part of `ProvenanceRecord`; any resample appends a `Transformation` naming the method and the before/after resolution. |
| Q5 | Is temporal meaning preserved? | Every layer carries a `TemporalProvenance` class (`static`, `annual`, `monthly`, `observation_time`, `retrospective`) and refuses to be built without one. |
| Q6 | Is the road network's connectivity characterised honestly? | Graph QA reports components, dead ends, articulation points, bridges, and single-egress candidates — and explicitly refuses to report "safe". |
| Q7 | Is a package reproducible? | `wg-data build-study-area` is a pure function of (config, inputs); the bundle manifest records input checksums, and re-running reproduces byte-identical arrays. |

## What would falsify the claim

The claim is falsified by any demonstration that a study-area bundle built by
this repository contains a quantity whose spatial, temporal, CRS, resolution, or
missing-data semantics differ from what its provenance record states. That is
the specific failure the test suite and `docs/VALIDATION.md` are designed to
catch.

## Non-questions

This repository does not ask, and must not attempt to answer, whether a
landscape is *dangerous*, whether a road is *passable*, or whether a shelter is
*safe*. Those are downstream modelling questions and require assumptions this
repository deliberately does not hold. See `docs/SCOPE.md`.
