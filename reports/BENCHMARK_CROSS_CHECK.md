# Cross-check against `wildfireguardian-benchmarks`

Phase 2 item 32: cross-check with the benchmark repository where practical, and
**do not duplicate the benchmark library**. Read-only against
`Sparkxt-0318/wildfireguardian-benchmarks`. Nothing there was modified, and this
repository imports nothing from it.

**These eight cases had never been run against this repository.** That is the
finding that made this worth doing: both repositories independently wrote
analytic terrain and graph cases, and nobody had checked whether the answers
agreed.

## Scope: 8 of 21 benchmarks are this repository's business

`WG-BM-001..008` are terrain (A1–A4) and graph connectivity (B1–B4). The other
thirteen — fire spread, observations, dispatch, traffic, forecast value — are
out of scope here (`docs/SCOPE.md`) and were not run.

## Result

| Case | Title | Result |
|---|---|---|
| WG-BM-001 | Flat plane has zero slope and undefined aspect | **PASS** (all assertions) |
| WG-BM-002 | Tilted plane reproduces the analytic slope and aspect | PASS except float32 storage |
| WG-BM-003 | Ridge crest: directional transition and zero-slope artefact | PASS except float32 storage |
| WG-BM-004 | Missing terrain stays missing, does not become zero | PASS except float32 storage |
| WG-BM-005 | Single-exit village has exactly one egress route | **PASS** (checkable parts) |
| WG-BM-006 | Two independent exits: egress is redundant | **PASS** (checkable parts) |
| WG-BM-007 | A shelter that exists but is unreachable by road | Representational difference — see below |
| WG-BM-008 | One-way roads are not traversable in reverse | Out of scope by design — see below |

Terrain: **47 of 52** assertions matched. Graph: **19 of 21** checkable
assertions matched. Every mismatch is explained below, and **none is a defect in
either repository's logic**.

## The terrain mismatches are float32 storage, and that is measured

All five terrain mismatches are the same number:

| | Value |
|---|---|
| Benchmark expects (WG-BM-002 slope) | `8.049466975528397` |
| This repository reports | `8.049467086791992` |
| Difference | **1.113 × 10⁻⁷ degrees** |
| Benchmark tolerance | `1 × 10⁻⁹` |

Horn's kernel was evaluated by hand in float64 on the same 5×5 plane:

```
float64 Horn slope : 8.049466975528397
benchmark expected : 8.049466975528397
difference         : 0.000e+00
```

**Bit-for-bit identical.** This repository's derivative arithmetic is already
float64 (`terrain/derivatives.py` computes in `np.float64` throughout); the only
float32 is the **final storage cast**. So the disagreement is entirely a storage
precision choice and contains no arithmetic error.

### Why this repository is not changing to float64 storage

The tempting move is to store float64 and make five failures disappear. That
would be wrong:

- **The input DEM is float32.** Copernicus GLO-30 tiles are float32. A float64
  slope derived from float32 elevations advertises precision the source does not
  have — the same category of error as recording a year-precision date as a day.
- **1.1 × 10⁻⁷ degrees is physically meaningless.** The real uncertainty in a
  GLO-30-derived slope is dominated by the DEM being a *surface* model (F-TER-3:
  a 20 m canopy step across one 30 m cell fabricates ~34°), which is eight
  orders of magnitude larger.
- **It would double every derivative raster** for no recoverable information.

### So this is a tolerance disagreement, and it is the benchmark's to resolve

`WG-BM-002` declares `exactness: exact_analytic` with `tolerance.default:
1.0e-09`. That tolerance is achievable only by a pipeline that both computes
*and stores* in float64. Since Horn is exact on a plane in real arithmetic, the
benchmark's derivation is right and its tolerance simply does not admit float32
storage.

**Recommendation to that repository, not acted on here:** either state a
storage-precision assumption alongside `slope_method: horn_3x3`, or set the
terrain tolerance to float32 epsilon (~1 × 10⁻⁶ relative). Item 32 says not to
duplicate the benchmark library, and changing another repository's tolerances
from here would be worse than duplicating it.

**What this repository did change:** nothing. The measurement is the deliverable.

## WG-BM-004 is the one that matters most, and it passes

`WG-BM-004` is titled *"Missing terrain stays missing and does not become zero
elevation"* — the exact defect found in the main repository's SRTM loader
(`reports/LEGACY_DATA_PIPELINE_COMPARISON.md`). Every structural assertion
matched:

- the no-data cell at (3, 3) reports `slope = None`, status
  `undefined_missing_neighbour`;
