# experiments/

Scratch analyses that are **not** part of the package and are not imported by
it. Anything here that becomes load-bearing moves into `src/` with tests, and
anything here that informed a choice gets an entry in `docs/DECISIONS.md`.

Rules that still apply (`AGENTS.md`): no invented data, no safety claims, and
`UNKNOWN` rather than a guess. An experiment that fetches real data documents
the attempt in `docs/DATA_PROVENANCE.md` whether it succeeded or not.

Empty at the end of Phase 1: the questions that arose during it were answered by
tests (`tests/`) rather than by scratch analysis, which is where they belong
when the expected answer is known analytically.
