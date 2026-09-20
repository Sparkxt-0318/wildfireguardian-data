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


# --------------------------------------------------------------------------- #
# Temporal validity, surface model, and explicit migration (Phase 2 items 17/26)
# --------------------------------------------------------------------------- #
def _minimal_record(**overrides):
    from wildfireguardian_data.provenance.models import (
        DataClass,
        ProvenanceRecord,
        SourceRecord,
        TemporalProvenance,
    )

    payload = dict(
        layer_name="layer",
        data_class=DataClass.OBSERVED,
        temporal_class=TemporalProvenance.ANNUAL,
        temporal_reference="2021",
        sources=(SourceRecord(name="s", url_or_identifier="u"),),
    )
    payload.update(overrides)
    return ProvenanceRecord(**payload)


def test_validity_interval_defaults_to_unknown_not_to_a_guess():
    record = _minimal_record()
    assert record.valid_from == "UNKNOWN"
    assert record.valid_to == "UNKNOWN"
    assert record.surface_model == "UNKNOWN"
    # And that gap is visible, not silent.
    assert {"valid_from", "valid_to", "surface_model"} <= set(record.unknown_fields())


def test_not_applicable_validity_is_not_counted_as_a_gap():
    # A synthetic construct describes no moment in the world. Only UNKNOWN is a
    # gap; conflating the two would dilute the incompleteness metric (D-0009).
    record = _minimal_record(
        valid_from="not_applicable",
        valid_to="not_applicable",
        surface_model="not_applicable",
    )
    unknown = set(record.unknown_fields())
    assert not ({"valid_from", "valid_to", "surface_model"} & unknown)


def test_an_inverted_validity_interval_is_rejected():
    with pytest.raises(ProvenanceError) as excinfo:
        _minimal_record(valid_from="2022-03-05", valid_to="2021-01-01")
    assert "precedes" in str(excinfo.value)
    # A single instant is a legitimate interval, not an inverted one.
    assert _minimal_record(valid_from="2022-03-05", valid_to="2022-03-05")


def test_validity_fields_preserve_precision_and_reject_junk():
    # Year precision stays year precision: a source that knows only the year
    # must not be recorded as if it knew the day (A-T-3).
    assert _minimal_record(valid_from="2021").valid_from == "2021"
    assert _minimal_record(valid_to="2021-03").valid_to == "2021-03"
    with pytest.raises(ProvenanceError):
        _minimal_record(valid_from="early 2021")


def test_surface_model_is_a_closed_set():
    # An unrecognised spelling is refused rather than stored: a consumer
    # switching on this field would read an unknown string as neither, and a
    # DSM read as a DTM turns canopy steps into terrain (F-TER-3).
    for value in ("dsm", "dtm", "not_applicable", "UNKNOWN"):
        assert _minimal_record(surface_model=value).surface_model == value
    for value in ("DSM", "surface", "dem", ""):
        with pytest.raises(ProvenanceError) as excinfo:
            _minimal_record(surface_model=value)
        assert "surface_model" in str(excinfo.value)


def test_a_1_0_0_payload_migrates_explicitly_and_says_so():
    from wildfireguardian_data.provenance.models import (
        PROVENANCE_SCHEMA_VERSION,
        ProvenanceRecord,
    )

    payload = _minimal_record().to_dict()
    for gone in ("valid_from", "valid_to", "surface_model"):
        payload.pop(gone)
    payload["schema_version"] = "1.0.0"

    restored = ProvenanceRecord.from_dict(payload)
    assert restored.schema_version == PROVENANCE_SCHEMA_VERSION
    # The migration fills UNKNOWN, never a guess -- and leaves a trace, so the
    # gap cannot later be mistaken for a fact somebody checked.
    assert restored.valid_from == restored.valid_to == "UNKNOWN"
    assert restored.surface_model == "UNKNOWN"
    assert "migrated from provenance schema 1.0.0" in restored.notes


def test_an_unregistered_schema_version_is_refused_not_guessed():
    from wildfireguardian_data.provenance.models import ProvenanceRecord

    payload = _minimal_record().to_dict()
    # A *newer* version is refused for the same reason as an unknown older one:
    # the writer may have changed what a field this reader recognises means.
    payload["schema_version"] = "9.9.9"
    with pytest.raises(ProvenanceError) as excinfo:
        ProvenanceRecord.from_dict(payload)
    assert "no migration is registered" in str(excinfo.value)


def test_the_committed_real_bundle_still_reads_through_the_migration():
    # The point of a migration: an already-published bundle keeps working.
    from wildfireguardian_data.study_area import read_bundle

    bundle = read_bundle("data/study_areas/uljin_real_v1")
    dem = bundle.provenance["dem"]
    assert dem.schema_version == "1.1.0"
    # Its validity was never established, so it reads UNKNOWN. Notably the
    # migration does NOT set surface_model="dsm" from the Copernicus source
    # name, even though that happens to be true: inferring it would be
    # indistinguishable afterwards from a fact somebody verified.
    assert dem.surface_model == "UNKNOWN"


def test_reproject_records_the_co_registration_contract():
    # Phase 2 item 16: the guard that refuses averaging a categorical layer
    # leaves no trace, so the kind and both grids are recorded for audit.
    import numpy as np

    from wildfireguardian_data.fixtures import make_fixture
    from wildfireguardian_data.terrain.reproject import reproject_raster

    dem = make_fixture("tilted_plane")
    warped = reproject_raster(dem, "EPSG:32652", resampling="bilinear")
    params = warped.provenance.transformations[-1].parameters
    assert params["categorical_or_continuous"] == "continuous"
    assert params["raster_kind"] == "continuous"
    assert set(params["source_grid"]) == {"x_origin", "y_origin", "x_size", "y_size"}
    assert set(params["target_grid"]) == {"x_origin", "y_origin", "x_size", "y_size"}
    # The recorded target grid is the grid the output actually has.
    assert params["target_grid"] == warped.transform.to_dict()
    assert np.isfinite(warped.transform.x_size)

    # And the field is read off the layer, not hardcoded: a categorical layer
    # records "categorical", which is what makes the record worth auditing.
    fuels = make_fixture("korean_valley_fuels")
    warped_fuels = reproject_raster(fuels, "EPSG:32652")
    fuel_params = warped_fuels.provenance.transformations[-1].parameters
    assert fuel_params["categorical_or_continuous"] == "categorical"
    assert fuel_params["resampling"] == "nearest"