- **exactly 9 interior cells** are undefined — the Chebyshev-distance-1
  neighbourhood of the hole, hand-derived by the benchmark and reproduced here;
- 16 interior cells remain defined, all at the analytic 8.0495°;
- the hole never becomes 0 m, and never produces a fabricated slope.

An independent repository's hand derivation of *which* cells a 3×3 estimator
must abandon agrees cell-for-cell with this implementation.

## WG-BM-007: a representational difference, not a disagreement

Reported honestly: `node_count` 3 against an expected 4, and `components` 1
against an expected 2.

The cause is a difference in **input model**, not in behaviour.
`shelter_island` is declared as a node with **no incident edges**. The benchmark
models a network as nodes-plus-edges, where an isolated node exists. This
repository builds a road graph from **line geometry**, where a node is by
definition an endpoint or vertex of a line — so a shelter with no road cannot
appear as a road node, and there is no second component for it to be alone in.

And that is the right model, because *the shelter is not part of the road
network*. In this repository it is a facility or settlement, and the substance
of the benchmark is reported through a different field. Demonstrated directly:

```
matched   : {'village': 0}
UNMATCHED : ('shelter_island',)
snap_m    : 100.0
```

`shelter_island` is reported as an **unmatched settlement** — validation
`RD-007` — which is precisely the benchmark's point: *a place can exist
geographically and be unreachable by road*. Same finding, different field.

The rest of `WG-BM-007` (`selected_destination`, `reachable_nearest_destination`,
`euclidean_nearest_destination`) is **destination selection**, which is mission
logic this repository does not perform and will not
(`AGENTS.md` §5, D-0028's `ASSISTED_DISPATCH` `not_supplied_here` list). That
`refuge_north` should be preferred over a closer unreachable shelter is a
correct and important conclusion — and it is the consumer's to draw.

## WG-BM-008: structurally unanswerable here, by design

Its topology assertions pass (4 nodes, 3 edges, both destinations reachable),
but its actual subject does not apply. The case asserts
`reachability_checks: {a->c: True, c->a: False}` for a one-way corridor.

**This repository builds an undirected `networkx.MultiGraph`, deliberately.** So
`c->a` is `True` here and always will be. That is not a bug to fix: `oneway` is
carried as an opaque source attribute on the edge (D-0022, and the Uljin extract
shows `oneway` absent from *every* kept way), and turning it into traversal
direction is a routing decision. A directed reachability query is a consumer's
to run, from attributes this repository preserves.

Worth stating plainly rather than scoring as a pass: **a consumer that needs
one-way semantics must build its own directed graph from the `oneway`
attribute.** This repository gives it the attribute and the geometry, and does
not pretend the undirected graph answers the question.

## Overlap with this repository's own fixtures — and why it was not removed

Item 32 says not to duplicate the benchmark library. There is overlap, and it
stays, for a stated reason:

| Benchmark | This repository's fixture |
|---|---|
| WG-BM-001 flat plane | `flat_terrain` |
| WG-BM-002 tilted plane | `tilted_plane` |
| WG-BM-004 missing cells | `missing_cells_raster` |
| WG-BM-005 single exit | `single_exit_network` |
| WG-BM-006 two exits | `two_exit_network` |
| WG-BM-007 disconnected | `disconnected_network` |

These fixtures are **unit-test inputs that must run with no network and no
sibling repository checked out** (`AGENTS.md` §7: no network in tests). Deleting
them in favour of the benchmark repository would make this repository's own test
suite depend on another repository being present, which is exactly the coupling
D-0001 refuses.

The right division, and what is now in place: this repository keeps its
fixtures for its own tests, and the benchmark repository is the **independent
check** — run deliberately, as here, rather than vendored. The agreement above
is worth more *because* the two were written separately.

## Summary of what this cross-check established

1. This repository's Horn implementation matches an independently derived
   analytic answer **bit-for-bit in float64**. The remaining difference is a
   storage-precision choice, quantified at 1.1 × 10⁻⁷ degrees.
2. Its missing-data behaviour matches an independent hand derivation of *which*
   cells a 3×3 estimator must abandon — cell for cell.
3. Its flat-ground aspect (`null`, never a bearing) matches the benchmark's
   `aspect_on_flat: "null"` assumption exactly.
4. Its graph connectivity, articulation points and bridge counts match on
   B1 and B2.
5. Two cases do not apply, for reasons that are design decisions in this
   repository and are documented as such rather than recorded as failures.
