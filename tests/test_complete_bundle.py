"""The complete real bundle (Phase 2 item 20) and the OSM facility fetcher.

The facilities assertions here are the most consequential in this suite. Two of
the three OSM facility elements in the Uljin box would be badly misread by any
pipeline that trusted the tag, and these tests pin the fact that this one does
not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wildfireguardian_data.bounds import Bounds
from wildfireguardian_data.errors import IngestError, NetworkAccessError
from wildfireguardian_data.sources import (
    DEFAULT_OSM_FACILITY_TAGS,
    fetch_osm_facilities,
)
from wildfireguardian_data.study_area import read_bundle

BUNDLE = Path("data/study_areas/uljin_real_v2")


@pytest.fixture(scope="module")
def bundle():
    if not BUNDLE.exists():
        pytest.skip("uljin_real_v2 is not committed in this checkout")
    return read_bundle(BUNDLE)


# --------------------------------------------------------------------------- #
# The fetcher's guards -- no network needed
# --------------------------------------------------------------------------- #
def test_facility_fetch_is_network_opt_in_and_wgs84_only():
    box = Bounds(129.308, 36.904, 129.354, 36.941, crs="EPSG:4326")
    with pytest.raises(NetworkAccessError) as excinfo:
        fetch_osm_facilities(box)
    assert "allow_network" in str(excinfo.value)
    projected = Bounds(227_500.0, 478_500.0, 231_500.0, 482_500.0, crs="EPSG:5187")
    with pytest.raises(IngestError) as excinfo:
        fetch_osm_facilities(projected, allow_network=True)
    assert "EPSG:4326" in str(excinfo.value)


def test_the_default_tag_set_assigns_no_roles():
    # DEFAULT_OSM_FACILITY_TAGS says which elements are KEPT, never what they
    # ARE. A mapping from tag to role in this module would be a role judgement
    # made by this repository instead of by the caller (A-FAC-2).
    for values in DEFAULT_OSM_FACILITY_TAGS.values():
        assert values is None or isinstance(values, frozenset)
    # Fire stations are looked for under both spellings OSM uses, so a station
    # is not missed by tag choice.
    assert "fire_station" in DEFAULT_OSM_FACILITY_TAGS["amenity"]
    assert "fire_station" in DEFAULT_OSM_FACILITY_TAGS["emergency"]


# --------------------------------------------------------------------------- #
# The bundle
# --------------------------------------------------------------------------- #
def test_the_complete_bundle_has_four_of_five_layers(bundle):
    components = bundle.components()
    for present in ("terrain", "roads", "fuels", "facilities"):
        assert components[present] is not None, present
    assert components["population"] is None


def test_the_absent_population_layer_states_a_documented_reason(bundle):
    # The point of representing absence as a value (D-0027). An ABSENT layer
    # whose reason is UNKNOWN is a gap nobody wrote down; this one is not.
    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    entry = manifest["layers"]["population"]
    assert entry["status"] == "ABSENT"
    assert entry["reason"] != "UNKNOWN"
    reason = entry["reason"]
    # It names the sources tried and why they failed, not just "unavailable".
    assert "kosis" in reason.lower()
    assert "PROXY_FAILURE" in reason
    assert "estimated" in reason.lower()


def test_no_osm_facility_tag_was_promoted_to_a_refuge_role(bundle):
    """The single most consequential assertion in this suite.

    OSM has three facility-tagged elements in this box:

    * ``amenity=shelter`` 구산리청암정 -- but ``shelter_type=gazebo``, a
      traditional pavilion;
    * ``amenity=school`` "구 노음초등학교 구고분교 터" -- 터 means *site of*, so
      the school does not stand;
    * ``amenity=townhall`` 구산3리마을회관 -- a real village hall, whose
      designation as an assembly point nobody here has verified.

    None of them is an established wildfire refuge, so none may carry a refuge
    role. A pipeline that mapped ``amenity=shelter`` to ``shelter`` would have
    invented an evacuation destination out of a gazebo.
    """
    records = bundle.facilities.records
    assert len(records) == 3
    roles = {record["kind"] for record in records}
    assert roles == {"other"}, (
        "no facility in this box is an established refuge, so none may carry a "
        f"refuge role; got {roles}"
    )
    for record in records:
        assert record["operational_status"] == "UNKNOWN"
        assert record["capacity_persons"] is None
        assert record["suitability_assessed"] is False


def test_the_misleading_tags_are_preserved_so_a_reader_can_see_them(bundle):
    # The role is 'other', but the evidence for that decision must survive:
    # shelter_type=gazebo and the 터 in the name are why. Dropping the source
    # tags would leave the decision unauditable.
    attributes = [record.get("attributes", {}) for record in bundle.facilities.records]
    blob = json.dumps([bundle.facilities.records, attributes], ensure_ascii=False)
    assert "gazebo" in blob, "shelter_type=gazebo must survive into the bundle"
    assert "터" in blob, "the 'site of' marker in the school's name must survive"


def test_every_facility_has_a_stable_identifier_from_the_source(bundle):
    # Item 11 asks for facility_id. It comes from OSM's own identity, which
    # resolves for anyone who looks it up -- not from the name, which is
    # neither unique nor stable.
    ids = [record["facility_id"] for record in bundle.facilities.records]
    assert len(set(ids)) == 3
    for facility_id in ids:
        assert facility_id.startswith("osm:")
        assert facility_id.split("/")[-1].isdigit()


def test_no_fire_station_is_claimed_and_none_is_denied(bundle):
    # There is no fire station mapped in this box. That is a fact about the box
    # and about OSM coverage, NOT a finding that Uljin-gun has no fire service,
    # and nothing in the bundle may imply the latter.
    kinds = {record["kind"] for record in bundle.facilities.records}
    assert "fire_station" not in kinds
    notes = bundle.provenance["facilities"].notes
    assert "not mapped in this box" in notes
    assert "never 'there is none'" in notes


def test_the_facilities_layer_claims_nothing_about_safety(bundle):
    blob = json.dumps(list(bundle.facilities.records), ensure_ascii=False).lower()
    for forbidden in ("is_safe", "is_viable", "passable", "trapped"):
        assert forbidden not in blob, forbidden


def test_the_complete_bundle_validates_with_no_errors():
    from wildfireguardian_data.validation import validate_bundle_directory

    if not BUNDLE.exists():
        pytest.skip("uljin_real_v2 is not committed in this checkout")
    report = validate_bundle_directory(BUNDLE)
    assert not report.has_errors, report.to_text()
    codes = {finding.code for finding in report.findings}
    # The facility caveats must fire: they are how the bundle says it performed
    # no suitability assessment and sourced no capacity.
    assert "FAC-001" in codes
    assert "FAC-003" in codes


def test_readiness_improves_over_the_terrain_and_roads_only_bundle(bundle):
    from wildfireguardian_data.integration import build_compatibility_report

    manifest = json.loads((BUNDLE / "bundle_manifest.json").read_text())
    report = build_compatibility_report(bundle, manifest)
    profiles = report["profiles"]
    # Two consumers are now satisfiable; FORECAST_VALUE still is not, and names
    # exactly what it lacks rather than reporting a score.
    assert profiles["OSSE"]["missing_inputs"] == []
    assert profiles["ASSISTED_DISPATCH"]["missing_inputs"] == []
    assert profiles["FORECAST_VALUE"]["missing_inputs"] == ["population"]
    # And the DSM limitation reaches the consumers that use terrain.
    assert "SURFACE_MODEL_NOT_TERRAIN" in profiles["OSSE"]["material_limitations"]
