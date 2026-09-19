"""Fuels (generic, never invented) and facilities (never assumed usable)."""

from __future__ import annotations

import numpy as np
import pytest
from helpers import make_provenance, make_raster
from shapely.geometry import Point, Polygon

from wildfireguardian_data import DataClass, RasterKind, SourceRecord, TemporalProvenance
from wildfireguardian_data.errors import ConfigError, IngestError, MissingDataError
from wildfireguardian_data.facilities import (
    Facility,
    FacilityKind,
    OperationalStatus,
    facilities_from_layer,
)
from wildfireguardian_data.fuels import (
    SYNTHETIC_DEMO_SCHEME,
    FuelClass,
    FuelClassScheme,
    fuel_layer_from_array,
    rasterize_fuel_vector,
)
from wildfireguardian_data.vector import Feature, VectorLayer

CRS = "EPSG:5187"


# --------------------------------------------------------------------------- #
# Fuel schemes
# --------------------------------------------------------------------------- #
def test_non_synthetic_scheme_without_a_source_is_refused():
    # docs/ASSUMPTIONS.md A-FU-1: this repository does not invent Korean fuel
    # crosswalks, and an unsourced scheme would be indistinguishable from a real
    # one once downstream.
    with pytest.raises(ConfigError) as excinfo:
        FuelClassScheme(
            name="korean_forest_types",
            classes=(FuelClass(1, "conifer"),),
            data_class=DataClass.OBSERVED,
            nodata_code=255,
        )
    assert "cites no source" in str(excinfo.value)


def test_synthetic_scheme_needs_no_source():
    scheme = FuelClassScheme(
        name="test_scheme",
        classes=(FuelClass(1, "a"), FuelClass(2, "b")),
        data_class=DataClass.SYNTHETIC,
        nodata_code=255,
    )
    assert scheme.codes == (1, 2)


def test_nodata_code_must_not_collide_with_a_real_class():
    with pytest.raises(ConfigError) as excinfo:
        FuelClassScheme(
            name="colliding",
            classes=(FuelClass(0, "zero"),),
            data_class=DataClass.SYNTHETIC,
            nodata_code=0,
        )
    assert "distinguishable" in str(excinfo.value)


def test_duplicate_class_codes_are_refused():
    with pytest.raises(ConfigError):
        FuelClassScheme(
            name="dupes",
            classes=(FuelClass(1, "a"), FuelClass(1, "b")),
            data_class=DataClass.SYNTHETIC,
            nodata_code=255,
        )


def test_shipped_demo_scheme_is_explicitly_synthetic():
    assert SYNTHETIC_DEMO_SCHEME.data_class is DataClass.SYNTHETIC
    assert "SYNTHETIC" in SYNTHETIC_DEMO_SCHEME.notes
    assert "not a crosswalk" in SYNTHETIC_DEMO_SCHEME.notes


def test_unknown_code_lookup_returns_the_unknown_literal_not_a_guess():
    assert SYNTHETIC_DEMO_SCHEME.label_for(99) == "UNKNOWN"
    assert SYNTHETIC_DEMO_SCHEME.label_for(255) == "nodata"


def test_scheme_serialisation_round_trip():
    payload = SYNTHETIC_DEMO_SCHEME.to_dict()
    restored = FuelClassScheme.from_dict(payload)
    assert restored.codes == SYNTHETIC_DEMO_SCHEME.codes
    assert restored.nodata_code == SYNTHETIC_DEMO_SCHEME.nodata_code


# --------------------------------------------------------------------------- #
# Fuel layers
# --------------------------------------------------------------------------- #
def fuel_layer(codes):
    from wildfireguardian_data import GridTransform

    return fuel_layer_from_array(
        np.asarray(codes, dtype=np.int32),
        name="fuels",
        transform=GridTransform(0.0, 300.0, 30.0, 30.0),
        crs=CRS,
        scheme=SYNTHETIC_DEMO_SCHEME,
        source=SourceRecord(name="test", source_date="not_applicable"),
        data_class=DataClass.SYNTHETIC,
        temporal_class=TemporalProvenance.STATIC,
        temporal_reference="not_applicable",
    )


def test_fuel_layer_is_categorical_and_uses_the_scheme_nodata():
    layer = fuel_layer([[1, 2], [5, 255]])
    assert layer.kind is RasterKind.CATEGORICAL
    assert layer.nodata == SYNTHETIC_DEMO_SCHEME.nodata_code
    assert layer.missing_count == 1


def test_undefined_class_codes_are_refused():
    with pytest.raises(IngestError) as excinfo:
        fuel_layer([[1, 99]])
    assert "does not define" in str(excinfo.value)


def test_float_fuel_array_is_refused():
    from wildfireguardian_data import GridTransform

    with pytest.raises(ConfigError) as excinfo:
        fuel_layer_from_array(
            np.array([[1.0, 2.0]]),
            name="fuels",
            transform=GridTransform(0.0, 300.0, 30.0, 30.0),
            crs=CRS,
            scheme=SYNTHETIC_DEMO_SCHEME,
            source=SourceRecord(name="test", source_date="not_applicable"),
            data_class=DataClass.SYNTHETIC,
            temporal_class=TemporalProvenance.STATIC,
        )
    assert "averaging" in str(excinfo.value)


