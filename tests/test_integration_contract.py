"""The downstream contract: the canonical manifest and the readiness report.

These tests exist because the contract is the one artifact another repository
codes against. A wrong number in a bundle is bad; a wrong number in the
contract is bad *and* invisible, because a consumer reads the contract instead
of the data.
"""

from __future__ import annotations

import json

import pytest

from wildfireguardian_data.integration import (
    BUNDLE_CONTRACT_SCHEMA_VERSION,
    CONSUMER_PROFILES,
    CONTRACT_LAYER_SLOTS,
    ReadinessLevel,
    build_bundle_manifest,
    build_compatibility_report,
)
from wildfireguardian_data.study_area import (
    StudyAreaConfig,
    build_study_area,
    read_bundle,
    write_bundle,
)

CONFIG = "configs/uljin_valley_synthetic.yaml"


@pytest.fixture(scope="module")
def written_bundle(tmp_path_factory):
    directory = write_bundle(
        build_study_area(StudyAreaConfig.from_yaml(CONFIG)),
        tmp_path_factory.mktemp("bundle") / "b",
    )
    return directory


@pytest.fixture(scope="module")
def contract(written_bundle):
    return json.loads((written_bundle / "bundle_manifest.json").read_text())


# --------------------------------------------------------------------------- #
# Shape (Phase 2 item 25)
# --------------------------------------------------------------------------- #
def test_the_contract_carries_every_key_the_brief_requires(contract):
    for key in (
        "bundle_id",
        "bundle_schema_version",
        "created_at",
        "study_area",
        "crs",
        "canonical_grid",
        "layers",
        "provenance",
        "validation",
        "checksums",
    ):
        assert key in contract, key
    assert contract["bundle_schema_version"] == BUNDLE_CONTRACT_SCHEMA_VERSION


def test_every_layer_slot_is_always_present_even_when_empty(written_bundle):
    # The whole reason the contract is a different shape from the internal
    # manifest: absence must be a value, not a missing key. A consumer reads a
    # missing key as easily as an oversight as a finding.
    bundle = read_bundle(written_bundle)
    bundle.population = None
    bundle.facilities = None
    manifest = build_bundle_manifest(
        bundle,
        created_at="T",
        absent_reasons={"population": "KOSIS unreachable: PROXY_FAILURE"},
    )
    assert set(manifest["layers"]) == set(CONTRACT_LAYER_SLOTS)
    assert manifest["layers"]["population"]["status"] == "ABSENT"
    assert "PROXY_FAILURE" in manifest["layers"]["population"]["reason"]
    # An absent layer with no documented reason is itself a finding: a gap
    # nobody wrote down, not a gap that does not exist.
    assert manifest["layers"]["facilities"]["reason"] == "UNKNOWN"
    assert manifest["layers"]["facilities"]["layers"] == []


def test_every_present_layer_carries_the_required_per_layer_fields(contract):
    for slot in CONTRACT_LAYER_SLOTS:
        entry = contract["layers"][slot]
        if entry["status"] != "PRESENT":
            continue
        for layer in entry["layers"]:
            for field in (
                "name",
                "data_class",
                "units",
                "licence",
                "checksum",
                "limitations",
                "valid_from",
                "valid_to",
                "surface_model",
                "sources",
            ):
                assert field in layer, f"{slot}/{layer.get('name')}: {field}"
            # The date is not one field: source_date and acquisition_date are
            # different facts and neither stands in for the other.
            for source in layer["sources"]:
                assert "source_date" in source
                assert "acquisition_date" in source


def test_data_class_is_uppercase_at_the_export_boundary(contract):
    # Research governance OC-029. Lowercase internally; see D-0023.
    for entry in contract["layers"].values():
        for layer in entry["layers"]:
            assert layer["data_class"] == layer["data_class"].upper()
            assert layer["data_class"] != ""


