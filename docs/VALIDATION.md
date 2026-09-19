# VALIDATION

## What validation is for

Validation is how this repository's central claim becomes checkable: that no
spatial, temporal, CRS, resolution, or missing-data property of a layer differs
from what its provenance states (`RESEARCH_QUESTION.md`). A bundle is not
"validated" because it built — it is validated because a named check looked for
a specific way of being wrong and did not find it.

## Severity is a scientific judgement, not formatting

| Severity | Meaning | What a consumer should do |
|---|---|---|
| **ERROR** | the data is wrong, or its meaning is unknowable | do not use the bundle |
| **WARNING** | usable but incomplete or surprising | read the finding and decide |
| **INFO** | context needed to interpret the data correctly | read it; it is not noise |

`UNKNOWN` provenance fields are **WARNING**, deliberately: an honest gap is a
finding, whereas a fabricated value would be a defect (`DECISIONS.md` D-0009).

Promoting an ERROR to a WARNING, or deleting a check, changes what this
repository claims. It requires a `DECISIONS.md` entry and a test
(`AGENTS.md` §2).

## Running it

```bash
wg-data validate-study-area data/study_areas/uljin_valley_synthetic_v1
wg-data validate-study-area <bundle> --strict     # warnings fail too
wg-data validate-study-area <bundle> --json       # machine-readable
```

Exit codes are in `INTERFACES.md`. `--strict` is what a CI job for a published
bundle should use.

**Validate the bundle on disk, not the one in memory.** Only the written bundle
has checksums to compare against its provenance, and only reading it back proves
the write round-trips. `wg-data build-study-area` therefore writes first, then
validates the directory, then stores the report in
`<bundle>/validation/report.json`.

## Two entry points

| Function | Checks | Also catches |
|---|---|---|
| `validation.validate_bundle(bundle)` | an in-memory bundle | — |
| `validation.validate_bundle_directory(dir)` | a bundle on disk | file checksums vs manifest vs provenance, orphan or missing provenance sidecars, missing manifest extras |

## Finding catalogue

Codes are stable and greppable, so a CI job can act on a specific finding
without matching prose. Generated from the code; see
`validation/checks.py` and `validation/bundle_checks.py`.

| Code | Severity | Finding |
|---|---|---|
| `CRS-001` | ERROR | layer declares no CRS; it cannot be combined with any other layer (A-CRS-5) |
| `CRS-002` | ERROR | CRS is not usable for length or slope work (geographic, or odd axis units) |
| `CRS-003` | ERROR | projected CRS whose axis unit is not the metre |
| `CRS-004` | INFO | authority axis order is (Northing, Easting) while this package stores (Easting, Northing) — a consumer reading authority order will transpose x and y |
| `CRS-005` | ERROR | a layer's CRS differs from the bundle's analysis CRS (D-0002) |
| `RAS-001` | WARNING / ERROR | no nodata value declared. WARNING for float (NaN is still detected), **ERROR** for integer, where missing cells would be indistinguishable from real values |
| `RAS-002` | ERROR | every cell is missing; any statistic is undefined, not zero |
| `RAS-003` | WARNING | missing fraction above the threshold (default 10%) |
| `RAS-004` | INFO | nodata declared but no missing cells present — often a sign a sentinel was converted upstream |
| `RAS-005` | WARNING | cells are not square, usually an unspecified reprojection |
| `RAS-006` | WARNING | cell size differs from the reference layer's |
| `RAS-007` | WARNING | grid origin offset within a cell — values do not correspond cell-for-cell even though cell sizes match |
| `PRV-001` | ERROR | `output_crs`, `value_unit` or `nodata_representation` is UNKNOWN; the values cannot be interpreted |
| `PRV-002` | WARNING | other provenance fields are UNKNOWN (counted, listed) |
| `PRV-003` | ERROR | provenance names no source |
| `PRV-004` | ERROR | layer is DERIVED but names no parent |
| `PRV-005` | INFO | layer is SYNTHETIC and makes no claim about any real place |
| `PRV-006` | INFO | layer is RETROSPECTIVE: not available to any real-time decision; using it as observation-time data is the temporal analogue of data leakage |
| `PRV-007` | WARNING | non-static layer with no `temporal_reference` |
| `PRV-008` | WARNING | no checksum recorded |
| `VEC-001` | ERROR | vector layer has no features |
| `VEC-002` | WARNING | mixed geometry types; consumers assuming one type will skip the others |
| `VEC-003` | ERROR | invalid geometry (self-intersection); area and intersection results undefined |
| `POP-001` | WARNING | age strata do not sum to `population_total` — reported, **not** reconciled (A-POP-5) |
| `POP-002` | WARNING | no `population_total` (None, not 0) |
| `POP-003` | WARNING | aggregate count below the k-anonymity floor (default 5). Not suppressed: a genuinely tiny hamlet is a real study object, but the figure is disclosive if published (D-0011) |
| `POP-004` | WARNING | count basis unstated (register / census / present population) |
| `POP-005` | ERROR | population layer carries attribute names that look person-level or medical. The load-time guard *raises*; at bundle level the same rule is a finding, so a bundle that did not come through this package's loaders is still checked and a validator that crashed does not hide everything else |
| `FAC-001` | INFO | facilities have no suitability assessment; this repository performs none |
| `FAC-002` | WARNING | facilities have UNKNOWN operational status; do not assume operational |
| `FAC-003` | INFO | facilities have no sourced capacity; it is never estimated from footprint area |
| `RD-001` | WARNING | multiple connected components; often a missing junction node rather than a real disconnection (D-0007) |
| `RD-002` | WARNING | isolated segments: single edges alone in their component with no exit |
| `RD-003` | INFO | components with a settlement and exactly one exit node. A topological observation, **not** a claim that anyone is trapped (D-0008) |
| `RD-004` | WARNING | components with a settlement and no exit node; usually a too-tight clip or an incomplete network |
| `RD-005` | INFO | some exits were inferred from the study-area boundary, so egress counts depend on where the area was cut (A-RD-4) |
| `RD-006` | WARNING | line pairs crossing without a shared node: either grade separation or missing junctions (D-0007) |
| `RD-007` | WARNING | settlements that matched no road node within the snap distance |
| `BND-001` | ERROR | bundle contains no layers |
| `BND-002` | WARNING | terrain does not fully cover the declared study area; nothing was invented to fill it |
| `BND-003` | ERROR | slope has no more missing cells than the DEM — impossible for a 3×3 estimator, so the edge was extrapolated or the layers do not correspond (D-0005) |
| `BND-004` | ERROR | fuel layer is not declared categorical (A-FU-3) |
| `BND-005` | ERROR | fuel layer has no class scheme; its integers have no meaning |
| `BND-008` | ERROR | fuel layer contains class codes its scheme does not define. Re-checked here, not only at ingest, because a bundle can arrive hand-edited or from another tool |
| `BND-009` | ERROR | fuel layer's declared nodata disagrees with its scheme's `nodata_code`; two missing-data conventions leave some missing cells indistinguishable from a real class |
| `BND-006` | INFO | bundle contains SYNTHETIC layers and describes no real place |
| `BND-007` | WARNING | synthetic bundle whose id does not say `synthetic` — it could be mistaken for real data (`AGENTS.md` §4) |
| `BND-010` | ERROR | no manifest: not a study-area bundle |
| `BND-011` | ERROR | bundle could not be read (includes a checksum mismatch) |
| `BND-012` | WARNING | provenance sidecar with no corresponding manifest layer |
| `BND-013` | ERROR | manifest layer with no provenance sidecar |
| `BND-014` | WARNING | provenance records no checksum |
| `BND-015` | ERROR | provenance checksum disagrees with the manifest |
| `BND-016` | WARNING | manifest extra points at a missing file |
| `BND-017` | WARNING | manifest records no checksum for an extra, so that file cannot be verified |
| `BND-018` | ERROR | a manifest extra does not match its recorded checksum — the QA report or statistics have been edited since the bundle was written |
| `BND-019` | ERROR | a provenance sidecar the manifest checksums is missing |
| `BND-020` | ERROR | a provenance sidecar does not match its recorded checksum — the provenance has been edited |

