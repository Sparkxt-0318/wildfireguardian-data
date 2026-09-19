# AGENTS.md — contract for every agent and contributor

This repository is a **scientific data foundation**. Its value is entirely in
being trustworthy. A plausible-looking wrong number here propagates silently
into every downstream WildfireGuardian analysis, where it is much harder to
detect. Read this file completely before editing anything.

## 1. Mandatory reading before editing code

You **must** read these, in this order, before you change a single line:

1. `docs/PROJECT_CONTEXT.md` — what this repository is and is not for
2. `docs/RESEARCH_QUESTION.md` — the claim the code has to support
3. `docs/SCOPE.md` — the hard boundary; what to refuse to build
4. `docs/ASSUMPTIONS.md` — every scientific assumption, and where it is enforced
5. `docs/DECISIONS.md` — why things are the way they are; append-only
6. `tasks/CURRENT.md` — what is in flight right now

Also read, before touching the corresponding area:

- `docs/DATA_PROVENANCE.md` — before adding or changing a data source
- `docs/VALIDATION.md` — before adding or changing a check
- `docs/FAILURE_MODES.md` — before deciding a behaviour is a bug
- `docs/INTERFACES.md` — before changing anything a caller can see
- `docs/GLOSSARY.md` — before naming anything

If you cannot read them, stop and say so. Do not infer their contents.

## 2. Never silently change

You may not change any of the following without an explicit, reviewed
`docs/DECISIONS.md` entry **and** a test that fails before the change and passes
after it:

- **Scientific assumptions** — anything in `docs/ASSUMPTIONS.md`.
- **Units** — the unit of any quantity, its default, or its conversion factors.
- **CRS semantics** — what counts as "the same CRS", when reprojection happens,
  what happens on mismatch, the meaning of a `None` CRS.
- **Time semantics** — temporal classes, timezone handling, the distinction
  between `source_date` and `acquisition_date`, or between `retrospective` and
  `observation_time`.
- **Schemas** — `ProvenanceRecord`, `StudyAreaBundle`, the bundle manifest, or
  any CLI `--json` top-level key. Bump `schema_version` when you do.
- **Missing-data semantics** — what `nodata`, `None`, `NaN`, and `0` mean, and
  how they propagate.
- **Validation severity** — promoting an ERROR to a WARNING, or removing a
  check, is a scientific change, not a cleanup.

"Silently" includes: doing it in a commit whose message describes something
else; doing it while "fixing a test"; and doing it because a test was failing
and the new behaviour made it pass.

## 3. UNKNOWN, not a guess

If you do not know a fact, write the literal string `UNKNOWN`.

Do **not**: infer a source date from a filename; assume a CRS from a coordinate
range; assume metres because the numbers look like metres; fill a vertical datum
from what is typical; estimate a facility capacity from a building footprint;
reconstruct a missing population figure from a neighbouring village.

`UNKNOWN` is counted and reported by `wg-data validate-study-area` as a WARNING.
An honest gap is a finding. A fabricated value is a defect.

## 4. Do not invent data

- Do **not** invent Korean datasets, agency names, dataset identifiers, URLs, or
  fuel-model crosswalks. If you did not verify it, it does not go in
  `docs/DATA_PROVENANCE.md`.
- If a real source is unavailable, record the attempt and the failure in
  `docs/DATA_PROVENANCE.md` §Access attempts, then proceed with **explicitly
  labelled synthetic** fixtures.
- Synthetic data must be labelled `DataClass.SYNTHETIC` in provenance, and must
  not be named after a real place without `synthetic` in the identifier.

## 5. Never claim safety

No function, field, attribute, log line, or docstring in this repository may
assert that a road, shelter, refuge, route, area, or person is *safe*,
*passable*, *usable*, *trapped*, or *at risk*. Those are downstream modelling
conclusions. Report topology, geometry, and provenance; stop there.

## 6. Stay in scope

Do not add wildfire prediction, forecast comparison, evacuation routing, rescue
routing, dispatch timing, or OSSE machinery. Do not add an import of, or a
write to, another WildfireGuardian repository. If a task appears to require one
of these, it belongs elsewhere — say so rather than building it.

## 7. Working rules

- **Fail loudly.** Prefer raising a specific exception from
  `wildfireguardian_data.errors` over a warning, and a warning over a silent
  default. Never `except Exception: pass`.
- **Carry units and CRS on the data, not in your head.** If a function takes a
  bare NumPy array plus a number, it is probably wrong; it should take a
  `RasterLayer`.
- **Provenance is not optional.** Any function producing a new layer must append
  a `Transformation` recording what it did and with what parameters.
- **Test analytically where possible.** The strongest tests in this repository
  compare against a closed-form answer on a synthetic geometry (a tilted plane's
  slope, a two-exit network's component count). Prefer those over
  regression-snapshot tests, and never write a test that merely asserts the
  current output.
- **Determinism.** Seed any randomness and record the seed.
- **No network in tests.** Anything requiring network access is marked
  `@pytest.mark.network` and is excluded from the default run.

## 8. Before you finish a task

1. `python -m pytest -q` passes (and you state how many tests ran).
2. `wg-data validate-study-area data/study_areas/<bundle>` reports no ERROR for
   every committed bundle.
3. New assumptions are in `docs/ASSUMPTIONS.md`; new choices are in
   `docs/DECISIONS.md`; new hazards are in `docs/FAILURE_MODES.md`.
4. `tasks/CURRENT.md` reflects reality, and finished work has moved to
   `tasks/COMPLETED.md`.
5. If you discovered that something is not known, it says `UNKNOWN` — in the
   artifact, not only in the commit message.

## 9. Multi-agent role split

When more than one agent works here, split by role, not by file:

- **Agent A — Scientific / Data Auditor.** Audits CRS assumptions,
  raster/vector alignment, temporal provenance, missing-data semantics, source
  reliability, and unit conversions. Writes findings, not production code.
- **Agent B — Implementation Engineer.** Builds the package, CLI, fixtures,
  tests, and pipeline.
- **Agent C — Verification Agent.** Independently tests outputs using synthetic
  geometries with analytically known answers. **Does not trust Agent B's tests
  merely because they pass** — a passing suite that encodes the implementation's
  own mistake is the failure mode this role exists to catch.

If you are working alone, perform the three roles sequentially and in that
spirit: audit the assumptions, build, then attack your own output as if someone
else had written it.