def test_the_contract_never_asserts_validity_or_safety(contract):
    blob = json.dumps(contract).lower()
    # The words this repository may not use as claims (AGENTS.md section 5).
    for forbidden in (
        '"safe"',
        '"passable"',
        '"usable"',
        '"trapped"',
        "is_safe",
        "scientifically_valid",
        "fit_for_purpose",
    ):
        assert forbidden not in blob, forbidden
    caveats = " ".join(contract["caveats"]).lower()
    assert "does not assert" in caveats
    assert "nothing was estimated" in caveats


def test_the_fuel_scheme_is_labelled_as_not_a_fire_behaviour_fuel_model(contract):
    scheme = contract["layers"]["fuels"]["class_scheme"]
    assert scheme["status"] == "PRESENT"
    assert scheme["scheme_kind"] == "source_class"
    # D-0024: the single most important thing to state about this layer.
    assert scheme["is_fire_behaviour_fuel_model"] is False


def test_road_topology_comes_from_the_persisted_qa_not_the_live_graph(
    written_bundle, contract
):
    # The graph is an in-memory object read_bundle does not reconstruct, so a
    # contract sourced from it would say different things about the same bundle
    # depending on whether it was just built or read back.
    graph = contract["layers"]["roads"]["graph"]
    assert graph["status"] == "PRESENT"
    assert graph["node_count"] is not None
    assert graph["edge_count"] is not None
    assert graph["component_count"] is not None
    assert graph["length_unit"] == "m"
    # And it survives a round trip identically.
    rederived = build_bundle_manifest(
        read_bundle(written_bundle), created_at=contract["created_at"]
    )
    assert rederived["layers"]["roads"]["graph"] == graph


def test_the_canonical_grid_is_reported_as_shared_or_mismatched(contract):
    grid = contract["canonical_grid"]
    assert grid["status"] in {"SHARED", "MISMATCHED", "NO_RASTERS"}
    assert grid["status"] == "SHARED", (
        "the pipeline warps siblings onto the DEM grid (D-0018), so a built "
        "bundle should be co-registered"
    )
    assert set(grid["grid"]) == {"x_origin", "y_origin", "x_size", "y_size"}


def test_a_grid_mismatch_is_reported_rather_than_papered_over(written_bundle):
    from wildfireguardian_data.terrain.clip import clip_raster

    bundle = read_bundle(written_bundle)
    # A sibling raster clipped to a different extent from the DEM: same cell
    # size, different origin and shape, so a consumer indexing both by the same
    # (row, col) reads the wrong cell. This is the realistic version of the
    # mistake D-0018 exists to prevent.
    dem_bounds = bundle.terrain.dem.bounds
    bundle.fuels.layer = clip_raster(
        bundle.fuels.layer,
        dem_bounds.buffered(-300.0),
        name="fuels",
    )
    assert bundle.fuels.layer.shape != bundle.terrain.dem.shape
    manifest = build_bundle_manifest(bundle, created_at="T")
    assert manifest["canonical_grid"]["status"] == "MISMATCHED"
    assert "mismatch_detail" in manifest["canonical_grid"]
    # And it reaches the consumers that index two rasters together, but not the
    # one that does not.
    report = build_compatibility_report(bundle, manifest)
    assert (
        "CANONICAL_GRID_MISMATCHED"
        in report["profiles"]["OSSE"]["material_limitations"]
    )
    assert (
        "CANONICAL_GRID_MISMATCHED"
        not in report["profiles"]["ASSISTED_DISPATCH"]["material_limitations"]
    )


# --------------------------------------------------------------------------- #
# Readiness (Phase 2 items 22, 23, 24, 28)
# --------------------------------------------------------------------------- #
def test_a_complete_bundle_is_ready_for_every_profile(written_bundle, contract):
    report = build_compatibility_report(read_bundle(written_bundle), contract)
    for name, profile in report["profiles"].items():
        assert profile["readiness"] in {
            ReadinessLevel.READY,
            ReadinessLevel.READY_WITH_LIMITATIONS,
        }, f"{name}: {profile['missing_inputs']}"
        assert profile["missing_inputs"] == []


