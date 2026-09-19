"""Validation: every check has a positive case, and severities are asserted.

A check that can never fire is not a check. Each test here builds data that
*should* trigger a specific finding code and asserts both the code and its
severity, because severity is a scientific judgement (``AGENTS.md`` §2).
"""

from __future__ import annotations

import numpy as np
import pytest
from helpers import make_provenance, make_raster, planar_surface

from wildfireguardian_data import (
    DataClass,
    GridTransform,
    RasterKind,
    RasterLayer,
    SourceRecord,
    TemporalProvenance,
)
from wildfireguardian_data.provenance import ProvenanceRecord
from wildfireguardian_data.validation import (
    Severity,
    ValidationReport,
    check_crs_declared,
    check_crs_projected_metre,
    check_grid_alignment,
    check_layers_share_crs,
    check_provenance_completeness,
    check_provenance_consistency,
    check_raster_missing_data,
    check_raster_nodata_declared,
    check_raster_square_cells,
    check_vector_geometry,
)


def codes(report: ValidationReport) -> set[str]:
    return {finding.code for finding in report.findings}


def severity_of(report: ValidationReport, code: str) -> Severity:
    return next(f.severity for f in report.findings if f.code == code)


@pytest.fixture
def report() -> ValidationReport:
    return ValidationReport(target="test")


# --------------------------------------------------------------------------- #
# CRS checks
# --------------------------------------------------------------------------- #
def test_undeclared_crs_is_an_error(report):
    check_crs_declared(make_raster(planar_surface(shape=(4, 4)), crs=None), report)
    assert "CRS-001" in codes(report)
    assert severity_of(report, "CRS-001") is Severity.ERROR


def test_geographic_crs_is_an_error(report):
    layer = make_raster(planar_surface(shape=(4, 4)), crs="EPSG:4326", cell_size_m=1 / 3600)
    check_crs_projected_metre(layer, report)
    assert "CRS-002" in codes(report)
    assert severity_of(report, "CRS-002") is Severity.ERROR


def test_northing_first_axis_order_is_reported_as_info(report):
    check_crs_projected_metre(make_raster(planar_surface(shape=(4, 4))), report)
    assert "CRS-004" in codes(report)
    assert severity_of(report, "CRS-004") is Severity.INFO


def test_mixed_crs_layers_are_an_error(report):
    layers = [
        make_raster(planar_surface(shape=(4, 4)), name="a"),
        make_raster(planar_surface(shape=(4, 4)), name="b", crs="EPSG:32652"),
    ]
    check_layers_share_crs(layers, report)
    assert "CRS-005" in codes(report)
    assert severity_of(report, "CRS-005") is Severity.ERROR


# --------------------------------------------------------------------------- #
# Raster checks
# --------------------------------------------------------------------------- #
def test_undeclared_nodata_is_a_warning_for_float_and_an_error_for_int(report):
    float_layer = make_raster(planar_surface(shape=(4, 4)), nodata=None)
    check_raster_nodata_declared(float_layer, report)
    assert severity_of(report, "RAS-001") is Severity.WARNING

    int_report = ValidationReport(target="int")
    int_layer = make_raster(
        np.ones((4, 4), dtype=np.int32),
        nodata=None,
        kind=RasterKind.CATEGORICAL,
        value_unit="class",
    )
    check_raster_nodata_declared(int_layer, int_report)
    assert severity_of(int_report, "RAS-001") is Severity.ERROR


def test_fully_missing_raster_is_an_error(report):
    check_raster_missing_data(make_raster(np.full((4, 4), np.nan)), report)
    assert "RAS-002" in codes(report)
    assert severity_of(report, "RAS-002") is Severity.ERROR


def test_high_missing_fraction_is_a_warning(report):
    data = planar_surface(shape=(10, 10))
    data[:3] = np.nan  # 30 percent
    check_raster_missing_data(make_raster(data), report, warn_fraction=0.10)
    assert "RAS-003" in codes(report)
    assert severity_of(report, "RAS-003") is Severity.WARNING


