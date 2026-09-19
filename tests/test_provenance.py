"""Provenance: required facts, UNKNOWN as a value, append-only history."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from wildfireguardian_data.errors import ProvenanceError
from wildfireguardian_data.provenance import (
    NOT_APPLICABLE,
    UNKNOWN,
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
    Transformation,
    read_provenance,
    sha256_array,
    sha256_json,
    validate_temporal_string,
    write_provenance,
)


def minimal_record(**changes) -> ProvenanceRecord:
    payload = dict(
        layer_name="dem",
        data_class=DataClass.OBSERVED,
        temporal_class=TemporalProvenance.STATIC,
        sources=(SourceRecord(name="test source", source_date="2023"),),
    )
    payload.update(changes)
    return ProvenanceRecord(**payload)


# --------------------------------------------------------------------------- #
# Required facts
# --------------------------------------------------------------------------- #
def test_data_class_and_temporal_class_have_no_defaults():
    # A wrong default (OBSERVED, STATIC) would be believed, so both are required.
    with pytest.raises(TypeError):
        ProvenanceRecord(layer_name="x")  # type: ignore[call-arg]


def test_derived_layer_must_name_its_parents():
    with pytest.raises(ProvenanceError) as excinfo:
        minimal_record(data_class=DataClass.DERIVED)
    assert "names no parents" in str(excinfo.value)


def test_source_record_requires_a_name():
    with pytest.raises(ProvenanceError):
        SourceRecord(name="")


def test_unknown_is_the_default_for_unsupplied_facts():
    record = minimal_record()
    assert record.original_crs == UNKNOWN
    assert record.vertical_datum == UNKNOWN
    assert "original_crs" in record.unknown_fields()


def test_unknown_fields_include_nested_source_fields():
    record = minimal_record()
    unknowns = record.unknown_fields()
    assert any(field.startswith("sources[0].") for field in unknowns)


def test_not_applicable_is_distinct_from_unknown_and_is_not_counted_as_a_gap():
    # A synthetic generator has no source date: "not_applicable" says that,
    # whereas UNKNOWN would imply a date nobody looked up.
    record = ProvenanceRecord(
        layer_name="synthetic",
        data_class=DataClass.SYNTHETIC,
        temporal_class=TemporalProvenance.STATIC,
        temporal_reference=NOT_APPLICABLE,
        sources=(SourceRecord(name="generator", source_date=NOT_APPLICABLE),),
    )
    assert "temporal_reference" not in record.unknown_fields()
    assert not any(
        field == "sources[0].source_date" for field in record.unknown_fields()
    )


# --------------------------------------------------------------------------- #
# Temporal semantics
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["2023", "2023-05", "2023-05-01", UNKNOWN, NOT_APPLICABLE])
def test_accepted_temporal_precisions_are_preserved_unchanged(value):
    assert validate_temporal_string(value, "source_date") == value


def test_naive_datetime_strings_are_refused():
    # A naive Korean local timestamp read as UTC is off by nine hours, which can
    # move an event to the wrong day and hence the wrong fire danger rating.
    with pytest.raises(ProvenanceError) as excinfo:
        validate_temporal_string("2026-03-14T09:00:00", "source_date")
    assert "offset" in str(excinfo.value)


def test_naive_datetime_objects_are_refused():
    with pytest.raises(ProvenanceError):
        validate_temporal_string(datetime(2026, 3, 14, 9, 0), "source_date")


def test_aware_datetimes_are_accepted_and_keep_their_offset():
    korea = timezone(timedelta(hours=9))
    text = validate_temporal_string(datetime(2026, 3, 14, 9, 0, tzinfo=korea), "source_date")
    assert text.endswith("+09:00")


def test_none_is_refused_in_favour_of_the_unknown_literal():
    with pytest.raises(ProvenanceError) as excinfo:
        validate_temporal_string(None, "source_date")
    assert "UNKNOWN" in str(excinfo.value)


def test_source_date_and_acquisition_date_are_separate_fields():
    source = SourceRecord(
        name="Copernicus-like", source_date="2011", acquisition_date="2026-09-19"
    )
    assert source.source_date == "2011"
    assert source.acquisition_date == "2026-09-19"


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #
def test_derivation_appends_and_never_rewrites_history():
    parent = minimal_record(
        transformations=(Transformation(operation="read_geotiff"),)
    )
    child = parent.derive("slope", Transformation(operation="slope"))
    assert [t.operation for t in child.transformations] == ["read_geotiff", "slope"]
    # The parent is untouched: provenance is append-only.
    assert [t.operation for t in parent.transformations] == ["read_geotiff"]
    assert child.parents == ("dem",)
    assert child.data_class is DataClass.DERIVED


def test_synthetic_parent_yields_a_synthetic_child():
    # Test data must not be able to launder itself into DERIVED and thereby look
    # like real data with a processing history.
    parent = minimal_record(data_class=DataClass.SYNTHETIC)
    child = parent.derive("slope", Transformation(operation="slope"))
    assert child.data_class is DataClass.SYNTHETIC


def test_with_transformation_appends_one_entry():
    record = minimal_record()
    updated = record.with_transformation(Transformation(operation="clip_raster"))
    assert len(updated.transformations) == len(record.transformations) + 1


def test_unknown_field_names_are_refused_on_update():
    with pytest.raises(ProvenanceError):
        minimal_record().with_updates(not_a_field=1)


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def test_round_trip_through_dict_preserves_everything():
    record = minimal_record(
        spatial_resolution=(30.0, 30.0),
        resolution_unit="m",
        value_unit="m",
        checksum="abc",
        transformations=(
            Transformation(operation="clip_raster", parameters={"buffer_cells": 2}),
        ),
    )
    payload = json.loads(json.dumps(record.to_dict()))
    restored = ProvenanceRecord.from_dict(payload)
    assert restored.to_dict() == record.to_dict()


def test_unrecognised_schema_version_is_refused():
    payload = minimal_record().to_dict()
    payload["schema_version"] = "99.0.0"
    with pytest.raises(ProvenanceError) as excinfo:
        ProvenanceRecord.from_dict(payload)
    assert "schema_version" in str(excinfo.value)


def test_unexpected_fields_are_refused_rather_than_dropped():
    payload = minimal_record().to_dict()
    payload["mystery"] = "value"
    with pytest.raises(ProvenanceError) as excinfo:
        ProvenanceRecord.from_dict(payload)
    assert "silently drop" in str(excinfo.value)


def test_negative_resolution_is_refused():
    with pytest.raises(ProvenanceError) as excinfo:
        minimal_record(spatial_resolution=(30.0, -30.0))
    assert "positive" in str(excinfo.value)


def test_sidecar_round_trip(tmp_path):
    record = minimal_record(value_unit="m", checksum="deadbeef")
    path = write_provenance(tmp_path, record)
    assert path.exists()
    assert read_provenance(tmp_path, "dem").to_dict() == record.to_dict()


def test_missing_sidecar_raises_rather_than_returning_empty(tmp_path):
    with pytest.raises(ProvenanceError):
        read_provenance(tmp_path, "nonexistent")


def test_nan_parameters_survive_serialisation_as_text():
    # JSON has no NaN; emitting the string keeps it distinguishable from null,
    # which would read as "absent".
    transformation = Transformation(
        operation="test", parameters={"nodata": float("nan")}
    )
    payload = json.loads(json.dumps(transformation.to_dict()))
    assert payload["parameters"]["nodata"] == "nan"


# --------------------------------------------------------------------------- #
# Checksums
# --------------------------------------------------------------------------- #
def test_array_checksum_distinguishes_dtype_and_shape():
    import numpy as np

    array = np.arange(16, dtype=np.float32).reshape(4, 4)
    assert sha256_array(array) != sha256_array(array.astype(np.float64))
    assert sha256_array(array) != sha256_array(array.reshape(2, 8))
    assert sha256_array(array) == sha256_array(array.copy())


def test_json_checksum_is_key_order_independent():
    assert sha256_json({"a": 1, "b": 2}) == sha256_json({"b": 2, "a": 1})
    assert sha256_json({"a": 1}) != sha256_json({"a": 2})
