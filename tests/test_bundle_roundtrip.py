"""Bundle assembly, serialisation round trip, and reproducibility."""

from __future__ import annotations

import json

import numpy as np
import pytest

from wildfireguardian_data.bounds import Bounds
from wildfireguardian_data.errors import BundleError, CRSMismatchError
from wildfireguardian_data.study_area import (
    StudyAreaBundle,
    StudyAreaConfig,
    build_study_area,
    read_bundle,
    summarize_bundle,
    write_bundle,
)

CONFIG_PATH = "configs/uljin_valley_synthetic.yaml"


@pytest.fixture(scope="module")
def config() -> StudyAreaConfig:
    return StudyAreaConfig.from_yaml(CONFIG_PATH)


@pytest.fixture(scope="module")
def bundle(config) -> StudyAreaBundle:
    return build_study_area(config)


def test_config_parses_with_every_component(config):
    assert config.study_area_id == "uljin_valley_synthetic_v1"
    assert config.terrain is not None
    assert config.roads is not None
    assert config.fuels is not None
    assert config.population is not None
    assert config.facilities is not None
    assert config.requires_network is False


def test_bundle_has_the_expected_layers(bundle):
    assert bundle.layer_names == [
        "dem",
        "slope_deg",
        "aspect_deg",
        "roads",
        "fuels",
        "villages",
        "facilities",
    ]


def test_every_layer_shares_the_analysis_crs(bundle):
    from wildfireguardian_data.crs import crs_equal

    for layer in bundle.all_layers():
        assert crs_equal(layer.crs, bundle.crs)


def test_every_layer_has_provenance_with_a_transformation_chain(bundle):
    for layer in bundle.all_layers():
        record = bundle.provenance[layer.name]
        assert record.layer_name == layer.name
        assert record.transformations, layer.name
        assert record.sources, layer.name


def test_bundle_construction_refuses_a_layer_in_another_crs(bundle):
    from wildfireguardian_data.study_area.bundle import TerrainComponent

    reprojected = bundle.terrain.dem.derived(
        bundle.terrain.dem.data,
        name="dem",
        transformation=bundle.provenance["dem"].transformations[-1],
        crs="EPSG:32652",
    )
    with pytest.raises(CRSMismatchError):
        StudyAreaBundle(
            study_area_id="mixed_crs_synthetic",
            crs=bundle.crs,
            bounds=bundle.bounds,
            terrain=TerrainComponent(dem=reprojected),
        )


def test_bundle_refuses_bounds_in_another_crs(bundle):
    with pytest.raises(CRSMismatchError):
        StudyAreaBundle(
            study_area_id="bad_bounds_synthetic",
            crs=bundle.crs,
            bounds=Bounds(129.0, 36.0, 130.0, 37.0, crs="EPSG:4326"),
        )


def test_slope_has_more_missing_cells_than_the_dem(bundle):
    # A 3x3 estimator must lose the edge; equality would mean the edge was
    # extrapolated (docs/DECISIONS.md D-0005).
    assert bundle.terrain.slope.missing_count > bundle.terrain.dem.missing_count


def test_derivatives_stay_on_the_dem_grid(bundle):
    assert bundle.terrain.slope.transform == bundle.terrain.dem.transform
    assert bundle.terrain.aspect.transform == bundle.terrain.dem.transform
    assert bundle.fuels.layer.transform == bundle.terrain.dem.transform


def test_terrain_covers_the_declared_study_area(bundle):
    dem_bounds = bundle.terrain.dem.bounds
    assert dem_bounds.min_x <= bundle.bounds.min_x
    assert dem_bounds.max_x >= bundle.bounds.max_x
    assert dem_bounds.min_y <= bundle.bounds.min_y
    assert dem_bounds.max_y >= bundle.bounds.max_y


def test_write_then_read_round_trips_arrays_exactly(bundle, tmp_path):
    directory = write_bundle(bundle, tmp_path / "bundle")
    restored = read_bundle(directory)

    assert restored.study_area_id == bundle.study_area_id
    assert restored.layer_names == bundle.layer_names
    for name in ("dem", "slope_deg", "aspect_deg", "fuels"):
        assert np.array_equal(
            restored.layer(name).data, bundle.layer(name).data, equal_nan=True
        ), name
    for name in ("roads", "villages", "facilities"):
        assert len(restored.layer(name)) == len(bundle.layer(name))


def test_round_trip_preserves_nodata_and_units(bundle, tmp_path):
    restored = read_bundle(write_bundle(bundle, tmp_path / "bundle"))
    assert np.isnan(restored.layer("dem").nodata)
    assert restored.layer("fuels").nodata == 255
    assert restored.layer("slope_deg").provenance.value_unit == "deg"


def test_manifest_records_checksums_that_match_the_files(bundle, tmp_path):
    from wildfireguardian_data.provenance import sha256_file

    directory = write_bundle(bundle, tmp_path / "bundle")
    manifest = json.loads((directory / "manifest.json").read_text())
    for entry in manifest["layers"]:
        assert entry["checksum_sha256"] == sha256_file(directory / entry["path"])


def test_provenance_sidecar_checksum_matches_the_manifest(bundle, tmp_path):
    directory = write_bundle(bundle, tmp_path / "bundle")
    manifest = json.loads((directory / "manifest.json").read_text())
    for entry in manifest["layers"]:
        sidecar = json.loads(
            (directory / "provenance" / f"{entry['name']}.provenance.json").read_text()
        )
        assert sidecar["checksum"] == entry["checksum_sha256"]