def test_ready_on_synthetic_data_is_flanked_by_the_governance_warning(
    written_bundle, contract
):
    # This is the test that stops READY being read as a quality claim. An
    # all-synthetic bundle CAN be READY -- that is what a fixture is for -- so
    # the report must make its class impossible to miss.
    report = build_compatibility_report(read_bundle(written_bundle), contract)
    governance = report["governance"]
    assert governance["data_classes_present"] == ["SYNTHETIC"]
    assert governance["contains_synthetic"] is True
    assert governance["all_layers_planner_legal"] is False
    assert governance["not_planner_legal"]
    caveats = " ".join(report["caveats"]).lower()
    assert "does not mean the data is accurate" in caveats
    assert "no claim of scientific validity" in caveats


def test_a_missing_layer_makes_a_profile_incomplete_and_names_it(written_bundle):
    bundle = read_bundle(written_bundle)
    bundle.fuels = None
    manifest = build_bundle_manifest(bundle, created_at="T")
    report = build_compatibility_report(bundle, manifest)
    # OSSE needs fuels; ASSISTED_DISPATCH does not. Readiness is per consumer,
    # not a single score for the bundle.
    assert report["profiles"]["OSSE"]["readiness"] == ReadinessLevel.INCOMPLETE
    assert report["profiles"]["OSSE"]["missing_inputs"] == ["fuels"]
    assert (
        report["profiles"]["ASSISTED_DISPATCH"]["readiness"] != ReadinessLevel.INCOMPLETE
    )


def test_a_fuel_raster_without_its_scheme_is_present_but_unusable(written_bundle):
    # Integers with no legend are not a fuel layer, so "present" is not enough.
    bundle = read_bundle(written_bundle)
    bundle.fuels.scheme = None
    manifest = build_bundle_manifest(bundle, created_at="T")
    report = build_compatibility_report(bundle, manifest)
    osse = report["profiles"]["OSSE"]
    assert osse["readiness"] == ReadinessLevel.INCOMPLETE
    assert "fuels" in osse["missing_inputs"]
    fuels_slot = next(s for s in osse["inputs"] if s["slot"] == "fuels")
    assert fuels_slot["status"] == "PRESENT_BUT_UNUSABLE"
    assert "no class scheme" in fuels_slot["reason"]


def test_every_profile_states_what_this_repository_will_not_supply():
    # The not_supplied_here lists are how scope refusal becomes machine-readable
    # instead of a paragraph somebody has to find (docs/SCOPE.md).
    for name, profile in CONSUMER_PROFILES.items():
        assert profile["not_supplied_here"], name
        assert set(profile["requires"]) <= set(CONTRACT_LAYER_SLOTS), name
        assert set(profile["requires_detail"]) == set(profile["requires"]), name
    joined = " ".join(
        item
        for profile in CONSUMER_PROFILES.values()
        for item in profile["not_supplied_here"]
    ).lower()
    # The four things Phase 2 items 22-24 explicitly place outside this repo.
    assert "forecast" in joined
    assert "routing" in joined
    assert "dynamic state" in joined
    assert "fuel model" in joined


def test_readiness_verdict_and_contract_cannot_disagree(written_bundle, contract):
    # The report is built FROM the manifest rather than re-deriving the facts,
    # so a slot the contract calls ABSENT can never read PRESENT here.
    bundle = read_bundle(written_bundle)
    report = build_compatibility_report(bundle, contract)
    for profile in report["profiles"].values():
        for slot in profile["inputs"]:
            contract_status = contract["layers"][slot["slot"]]["status"]
            if contract_status == "ABSENT":
                assert slot["status"] == "ABSENT"
            else:
                assert slot["status"] in {"PRESENT", "PRESENT_BUT_UNUSABLE"}