def test_declared_nodata_with_no_missing_cells_is_info(report):
    check_raster_missing_data(make_raster(planar_surface(shape=(4, 4))), report)
    assert "RAS-004" in codes(report)
    assert severity_of(report, "RAS-004") is Severity.INFO


def test_non_square_cells_are_a_warning(report):
    layer = RasterLayer(
        name="stretched",
        data=planar_surface(shape=(4, 4)),
        transform=GridTransform(0.0, 120.0, 30.0, 20.0),
        crs="EPSG:5187",
        nodata=np.nan,
        kind=RasterKind.CONTINUOUS,
        value_unit="m",
        provenance=make_provenance("stretched"),
    )
    check_raster_square_cells(layer, report)
    assert "RAS-005" in codes(report)


def test_different_cell_sizes_are_flagged(report):
    check_grid_alignment(
        [
            make_raster(planar_surface(shape=(4, 4)), name="a", cell_size_m=30.0),
            make_raster(planar_surface(shape=(4, 4)), name="b", cell_size_m=10.0),
        ],
        report,
    )
    assert "RAS-006" in codes(report)


def test_half_cell_grid_offset_is_flagged(report):
    # A 15 m offset on a 30 m grid shifts every value by half a cell while the
    # cell sizes match, which is exactly the misalignment that looks fine.
    base = make_raster(planar_surface(shape=(4, 4)), name="a", origin=(0.0, 0.0))
    shifted = make_raster(planar_surface(shape=(4, 4)), name="b", origin=(15.0, 0.0))
    check_grid_alignment([base, shifted], report)
    assert "RAS-007" in codes(report)
    detail = next(f for f in report.findings if f.code == "RAS-007").detail
    assert detail["offset_x"] == pytest.approx(15.0)


def test_aligned_rasters_produce_no_alignment_finding(report):
    check_grid_alignment(
        [
            make_raster(planar_surface(shape=(4, 4)), name="a", origin=(0.0, 0.0)),
            make_raster(planar_surface(shape=(4, 4)), name="b", origin=(60.0, 0.0)),
        ],
        report,
    )
    assert not {"RAS-006", "RAS-007"} & codes(report)


# --------------------------------------------------------------------------- #
# Provenance checks
# --------------------------------------------------------------------------- #
def test_missing_interpretive_provenance_is_an_error(report):
    record = ProvenanceRecord(
        layer_name="x",
        data_class=DataClass.OBSERVED,
        temporal_class=TemporalProvenance.STATIC,
        sources=(SourceRecord(name="s", source_date="2023"),),
    )
    check_provenance_completeness(record, report)
    assert "PRV-001" in codes(report)
    assert severity_of(report, "PRV-001") is Severity.ERROR
    detail = next(f for f in report.findings if f.code == "PRV-001").detail
    assert set(detail["fields"]) == {"output_crs", "value_unit", "nodata_representation"}


def test_remaining_unknowns_are_warnings_not_errors(report):
    record = ProvenanceRecord(
        layer_name="x",
        data_class=DataClass.OBSERVED,
        temporal_class=TemporalProvenance.STATIC,
        sources=(SourceRecord(name="s", source_date="2023"),),
        output_crs="EPSG:5187",
        value_unit="m",
        nodata_representation="nan",
    )
    check_provenance_completeness(record, report)
    assert "PRV-001" not in codes(report)
    assert severity_of(report, "PRV-002") is Severity.WARNING


def test_sourceless_provenance_is_an_error(report):
    record = ProvenanceRecord(
        layer_name="x",
        data_class=DataClass.OBSERVED,
        temporal_class=TemporalProvenance.STATIC,
    )
    check_provenance_consistency(record, report)
    assert "PRV-003" in codes(report)
    assert severity_of(report, "PRV-003") is Severity.ERROR