def test_tampering_with_a_layer_file_is_detected_on_read(bundle, tmp_path):
    directory = write_bundle(bundle, tmp_path / "bundle")
    target = directory / "roads" / "roads.geojson"
    target.write_text(target.read_text().replace("road_class", "road_klass"))
    with pytest.raises(BundleError) as excinfo:
        read_bundle(directory)
    assert "checksum mismatch" in str(excinfo.value)


def test_unrecognised_manifest_version_is_refused(bundle, tmp_path):
    directory = write_bundle(bundle, tmp_path / "bundle")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = "99.0.0"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(BundleError) as excinfo:
        read_bundle(directory)
    assert "refusing to guess" in str(excinfo.value)


def test_write_refuses_to_overwrite_without_force(bundle, tmp_path):
    directory = write_bundle(bundle, tmp_path / "bundle")
    with pytest.raises(BundleError) as excinfo:
        write_bundle(bundle, directory)
    assert "overwrite=True" in str(excinfo.value)
    write_bundle(bundle, directory, overwrite=True)  # succeeds with the flag


def test_rebuild_is_byte_identical(config, tmp_path):
    # A-REP-2: a build is a pure function of (config, inputs). Identical arrays
    # on a rebuild is what makes "reproducible" a checkable claim.
    from wildfireguardian_data.provenance import sha256_array

    first = build_study_area(config)
    second = build_study_area(config)
    for name in first.layer_names:
        left, right = first.layer(name), second.layer(name)
        if hasattr(left, "data"):
            assert sha256_array(left.data) == sha256_array(right.data), name
        else:
            assert left.to_geojson()["features"] == right.to_geojson()["features"], name


def test_npz_fallback_round_trips_without_gdal(bundle, tmp_path):
    # docs/DECISIONS.md D-0003: a bundle can be written and read with no GDAL.
    directory = write_bundle(bundle, tmp_path / "npz_bundle", prefer_geotiff=False)
    manifest = json.loads((directory / "manifest.json").read_text())
    formats = {entry["format"] for entry in manifest["layers"] if entry["type"] == "raster"}
    assert formats == {"npz"}
    restored = read_bundle(directory)
    assert np.array_equal(
        restored.layer("dem").data, bundle.layer("dem").data, equal_nan=True
    )


def test_summary_is_json_safe_and_reports_gaps(bundle):
    summary = summarize_bundle(bundle)
    json.dumps(summary)
    assert summary["unknown_field_total"] >= 0
    assert summary["roads_qa_headline"]["components"] >= 1
    assert summary["population"]["settlements"] == 3
    assert summary["facilities"]["suitability_assessed"] == 0
    assert any("internal and unstable" in caveat for caveat in summary["caveats"])


def test_summary_from_disk_matches_summary_in_memory(bundle, tmp_path):
    directory = write_bundle(bundle, tmp_path / "bundle")
    in_memory = summarize_bundle(bundle)
    from_disk = summarize_bundle(read_bundle(directory))
    assert from_disk["roads_qa_headline"] == in_memory["roads_qa_headline"]
    assert from_disk["population"]["population_total_sum"] == (
        in_memory["population"]["population_total_sum"]
    )
    assert from_disk["facilities"]["by_kind"] == in_memory["facilities"]["by_kind"]


def test_bundle_says_it_is_synthetic_in_its_own_id_and_layers(bundle):
    from wildfireguardian_data import DataClass

    assert "synthetic" in bundle.study_area_id
    for layer in bundle.all_layers():
        assert layer.provenance.data_class is DataClass.SYNTHETIC


def test_percent_slope_config_path(tmp_path):
    # `slope_unit: percent` is a config option, so it needs a test: the layer is
    # named after its unit, must survive the round trip, and must agree with
    # 100*tan(degrees) from an otherwise identical degree build.
    import math

    import yaml

    payload = yaml.safe_load(open(CONFIG_PATH))
    payload["study_area_id"] = "percent_slope_synthetic_test"
    payload["terrain"]["slope_unit"] = "percent"
    percent_config = tmp_path / "percent.yaml"
    percent_config.write_text(yaml.safe_dump(payload))

    percent_bundle = build_study_area(StudyAreaConfig.from_yaml(percent_config))
    assert "slope_percent" in percent_bundle.layer_names

    degree_bundle = build_study_area(StudyAreaConfig.from_yaml(CONFIG_PATH))
    degrees = degree_bundle.terrain.slope.data
    percent = percent_bundle.terrain.slope.data
    finite = np.isfinite(degrees) & np.isfinite(percent)
    expected = 100.0 * np.tan(np.radians(degrees[finite].astype(np.float64)))
    assert percent[finite] == pytest.approx(expected, abs=1e-4)

    # And the layer is still discoverable after a write/read round trip.
    restored = read_bundle(write_bundle(percent_bundle, tmp_path / "percent_bundle"))
    assert restored.terrain.slope is not None
    assert restored.terrain.slope.name == "slope_percent"


def test_resampling_within_one_crs_is_refused_rather_than_implicit(tmp_path):
    # F-BND-6: a source already in the analysis CRS at a different resolution
    # than requested must raise, not resample. Resampling changes every value,
    # and doing it because two config numbers disagreed would leave nothing in
    # the output to show which estimator was used.
    import yaml

    from wildfireguardian_data.errors import ConfigError

    payload = yaml.safe_load(open(CONFIG_PATH))
    payload["study_area_id"] = "implicit_resample_synthetic_test"
    payload["terrain"]["target_resolution_m"] = 10.0  # source fixture is 30 m
    config_path = tmp_path / "resample.yaml"
    config_path.write_text(yaml.safe_dump(payload))

    with pytest.raises(ConfigError) as excinfo:
        build_study_area(StudyAreaConfig.from_yaml(config_path))
    assert "not performed implicitly" in str(excinfo.value)
