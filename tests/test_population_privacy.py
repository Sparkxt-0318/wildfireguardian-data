"""Population: aggregate only, and the privacy tripwire.

The guard is a tripwire, not a security boundary
(``docs/FAILURE_MODES.md`` F-POP-1); the last test in this file asserts that
limitation explicitly so nobody mistakes it for protection.
"""

from __future__ import annotations

import pytest
from helpers import make_provenance
from shapely.geometry import Point, Polygon

from wildfireguardian_data.errors import ConfigError, IngestError, PrivacyGuardError
from wildfireguardian_data.population import (
    AgeStrataSet,
    AgeStratum,
    SettlementCentroidKind,
    VillagePopulation,
    check_privacy,
    villages_from_layer,
)
from wildfireguardian_data.vector import Feature, VectorLayer

CRS = "EPSG:5187"


@pytest.mark.parametrize(
    "field",
    [
        "resident_registration_number",
        "jumin",
        "rrn",
        "patient_name",
        "person_id",
        "first_name",
        "diagnosis",
        "icd10",
        "medication_list",
        "care_grade",
        "ltci",
        "disability_grade",
        "mobility_status",
        "bedridden",
        "wheelchair_user",
        "phone",
        "email",
        "household_id",
        "street_address",
        "medical_record_no",
    ],
)
def test_person_level_and_medical_fields_are_refused(field):
    with pytest.raises(PrivacyGuardError):
        check_privacy([field])


@pytest.mark.parametrize(
    "field",
    [
        "population_total",
        "pop_0_14",
        "pop_15_64",
        "age_65_plus",
        "households_count",
        "settlement_id",
        "name",
        "count_basis",
        "reference_date",
        "area_ha",
        "elderly_share",
    ],
)
def test_legitimate_aggregate_fields_are_accepted(field):
    # The wider project studies mobility-limited residents, so the aggregate
    # demography must stay usable; it is the individual and the diagnosis that
    # are refused (docs/ASSUMPTIONS.md A-POP-2).
    check_privacy([field])


def test_guard_message_points_at_the_rule_and_forbids_bypassing():
    with pytest.raises(PrivacyGuardError) as excinfo:
        check_privacy(["care_grade"])
    message = str(excinfo.value)
    assert "D-0011" in message
    assert "do not bypass this guard" in message


def test_guard_is_case_and_separator_insensitive():
    for spelling in ("CareGrade", "care-grade", "CARE_GRADE", "care grade"):
        with pytest.raises(PrivacyGuardError):
            check_privacy([spelling])


# --------------------------------------------------------------------------- #
# Aggregate semantics
# --------------------------------------------------------------------------- #
def test_age_strata_are_half_open_and_labelled_accordingly():
    strata = AgeStrataSet(
        (AgeStratum(0, 15, 3), AgeStratum(15, 65, 20), AgeStratum(65, None, 30))
    )
    assert [s.label for s in strata.strata] == ["0-14", "15-64", "65+"]
    assert strata.total_counted == 53


def test_overlapping_strata_are_refused():
    with pytest.raises(ConfigError) as excinfo:
        AgeStrataSet((AgeStratum(0, 20, 1), AgeStratum(15, 65, 2)))
    assert "double-count" in str(excinfo.value)


def test_missing_stratum_count_is_none_not_zero():
    strata = AgeStrataSet((AgeStratum(0, 15, None), AgeStratum(15, None, 10)))
    assert strata.has_missing_counts is True
    assert strata.total_counted == 10  # only supplied counts are summed


def test_all_missing_strata_sum_to_none_not_zero():
    strata = AgeStrataSet((AgeStratum(0, 15, None), AgeStratum(15, None, None)))
    assert strata.total_counted is None


def test_strata_mismatch_is_reported_not_reconciled():
    village = VillagePopulation(
        settlement_id="V1",
        centroid=Point(0, 0),
        population_total=3,
        age_strata=AgeStrataSet((AgeStratum(65, None, 4),)),
    )
    assert village.strata_total_matches_total is False
    # Nothing was rescaled to make them agree.
    assert village.population_total == 3
    assert village.age_strata.total_counted == 4


def test_unknown_total_gives_none_for_the_consistency_question():
    village = VillagePopulation(settlement_id="V1", centroid=Point(0, 0))
    assert village.population_total is None
    assert village.strata_total_matches_total is None


def test_centroid_derived_from_a_polygon_is_labelled_as_derived():
    village = VillagePopulation(
        settlement_id="V1",
        geometry=Polygon([(0, 0), (100, 0), (100, 100), (0, 100)]),
    )
    assert village.centroid is not None
    assert village.centroid_kind is SettlementCentroidKind.REPRESENTATIVE_POINT


def test_record_without_any_location_is_refused():
    with pytest.raises(ConfigError):
        VillagePopulation(settlement_id="V1")


def test_negative_population_is_refused():
    with pytest.raises(ConfigError):
        VillagePopulation(settlement_id="V1", centroid=Point(0, 0), population_total=-1)


def test_villages_from_layer_requires_stable_identifiers():
    layer = VectorLayer(
        name="villages",
        features=(Feature(Point(0, 0), {"name": "no id here"}),),
        crs=CRS,
        provenance=make_provenance("villages", value_unit="count"),
    )
    with pytest.raises(IngestError) as excinfo:
        villages_from_layer(layer)
    assert "reordered" in str(excinfo.value)


def test_villages_from_layer_keeps_missing_totals_as_none():
    layer = VectorLayer(
        name="villages",
        features=(Feature(Point(0, 0), {"settlement_id": "V1"}),),
        crs=CRS,
        provenance=make_provenance("villages", value_unit="count"),
    )
    villages = villages_from_layer(layer)
    assert villages[0].population_total is None


def test_the_guard_is_documented_as_defeatable_by_renaming():
    # This is the limitation stated in docs/FAILURE_MODES.md F-POP-1. Asserting
    # it keeps the documentation honest: if the guard ever became
    # value-inspecting, this test would fail and the doc would need updating.
    check_privacy(["notes"])  # a renamed medical field would pass
