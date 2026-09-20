"""The committed downstream CI fixture (Phase 2 item 29).

Every assertion here is a **closed-form or hand-derived** expectation, not a
snapshot of what this repository last produced. That is the whole point: a
downstream repository pins its CI to this bundle, so if these numbers were
regression snapshots, a bug here would become a bug there with a green test
suite in between.

The one number that is *not* independently derivable — the road critical-link
count — is included because deriving it by hand got it **wrong** the first
time (see ``integration_fixture_roads``), which is a good argument for
asserting it rather than trusting a reading of the geometry.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from wildfireguardian_data.bounds import Bounds
from wildfireguardian_data.provenance.models import DataClass
from wildfireguardian_data.study_area import read_bundle
from wildfireguardian_data.terrain.clip import clip_raster

BUNDLE = Path("data/study_areas/wg_integration_fixture_synthetic_v1")
CONFIG = "configs/integration_fixture_synthetic.yaml"

#: Closed-form, from the fixture's 10% eastward gradient. Horn's estimator is
#: exact on a plane, so these are the right answers and not a tolerance band.
EXPECTED_SLOPE_DEG = math.degrees(math.atan(0.10))  # 5.710593...
EXPECTED_ASPECT_DEG = 270.0

#: The declared study area, from the config. Not read from the bundle: the
#: point is to check the bundle covers what was asked for.
STUDY_AREA = (240_000.0, 490_000.0, 240_360.0, 490_360.0)


@pytest.fixture(scope="module")
def bundle():
    if not BUNDLE.exists():
        pytest.skip(f"{BUNDLE} is not committed in this checkout")
    return read_bundle(BUNDLE)


# --------------------------------------------------------------------------- #
# It is committed, and it is what the config builds
# --------------------------------------------------------------------------- #
def test_the_committed_fixture_rebuilds_identically(tmp_path):
    """A downstream repository pins this bundle; it must be reproducible.

    Layer checksums are compared, not file bytes: GeoTIFF writers embed
    timestamps and library versions, so byte equality would fail for reasons
    that have nothing to do with the data.
    """
    from wildfireguardian_data.study_area import (
        StudyAreaConfig,
        build_study_area,
        write_bundle,
    )

    if not BUNDLE.exists():
        pytest.skip(f"{BUNDLE} is not committed in this checkout")
    rebuilt = write_bundle(
        build_study_area(StudyAreaConfig.from_yaml(CONFIG)), tmp_path / "rebuilt"
    )
    committed = json.loads((BUNDLE / "manifest.json").read_text())
    fresh = json.loads((rebuilt / "manifest.json").read_text())

    committed_sums = {
        layer["name"]: layer["checksum_sha256"] for layer in committed["layers"]
    }
    fresh_sums = {layer["name"]: layer["checksum_sha256"] for layer in fresh["layers"]}
    assert committed_sums == fresh_sums, (
        "the committed fixture does not rebuild to the same layer checksums, so "
        "it is not deterministic and a downstream CI job cannot rely on it"
    )


def test_it_is_small_enough_to_be_a_dependency():
    if not BUNDLE.exists():
        pytest.skip(f"{BUNDLE} is not committed in this checkout")
    total = sum(path.stat().st_size for path in BUNDLE.rglob("*") if path.is_file())
    # Generous, but it catches the failure that matters: somebody enlarging the
    # fixture until it is no longer cheap for another repository to vendor.
    assert total < 512_000, f"fixture has grown to {total} bytes"


def test_it_is_unmistakably_synthetic(bundle):
    # AGENTS.md section 4: a synthetic bundle must not be mistakable for real
    # data by its name alone, and every layer must say so itself.
    assert "synthetic" in bundle.study_area_id
    for name, record in bundle.provenance.items():
        assert record.data_class is DataClass.SYNTHETIC, name
        assert "SYNTHETIC" in record.notes, name


# --------------------------------------------------------------------------- #
# Terrain: closed-form
# --------------------------------------------------------------------------- #
def test_slope_and_aspect_are_exact_over_the_whole_study_area(bundle):
    study = Bounds(*STUDY_AREA, crs=bundle.crs)
    for layer, expected in (
        (bundle.terrain.slope, EXPECTED_SLOPE_DEG),
        (bundle.terrain.aspect, EXPECTED_ASPECT_DEG),
    ):
        clipped = clip_raster(layer, study, name="study_area")
        assert clipped.shape == (12, 12)
        # No missing cells inside the study area. The layer as stored keeps a
        # 1-cell buffer ring that IS nodata -- that is where the estimator has
        # no 3x3 neighbourhood, and it is outside what was asked for.
        assert int((~clipped.valid_mask()).sum()) == 0
        values = clipped.data[clipped.valid_mask()]
        assert values.size == 144
        np.testing.assert_allclose(values, expected, rtol=0, atol=1e-5)


def test_the_buffer_ring_is_nodata_and_is_not_extrapolated(bundle):
    # D-0005 / F-TER-1: the edge is missing, never filled in. A consumer that
    # reads nodata as 0 gets slope 0 on a 10% slope, which is the exact mistake
    # this fixture should expose.
    slope = bundle.terrain.slope
    assert slope.shape == (14, 14)
    missing = ~slope.valid_mask()
    assert bool(missing[0, :].all()) and bool(missing[-1, :].all())
    assert bool(missing[:, 0].all()) and bool(missing[:, -1].all())
    assert int(missing.sum()) == 14 * 14 - 12 * 12


def test_the_dem_is_a_plane_rising_due_east(bundle):
    # Checks the gradient directly, so a transposed grid fails here rather than
    # producing a plausible aspect somewhere else.
    data = bundle.terrain.dem.data
    d_east = np.diff(data, axis=1)
    d_north = np.diff(data, axis=0)
    np.testing.assert_allclose(d_east, 0.10 * 30.0, rtol=0, atol=1e-4)
    np.testing.assert_allclose(d_north, 0.0, rtol=0, atol=1e-4)


# --------------------------------------------------------------------------- #
# Fuels: categorical, co-registered, with a hole
# --------------------------------------------------------------------------- #
def test_fuels_is_co_registered_with_the_dem_cell_for_cell(bundle):
    fuels, dem = bundle.fuels.layer, bundle.terrain.dem
    assert fuels.shape == dem.shape
    assert fuels.transform.to_dict() == dem.transform.to_dict()
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    assert manifest["canonical_grid"]["status"] == "SHARED"


def test_fuels_is_categorical_and_split_east_west(bundle):
    from wildfireguardian_data.raster import RasterKind

    fuels = bundle.fuels.layer
    assert fuels.kind is RasterKind.CATEGORICAL
    assert bundle.fuels.scheme is not None
    # The east-west split is there so a consumer that transposes x and y sees
    # it immediately -- EPSG:5187's authority axis order is (northing, easting)
    # while this package stores (easting, northing).
    valid = fuels.valid_mask()
    width = fuels.shape[1]
    west = fuels.data[:, : width // 2][valid[:, : width // 2]]
    east = fuels.data[:, width // 2 :][valid[:, width // 2 :]]
    assert len(np.unique(west)) == 1
    assert len(np.unique(east)) == 1
    assert np.unique(west)[0] != np.unique(east)[0]


def test_fuels_carries_a_nodata_hole_so_the_missing_path_is_exercised(bundle):
    fuels = bundle.fuels.layer
    missing = ~fuels.valid_mask()
    assert int(missing.sum()) > 0, (
        "the fixture should exercise the missing-data path by default, not only "
        "in a special test"
    )
    # Declared nodata agrees with the scheme's nodata_code, so there is exactly
    # one missing-data convention in play (validation BND-009).
    assert fuels.nodata == bundle.fuels.scheme.nodata_code
    # And no cell carries a code the scheme does not define.
    present = set(np.unique(fuels.data[fuels.valid_mask()]).tolist())
    assert present <= set(int(c) for c in bundle.fuels.scheme.codes)


# --------------------------------------------------------------------------- #
# Roads: hand-derived topology, and the trap it exists to expose
# --------------------------------------------------------------------------- #
def test_road_topology_matches_the_hand_derived_geometry(bundle):
    qa = bundle.roads.qa
    graph = qa["graph"]
    assert graph["nodes"] == 4
    assert graph["edges"] == 3
    # 180 + 180 + 120, exactly: planar length in metres (A-RD-5).
    assert graph["total_length_m"] == pytest.approx(480.0)
    assert qa["connectivity"]["component_count"] == 1
    component = qa["connectivity"]["components"][0]
    assert len(component["exit_nodes"]) == 2
    assert len(component["settlement_nodes"]) == 1


def test_no_single_egress_candidate_but_one_critical_link(bundle):
    """The reason this fixture is worth depending on.

    A consumer reading only ``single_egress_candidates`` sees an empty list and
    concludes the settlement is comfortably served. It is not: removing the one
    critical link cuts it off from both exits. D-0008 and F-RD-6 say so, and
    this asserts that a consumer reading both fields gets both answers.
    """
    qa = bundle.roads.qa
    assert qa["egress"]["single_egress_candidates"] == []
    critical = qa["fragility"]["critical_links"]
    assert len(critical) == 1
    only = critical[0]
    # The stem of the T, 120 m, cutting off the single settlement node.
    assert only["length_m"] == pytest.approx(120.0)
    assert len(only["settlement_nodes_cut_off"]) == 1
    # Phrased as topology, never as access (AGENTS.md section 5).
    assert "no path to any exit node" in only["note"]


def test_road_attributes_are_present_or_absent_never_defaulted(bundle):
    # D-0022: highway is the only attribute the source has. lanes, surface,
    # maxspeed and the rest are absent, and a consumer is exercised against
    # that rather than against invented defaults.
    availability = bundle.roads.qa["attributes"]
    assert availability["highway"]["edges_with_value"] == 3
    assert availability["highway"]["fraction_present"] == 1.0
    assert availability["highway"]["values"] == {"unclassified": 3}
    for absent in ("lanes", "surface", "maxspeed", "width", "oneway"):
        assert availability[absent]["edges_with_value"] == 0, absent
        assert availability[absent]["values"] == {}, absent


# --------------------------------------------------------------------------- #
# Population and facilities
# --------------------------------------------------------------------------- #
def test_population_is_aggregate_consistent_and_above_the_floor(bundle):
    # Read back from disk, so these are dicts rather than dataclasses.
    village = bundle.population.villages[0]
    assert village["population_total"] == 40
    # Strata sum exactly and the total clears the k-anonymity floor, so this
    # fixture emits no population findings for a consumer to learn to ignore.
    # The valley fixture is where POP-001 and POP-003 are exercised.
    strata = village["age_strata"]
    assert sum(band["count"] for band in strata["strata"]) == 40
    assert strata["total_counted"] == 40
    assert village["strata_total_matches_total"] is True
    assert strata["has_missing_counts"] is False
    assert village["population_total"] >= 5
    # The basis is stated rather than omitted: a count with no stated basis is
    # reported by POP-004, and "invented" is the honest basis for a synthetic
    # figure.
    assert village["count_basis"] == "synthetic_invented"


def test_population_carries_no_person_level_attribute(bundle):
    # F-POP-1. The loaders raise on names that look person-level or medical;
    # this asserts the fixture itself gives them nothing to raise on.
    forbidden = ("name_ko", "resident", "patient", "medical", "household", "phone")
    for feature in bundle.population.layer.features:
        keys = {key.lower() for key in feature.properties}
        for bad in forbidden:
            assert not any(bad in key for key in keys), bad


def test_facilities_are_one_base_one_destination_with_unknown_capability(bundle):
    records = bundle.facilities.records
    assert sorted(record["kind"] for record in records) == [
        "responder_base",
        "shelter",
    ]
    for record in records:
        # Exactly the real case: the place exists, and nobody established
        # whether it is operational or how many people it holds. Capacity is
        # never estimated from footprint area (A-FAC-3).
        assert record["operational_status"] == "UNKNOWN"
        assert record["capacity_persons"] is None
        assert record["suitability_assessed"] is False
        # And the role is the source's tag, carried through unchanged rather
        # than reinterpreted (A-FAC-2).
        assert record["source_kind_tag"] == record["kind"]


def test_nothing_in_the_bundle_claims_a_facility_is_a_viable_refuge(bundle):
    # AGENTS.md section 5. 'shelter' records what a source calls a place; it is
    # not a finding about the place.
    blob = json.dumps(list(bundle.facilities.records), default=str).lower()
    for forbidden in ("is_safe", "is_viable", "passable", "trapped"):
        assert forbidden not in blob, forbidden
    # The record does carry the word "usable" -- in a caveat saying it makes no
    # such claim. That is the opposite of an assertion, so it is checked for
    # rather than banned.
    for record in bundle.facilities.records:
        assert "makes no claim" in record["caveat"]


# --------------------------------------------------------------------------- #
# What downstream CI should actually gate on
# --------------------------------------------------------------------------- #
def test_the_fixture_has_no_errors_and_its_warnings_are_the_expected_ones():
    from wildfireguardian_data.validation import validate_bundle_directory

    if not BUNDLE.exists():
        pytest.skip(f"{BUNDLE} is not committed in this checkout")
    report = validate_bundle_directory(BUNDLE)
    assert not report.has_errors, report.to_text()

    codes = {finding.code for finding in report.findings}
    # RAS-003 is expected and documented: it is the retained buffer ring, which
    # is a large fraction of a small layer. Asserting it here means it cannot
    # quietly become something else.
    assert "RAS-003" in codes
    # These would mean the fixture had stopped being what it claims to be.
    for must_not_fire in ("POP-001", "POP-003", "RD-004", "BND-023"):
        assert must_not_fire not in codes, must_not_fire


def test_the_fixture_is_ready_for_every_consumer_and_none_of_it_is_planner_legal(
    bundle,
):
    from wildfireguardian_data.integration import build_compatibility_report

    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    report = build_compatibility_report(bundle, manifest)
    for name, profile in report["profiles"].items():
        assert profile["missing_inputs"] == [], name
    # And the thing that stops READY being read as a quality claim (D-0028).
    assert report["governance"]["contains_synthetic"] is True
    assert report["governance"]["all_layers_planner_legal"] is False