def test_arithmetic_statistics_on_a_categorical_layer_are_refused():
    from wildfireguardian_data.terrain import categorical_statistics, raster_statistics

    layer = fuel_layer([[1, 2], [5, 255]])
    with pytest.raises(MissingDataError):
        raster_statistics(layer)
    stats = categorical_statistics(layer)
    assert stats["cells_missing"] == 1
    assert {entry["code"] for entry in stats["classes"]} == {1, 2, 5}


def test_categorical_fractions_are_of_valid_cells_and_say_so():
    from wildfireguardian_data.terrain import categorical_statistics

    stats = categorical_statistics(fuel_layer([[1, 255], [1, 2]]))
    by_code = {entry["code"]: entry for entry in stats["classes"]}
    assert by_code[1]["fraction_of_valid"] == pytest.approx(2 / 3)
    assert "not of the whole raster" in stats["note"]


def test_rasterise_leaves_unburned_cells_as_nodata_not_class_zero():
    template = make_raster(np.zeros((10, 10)), origin=(0.0, 0.0), cell_size_m=30.0)
    polygon = Polygon([(30, 30), (120, 30), (120, 120), (30, 120)])
    layer = VectorLayer(
        name="fuel_polys",
        features=(Feature(polygon, {"fuel_code": 4}),),
        crs=CRS,
        provenance=make_provenance("fuel_polys", value_unit="class"),
    )
    burned = rasterize_fuel_vector(
        layer,
        name="fuels",
        class_property="fuel_code",
        scheme=SYNTHETIC_DEMO_SCHEME,
        template=template,
    )
    assert set(np.unique(burned.data)) == {4, SYNTHETIC_DEMO_SCHEME.nodata_code}
    assert 0 not in np.unique(burned.data)


def test_rasterise_refuses_features_missing_the_class_attribute():
    template = make_raster(np.zeros((6, 6)), origin=(0.0, 0.0), cell_size_m=30.0)
    layer = VectorLayer(
        name="fuel_polys",
        features=(Feature(Polygon([(0, 0), (60, 0), (60, 60)]), {}),),
        crs=CRS,
        provenance=make_provenance("fuel_polys", value_unit="class"),
    )
    with pytest.raises(MissingDataError) as excinfo:
        rasterize_fuel_vector(
            layer,
            name="fuels",
            class_property="fuel_code",
            scheme=SYNTHETIC_DEMO_SCHEME,
            template=template,
        )
    assert "not burned as nodata silently" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Facilities
# --------------------------------------------------------------------------- #
def test_facility_defaults_are_unknown_and_unassessed():
    facility = Facility(
        facility_id="F1", kind=FacilityKind.SHELTER, geometry=Point(0, 0)
    )
    assert facility.operational_status is OperationalStatus.UNKNOWN
    assert facility.capacity_persons is None
    assert facility.suitability_assessed is False
    assert "not verified" in facility.to_dict()["caveat"]


def test_there_is_no_assumed_operational_status():
    # An "assumed operational" value is exactly the inference A-FAC-1 forbids.
    assert "ASSUMED_OPERATIONAL" not in {s.name for s in OperationalStatus}
    assert {s.value for s in OperationalStatus} == {
        "operational",
        "not_operational",
        "seasonal",
        "UNKNOWN",
    }


def test_claiming_an_assessment_requires_stating_its_basis():
    with pytest.raises(ConfigError) as excinfo:
        Facility(
            facility_id="F1",
            kind=FacilityKind.SHELTER,
            geometry=Point(0, 0),
            suitability_assessed=True,
        )
    assert "no stated basis" in str(excinfo.value)


def test_facility_without_geometry_is_refused():
    with pytest.raises(ConfigError):
        Facility(facility_id="F1", kind=FacilityKind.SHELTER, geometry=None)


def facility_layer(properties_list):
    return VectorLayer(
        name="facilities",
        features=tuple(
            Feature(Point(index * 10, 0), properties)
            for index, properties in enumerate(properties_list)
        ),
        crs=CRS,
        provenance=make_provenance("facilities", value_unit="not_applicable"),
    )


def test_unmapped_role_tag_is_refused_by_default():
    layer = facility_layer([{"facility_id": "F1", "kind": "bus_shelter"}])
    with pytest.raises(IngestError) as excinfo:
        facilities_from_layer(
            layer, kind_property="kind", kind_map={"fire_station": FacilityKind.FIRE_STATION}
        )
    assert "does not infer a facility's role" in str(excinfo.value)


def test_unmapped_role_can_be_kept_as_other_with_the_original_tag():
    layer = facility_layer([{"facility_id": "F1", "kind": "bus_shelter"}])
    records = facilities_from_layer(
        layer,
        kind_property="kind",
        kind_map={"fire_station": FacilityKind.FIRE_STATION},
        unmapped="other",
    )
    assert records[0].kind is FacilityKind.OTHER
    assert records[0].source_kind_tag == "bus_shelter"


def test_unrecognised_operational_status_is_not_mapped_to_operational():
    layer = facility_layer(
        [{"facility_id": "F1", "kind": "fire_station", "operational_status": "probably"}]
    )
    with pytest.raises(IngestError) as excinfo:
        facilities_from_layer(
            layer,
            kind_property="kind",
            kind_map={"fire_station": FacilityKind.FIRE_STATION},
        )
    assert "not mapped to 'operational'" in str(excinfo.value)


def test_missing_capacity_stays_none_and_is_never_estimated():
    layer = facility_layer([{"facility_id": "F1", "kind": "fire_station"}])
    records = facilities_from_layer(
        layer, kind_property="kind", kind_map={"fire_station": FacilityKind.FIRE_STATION}
    )
    assert records[0].capacity_persons is None