def test_retrospective_layers_get_a_leakage_warning_as_info(report):
    record = ProvenanceRecord(
        layer_name="burn_perimeter",
        data_class=DataClass.OBSERVED,
        temporal_class=TemporalProvenance.RETROSPECTIVE,
        temporal_reference="2022",
        sources=(SourceRecord(name="s", source_date="2022"),),
    )
    check_provenance_consistency(record, report)
    assert "PRV-006" in codes(report)
    message = next(f for f in report.findings if f.code == "PRV-006").message
    assert "data leakage" in message


def test_non_static_layer_without_a_temporal_reference_is_a_warning(report):
    record = ProvenanceRecord(
        layer_name="x",
        data_class=DataClass.OBSERVED,
        temporal_class=TemporalProvenance.ANNUAL,
        sources=(SourceRecord(name="s", source_date="2023"),),
    )
    check_provenance_consistency(record, report)
    assert "PRV-007" in codes(report)


# --------------------------------------------------------------------------- #
# Vector checks
# --------------------------------------------------------------------------- #
def test_empty_vector_layer_is_an_error(report):
    from wildfireguardian_data.vector import VectorLayer

    layer = VectorLayer(
        name="empty",
        features=(),
        crs="EPSG:5187",
        provenance=make_provenance("empty", value_unit="not_applicable"),
    )
    check_vector_geometry(layer, report)
    assert severity_of(report, "VEC-001") is Severity.ERROR


def test_invalid_geometry_is_an_error(report):
    from shapely.geometry import Polygon

    from wildfireguardian_data.vector import Feature, VectorLayer

    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    layer = VectorLayer(
        name="bowtie",
        features=(Feature(bowtie, {}),),
        crs="EPSG:5187",
        provenance=make_provenance("bowtie", value_unit="not_applicable"),
    )
    check_vector_geometry(layer, report)
    assert severity_of(report, "VEC-003") is Severity.ERROR


# --------------------------------------------------------------------------- #
# Report behaviour
# --------------------------------------------------------------------------- #
def test_status_depends_on_severity_and_strictness():
    report = ValidationReport(target="t")
    assert report.status() == "pass"

    report.add("X-001", Severity.WARNING, "a warning")
    assert report.status() == "pass_with_warnings"
    assert report.status(strict=True) == "fail"

    report.add("X-002", Severity.ERROR, "an error")
    assert report.status() == "fail"
    assert report.has_errors


def test_report_serialises_with_a_schema_version():
    import json

    report = ValidationReport(target="t")
    report.add("X-001", Severity.INFO, "note")
    payload = json.loads(json.dumps(report.to_dict()))
    assert payload["schema_version"]
    assert payload["counts"]["INFO"] == 1


# --------------------------------------------------------------------------- #
# Whole-bundle checks
# --------------------------------------------------------------------------- #
def test_bundle_validation_of_the_example_reports_no_errors(tmp_path):
    from wildfireguardian_data.study_area import (
        StudyAreaConfig,
        build_study_area,
        write_bundle,
    )
    from wildfireguardian_data.validation import validate_bundle_directory

    config = StudyAreaConfig.from_yaml("configs/uljin_valley_synthetic.yaml")
    directory = write_bundle(build_study_area(config), tmp_path / "bundle")
    report = validate_bundle_directory(directory)
    assert not report.has_errors, report.to_text()
    # The deliberate findings in the fixture are present, so the checks are
    # demonstrably live rather than vacuously passing.
    found = codes(report)
    assert "POP-001" in found  # strata do not sum to the total
    assert "POP-003" in found  # count below the k-anonymity floor
    assert "RD-001" in found  # multiple components
    assert "RD-004" in found  # settlement in a component with no exit
    assert "RD-006" in found  # crossing without a node
    assert "BND-006" in found  # synthetic bundle


def test_missing_manifest_is_reported_not_raised(tmp_path):
    from wildfireguardian_data.validation import validate_bundle_directory

    report = validate_bundle_directory(tmp_path)
    assert "BND-010" in codes(report)
    assert report.has_errors