## What the test suite checks, and how

Every check above has a **positive case** in `tests/test_validation.py`: data
constructed to trigger it, asserting both the code and the severity. A check
that can never fire is not a check.

The strongest tests in the suite are the ones with closed-form answers:

| Test | Why it is evidence and not a snapshot |
|---|---|
| `test_slope_and_aspect_exact_on_a_plane` | Horn's estimator is exact on a plane, so the expected value is analytic. Tolerance `1e-4` degrees |
| `test_aspect_cardinal_directions_are_not_transposed` | four surfaces falling N/E/S/W; a transposed axis or an upslope convention fails |
| `test_slope_scales_correctly_with_cell_size` | doubling cell size must halve `tan(slope)` |
| `test_nan_and_sentinel_representations_give_identical_statistics` | the same void marked `NaN` and `-9999` must summarise identically. `NaN` fails loudly, `-9999` fails silently, so agreement is real evidence the sentinel is honoured |
| `test_one_missing_cell_invalidates_its_whole_neighbourhood_and_no_more` | exactly 9 cells lost, not 1 and not the whole row |
| `test_two_exit_network_has_no_critical_link_for_the_hamlet` | a redundant network must yield **zero** critical links; a finder that reported every bridge fails |
| `test_parallel_edges_are_not_reported_as_bridges` | two roads between the same junctions are two ways out |
| `test_rebuild_is_byte_identical` | makes A-REP-2's reproducibility claim checkable |
| `test_tampering_with_a_layer_file_is_detected_on_read` | proves checksums are enforced, not merely stored |
| `test_documented_korean_crs_facts_match_the_proj_database` | documentation about a CRS is checked against PROJ, so a PROJ upgrade fails a test instead of drifting the docs |

Run:

```bash
python -m pytest -q             # 287 tests, offline and deterministic
python -m pytest -m network     # 3 live-source tests, excluded by default
```

## What validation does **not** check

Stated so nobody reads a passing report as more than it is:

- that the values are **true** — a plausible wrong elevation passes everything;
- that a source is **appropriate** for a study — a DSM passes every check while
  being the wrong model for ground slope (`FAILURE_MODES.md` F-TER-3);
- that a road is **passable** or a shelter **usable** — out of scope by
  construction (`SCOPE.md`);
- that OSM's rural coverage is **complete** — completeness is UNKNOWN and no
  check can establish it (F-RD-3);
- that a renamed person-level column is **not** person-level — the privacy guard
  is a name-based tripwire (F-POP-1).