def test_synthetic_bundle_named_after_a_real_place_is_warned_about():
    from wildfireguardian_data.study_area import StudyAreaConfig, build_study_area
    from wildfireguardian_data.study_area.bundle import StudyAreaBundle
    from wildfireguardian_data.validation import validate_bundle

    config = StudyAreaConfig.from_yaml("configs/uljin_valley_synthetic.yaml")
    built = build_study_area(config)
    renamed = StudyAreaBundle(
        study_area_id="uljin_valley",  # no 'synthetic' in the name
        crs=built.crs,
        bounds=built.bounds,
        terrain=built.terrain,
    )
    report = validate_bundle(renamed)
    assert "BND-007" in codes(report)


# --------------------------------------------------------------------------- #
# Checks re-applied at bundle level, for bundles that did not come through
# this package's own loaders
# --------------------------------------------------------------------------- #
def test_undefined_fuel_code_in_a_bundle_is_an_error(tmp_path):
    # fuels.fuel_layer_from_array validates on the way in, but a bundle can
    # arrive hand-edited or from another tool. An undefined code is data whose
    # meaning nobody knows, so validation must catch it too.
    import numpy as np

    from wildfireguardian_data.fuels import SYNTHETIC_DEMO_SCHEME
    from wildfireguardian_data.study_area import StudyAreaConfig, build_study_area
    from wildfireguardian_data.study_area.bundle import FuelsComponent
    from wildfireguardian_data.validation import validate_bundle

    built = build_study_area(
        StudyAreaConfig.from_yaml("configs/uljin_valley_synthetic.yaml")
    )
    tampered_data = np.array(built.fuels.layer.data)
    tampered_data[0, 0] = 99  # not in the scheme
    tampered = built.fuels.layer.with_data(tampered_data)
    built.fuels = FuelsComponent(layer=tampered, scheme=SYNTHETIC_DEMO_SCHEME)

    report = validate_bundle(built)
    assert "BND-008" in codes(report)
    assert severity_of(report, "BND-008") is Severity.ERROR


def test_fuel_nodata_disagreeing_with_its_scheme_is_an_error():
    from wildfireguardian_data.fuels import SYNTHETIC_DEMO_SCHEME
    from wildfireguardian_data.study_area import StudyAreaConfig, build_study_area
    from wildfireguardian_data.study_area.bundle import FuelsComponent
    from wildfireguardian_data.validation import validate_bundle

    built = build_study_area(
        StudyAreaConfig.from_yaml("configs/uljin_valley_synthetic.yaml")
    )
    mismatched = built.fuels.layer.with_data(built.fuels.layer.data, nodata=254)
    built.fuels = FuelsComponent(layer=mismatched, scheme=SYNTHETIC_DEMO_SCHEME)

    report = validate_bundle(built)
    assert "BND-009" in codes(report)
    assert severity_of(report, "BND-009") is Severity.ERROR


def test_person_level_population_attribute_in_a_bundle_is_an_error():
    # The load-time guard raises; at bundle level the same rule is a finding,
    # because a validator that crashed would report nothing else about the
    # bundle. Either way the prohibited data does not pass silently.
    from shapely.geometry import Point

    from wildfireguardian_data.study_area.bundle import PopulationComponent, StudyAreaBundle
    from wildfireguardian_data.validation import validate_bundle
    from wildfireguardian_data.vector import Feature, VectorLayer

    layer = VectorLayer(
        name="villages",
        features=(
            Feature(
                Point(226_500.0, 477_500.0),
                {"settlement_id": "V1", "care_grade": 3},
            ),
        ),
        crs="EPSG:5187",
        provenance=make_provenance("villages", value_unit="count"),
    )
    bundle = StudyAreaBundle(
        study_area_id="privacy_probe_synthetic",
        crs="EPSG:5187",
        bounds=__import__(
            "wildfireguardian_data.bounds", fromlist=["Bounds"]
        ).Bounds(226_000.0, 477_000.0, 232_000.0, 483_000.0, crs="EPSG:5187"),
        population=PopulationComponent(layer=layer),
    )
    report = validate_bundle(bundle)
    assert "POP-005" in codes(report)
    assert severity_of(report, "POP-005") is Severity.ERROR
