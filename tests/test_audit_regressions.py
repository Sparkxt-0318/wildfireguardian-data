"""Regression tests for the findings of the phase-1 scientific audit.

Each test names the finding it pins. They are grouped here rather than spread
across the suite so the audit's conclusions stay checkable as a set: these are
the specific ways this package was found to state something its data did not
support.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
from helpers import make_provenance, make_raster, planar_surface
from shapely.geometry import Point, Polygon

from wildfireguardian_data import (
    DataClass,
    RasterKind,
    SourceRecord,
    TemporalProvenance,
)
from wildfireguardian_data.errors import (
    ConfigError,
    CRSMismatchError,
    IngestError,
    RasterAlignmentError,
    RasterGeometryError,
)

rasterio = pytest.importorskip("rasterio")


def _write_geotiff(path, array, *, crs="EPSG:5187", cell=30.0, unit=None, nodata=None):
    """Write a small GeoTIFF directly with rasterio, bypassing this package."""
    from affine import Affine

    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": array.dtype,
        "crs": crs,
        "transform": Affine(cell, 0.0, 226_000.0, 0.0, -cell, 483_000.0),
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(array, 1)
        if unit is not None:
            dataset.set_band_unit(1, unit)
    return path


# --------------------------------------------------------------------------- #
# S1 — a DEM's vertical unit is never assumed
# --------------------------------------------------------------------------- #
def test_read_geotiff_requires_an_explicit_value_unit(tmp_path):
    from wildfireguardian_data.terrain import read_geotiff

    path = _write_geotiff(tmp_path / "dem.tif", np.full((5, 5), 400.0, dtype=np.float32))
    with pytest.raises(TypeError):
        # No default: a metre default is right most of the time and
        # catastrophic the rest, and A-TER-8's enforcement downstream cannot
        # fire on a layer mislabelled at ingest.
        read_geotiff(  # type: ignore[call-arg]
            path,
            name="dem",
            source=SourceRecord(name="test", source_date="2023"),
            data_class=DataClass.OBSERVED,
            temporal_class=TemporalProvenance.STATIC,
        )


def test_read_geotiff_refuses_a_unit_that_contradicts_the_file(tmp_path):
    from wildfireguardian_data.terrain import read_geotiff

    # Elevations in feet, and the file says so.
    path = _write_geotiff(
        tmp_path / "feet.tif", np.full((5, 5), 1300.0, dtype=np.float32), unit="ft"
    )
    with pytest.raises(IngestError) as excinfo:
        read_geotiff(
            path,
            name="dem",
            source=SourceRecord(name="test", source_date="2023"),
            data_class=DataClass.OBSERVED,
            temporal_class=TemporalProvenance.STATIC,
            value_unit="m",
        )
    message = str(excinfo.value)
    assert "declares band unit" in message
    assert "3.28" in message  # the consequence is stated, not just the mismatch


def test_read_geotiff_records_agreement_with_the_file_unit(tmp_path):
    from wildfireguardian_data.terrain import read_geotiff

    path = _write_geotiff(
        tmp_path / "metres.tif", np.full((5, 5), 400.0, dtype=np.float32), unit="m"
    )
    layer = read_geotiff(
        path,
        name="dem",
        source=SourceRecord(name="test", source_date="2023"),
        data_class=DataClass.OBSERVED,
        temporal_class=TemporalProvenance.STATIC,
        value_unit="m",
    )
    assert "agrees with the declared value_unit" in layer.provenance.transformations[0].notes


def test_a_foot_dem_declared_as_feet_is_refused_by_slope(tmp_path):
    # The whole point of S1: once the unit is honest, A-TER-8 can do its job.
    from wildfireguardian_data.errors import UnitMismatchError
    from wildfireguardian_data.terrain import read_geotiff, slope

    path = _write_geotiff(
        tmp_path / "feet.tif", planar_surface(shape=(7, 7)).astype(np.float32), unit="ft"
    )
    layer = read_geotiff(
        path,
        name="dem",
        source=SourceRecord(name="test", source_date="2023"),
        data_class=DataClass.OBSERVED,
        temporal_class=TemporalProvenance.STATIC,
        value_unit="ft",
    )
    with pytest.raises(UnitMismatchError):
        slope(layer)


# --------------------------------------------------------------------------- #
# S3 — sibling rasters are co-registered
# --------------------------------------------------------------------------- #
def test_reproject_onto_an_existing_grid_lands_exactly_on_it():
    from wildfireguardian_data.terrain import reproject_raster

    dem = make_raster(planar_surface(shape=(20, 20)), name="dem", origin=(226_000.0, 477_000.0))
    other = make_raster(
        np.ones((20, 20), dtype=np.int32),
        name="fuels",
        kind=RasterKind.CATEGORICAL,
        value_unit="class",
        nodata=255,
        crs="EPSG:5186",
    )
    warped = reproject_raster(
        other, dem.crs, target_grid=dem.transform, target_shape=dem.shape
    )
    assert warped.transform == dem.transform
    assert warped.shape == dem.shape
    assert warped.provenance.transformations[-1].parameters["target_grid_supplied"] is True


def test_target_grid_and_dst_resolution_are_mutually_exclusive():
    from wildfireguardian_data.terrain import reproject_raster

    dem = make_raster(planar_surface(shape=(8, 8)))
    with pytest.raises(ConfigError):
        reproject_raster(
            dem, "EPSG:32652", target_grid=dem.transform, target_shape=dem.shape,
            dst_resolution=30.0,
        )


def test_pipeline_co_registers_a_fuel_raster_arriving_in_another_crs(tmp_path):
    """A fuel layer from a different Korean belt must land on the DEM's grid.

    Reprojecting it independently derives a target grid from its own extent, so
    the origins end up offset by a fraction of a cell and every fuel value is
    displaced relative to the DEM cell a consumer indexes it by (A-RAS-5).
    """
    import yaml

    from wildfireguardian_data.fuels import SYNTHETIC_DEMO_SCHEME
    from wildfireguardian_data.study_area import StudyAreaConfig, build_study_area
    from wildfireguardian_data.validation import validate_bundle

    # A fuel GeoTIFF on EPSG:5186 (the central belt) covering the study area.
    codes = np.full((260, 260), 4, dtype=np.int32)
    from affine import Affine
    from pyproj import Transformer

    transformer = Transformer.from_crs("EPSG:5187", "EPSG:5186", always_xy=True)
    x0, y0 = transformer.transform(225_000.0, 484_000.0)
    fuel_path = tmp_path / "fuels_5186.tif"
    with rasterio.open(
        fuel_path,
        "w",
        driver="GTiff",
        height=codes.shape[0],
        width=codes.shape[1],
        count=1,
        dtype=codes.dtype,
        crs="EPSG:5186",
        transform=Affine(30.0, 0.0, x0, 0.0, -30.0, y0),
        nodata=SYNTHETIC_DEMO_SCHEME.nodata_code,
    ) as dataset:
        dataset.write(codes, 1)

    payload = yaml.safe_load(open("configs/uljin_valley_synthetic.yaml"))
    payload["study_area_id"] = "coregistration_synthetic_test"
    payload["fuels"] = {
        "source": {
            "kind": "geotiff",
            "path": str(fuel_path),
            "temporal_class": "annual",
            "temporal_reference": "2023",
            "source_name": "test fuel raster on EPSG:5186",
            "data_class": "synthetic",
        },
        "scheme": "synthetic_demo_v1",
    }
    config_path = tmp_path / "coreg.yaml"
    config_path.write_text(yaml.safe_dump(payload))

    bundle = build_study_area(StudyAreaConfig.from_yaml(config_path))
    dem = bundle.terrain.dem
    fuels = bundle.fuels.layer
    assert fuels.transform == dem.transform
    assert fuels.shape == dem.shape
    # And no alignment finding, because there is nothing to report.
    report = validate_bundle(bundle)
    assert not {"RAS-006", "RAS-007"} & {f.code for f in report.findings}


# --------------------------------------------------------------------------- #
# S4 — resolution_unit comes from the CRS
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("crs", "expected"),
    [("EPSG:5187", "metre"), ("EPSG:4326", "degree"), ("EPSG:2225", "US survey foot")],
)
def test_resolution_unit_is_read_from_the_crs(crs, expected):
    from wildfireguardian_data.terrain.io import resolution_unit_text

    assert resolution_unit_text(crs) == expected


def test_reprojecting_to_a_geographic_crs_records_degrees_not_metres():
    from wildfireguardian_data.terrain import reproject_raster

    layer = make_raster(planar_surface(shape=(12, 12)))
    out = reproject_raster(layer, "EPSG:4326")
    assert out.provenance.resolution_unit == "degree"
    assert out.provenance.spatial_resolution[0] < 1.0  # degrees, not metres


def test_shipped_geographic_fixture_records_degrees():
    from wildfireguardian_data.fixtures import incompatible_crs_pair

    pair = incompatible_crs_pair()
    assert pair.geographic_raster.provenance.resolution_unit == "degree"
    assert pair.projected_raster.provenance.resolution_unit == "metre"


# --------------------------------------------------------------------------- #
# S5 — temporal_class is never defaulted for a local file
# --------------------------------------------------------------------------- #
def test_local_file_source_must_declare_its_temporal_class(tmp_path):
    import yaml

    from wildfireguardian_data.study_area import StudyAreaConfig, build_study_area

    path = _write_geotiff(tmp_path / "dem.tif", np.full((300, 300), 400.0, dtype=np.float32))
    payload = {
        "study_area_id": "undeclared_temporal_test",
        "crs": "EPSG:5187",
        "bounds": [226_100.0, 477_100.0, 227_000.0, 478_000.0],
        "terrain": {
            "source": {"kind": "geotiff", "path": str(path), "value_unit": "m"},
        },
    }
    config_path = tmp_path / "c.yaml"
    config_path.write_text(yaml.safe_dump(payload))
    with pytest.raises(ConfigError) as excinfo:
        build_study_area(StudyAreaConfig.from_yaml(config_path))
    assert "temporal_class" in str(excinfo.value)
    assert "retrospective" in str(excinfo.value)  # the options are named


def test_local_raster_source_must_declare_its_value_unit(tmp_path):
    import yaml

    from wildfireguardian_data.study_area import StudyAreaConfig, build_study_area

    path = _write_geotiff(tmp_path / "dem.tif", np.full((300, 300), 400.0, dtype=np.float32))
    payload = {
        "study_area_id": "undeclared_unit_test",
        "crs": "EPSG:5187",
        "bounds": [226_100.0, 477_100.0, 227_000.0, 478_000.0],
        "terrain": {
            "source": {"kind": "geotiff", "path": str(path), "temporal_class": "static"},
        },
    }
    config_path = tmp_path / "c.yaml"
    config_path.write_text(yaml.safe_dump(payload))
    with pytest.raises(ConfigError) as excinfo:
        build_study_area(StudyAreaConfig.from_yaml(config_path))
    assert "value_unit" in str(excinfo.value)


def test_a_retrospective_layer_keeps_its_declared_class(tmp_path):
    import yaml

    from wildfireguardian_data.study_area import StudyAreaConfig, build_study_area

    path = _write_geotiff(tmp_path / "dem.tif", np.full((300, 300), 400.0, dtype=np.float32))
    payload = {
        "study_area_id": "retrospective_declared_test",
        "crs": "EPSG:5187",
        "bounds": [226_100.0, 477_100.0, 227_000.0, 478_000.0],
        "terrain": {
            "source": {
                "kind": "geotiff",
                "path": str(path),
                "value_unit": "m",
                "temporal_class": "retrospective",
                "temporal_reference": "2022",
            },
            "derivatives": [],
        },
    }
    config_path = tmp_path / "c.yaml"
    config_path.write_text(yaml.safe_dump(payload))
    bundle = build_study_area(StudyAreaConfig.from_yaml(config_path))
    assert bundle.terrain.dem.provenance.temporal_class is TemporalProvenance.RETROSPECTIVE


# --------------------------------------------------------------------------- #
# S8 — nodata must be representable in the array's dtype
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("dtype", "nodata"), [("uint8", -9999), ("int16", 70_000), ("uint8", 300)]
)
def test_out_of_range_integer_nodata_is_refused(dtype, nodata):
    # `data != nodata` would be vacuously true everywhere: the layer would claim
    # full coverage while its missing cells read as real values.
    with pytest.raises(RasterGeometryError) as excinfo:
        make_raster(
            np.ones((4, 4), dtype=dtype),
            nodata=nodata,
            kind=RasterKind.CATEGORICAL,
            value_unit="class",
        )
    assert "outside the range" in str(excinfo.value)


def test_in_range_integer_nodata_is_accepted():
    layer = make_raster(
        np.array([[1, 255], [2, 3]], dtype="uint8"),
        nodata=255,
        kind=RasterKind.CATEGORICAL,
        value_unit="class",
    )
    assert layer.missing_count == 1


# --------------------------------------------------------------------------- #
# S10 — terrain_statistics is a cross-layer operation
# --------------------------------------------------------------------------- #
def test_terrain_statistics_refuses_layers_from_another_crs():
    from wildfireguardian_data.terrain import terrain_statistics

    dem = make_raster(planar_surface(shape=(10, 10)), name="dem")
    alien = make_raster(
        np.full((10, 10), 12.0), name="slope_alien", crs="EPSG:5179", value_unit="deg"
    )
    with pytest.raises(CRSMismatchError):
        terrain_statistics(dem, slope_layer=alien)


def test_terrain_statistics_refuses_layers_off_the_dem_grid():
    from wildfireguardian_data.terrain import terrain_statistics

    dem = make_raster(planar_surface(shape=(10, 10)), name="dem")
    offset = make_raster(
        np.full((10, 10), 12.0), name="slope_offset", value_unit="deg", cell_size_m=10.0
    )
    with pytest.raises(RasterAlignmentError):
        terrain_statistics(dem, slope_layer=offset)


def test_relief_carries_its_unit_as_a_field_not_in_the_key():
    from wildfireguardian_data.terrain import terrain_statistics

    dem = make_raster(planar_surface(shape=(8, 8)), name="dem")
    stats = terrain_statistics(dem)
    assert "relief_m" not in stats
    assert stats["relief_unit"] == "m"
    assert stats["cell_size_unit"] == "metre"


# --------------------------------------------------------------------------- #
# S11 — a CRS with no authority code survives a round trip
# --------------------------------------------------------------------------- #
def test_custom_projection_round_trips_through_geojson(tmp_path):
    from wildfireguardian_data.crs import crs_equal
    from wildfireguardian_data.vector import Feature, VectorLayer, read_geojson, write_geojson

    custom = (
        "+proj=tmerc +lat_0=37.1234 +lon_0=129.1234 +k=0.9998 +x_0=200000 "
        "+y_0=500000 +datum=WGS84 +units=m +no_defs"
    )
    layer = VectorLayer(
        name="custom",
        features=(Feature(Point(200_100.0, 500_100.0), {"id": 1}),),
        crs=custom,
        provenance=make_provenance("custom", value_unit="not_applicable"),
    )
    path = write_geojson(layer, tmp_path / "custom.geojson")
    restored = read_geojson(path, name="custom", provenance=layer.provenance)
    assert restored.crs is not None
    assert crs_equal(restored.crs, layer.crs)


def test_parse_crs_accepts_what_crs_to_string_emits():
    from wildfireguardian_data.crs import crs_to_string, parse_crs

    custom = parse_crs("+proj=tmerc +lat_0=37 +lon_0=129 +datum=WGS84 +units=m +no_defs")
    text = crs_to_string(custom)
    assert text.startswith("wkt:")
    assert parse_crs(text) is not None


# --------------------------------------------------------------------------- #
# S12 — polygons that burn nothing are reported
# --------------------------------------------------------------------------- #
def test_sub_cell_fuel_polygon_that_burns_nothing_is_recorded():
    from wildfireguardian_data.fuels import SYNTHETIC_DEMO_SCHEME, rasterize_fuel_vector
    from wildfireguardian_data.vector import Feature, VectorLayer

    template = make_raster(np.zeros((10, 10)), origin=(0.0, 0.0), cell_size_m=30.0)
    # Cell centres are at 15, 45, 75 ...; this polygon straddles the 30 m
    # boundary and contains no centre, so it burns nothing.
    tiny = Polygon([(25, 265), (35, 265), (35, 275), (25, 275)])
    layer = VectorLayer(
        name="fuel_polys",
        features=(Feature(tiny, {"fuel_code": 4}),),
        crs=template.crs,
        provenance=make_provenance("fuel_polys", value_unit="class"),
    )
    burned = rasterize_fuel_vector(
        layer,
        name="fuels",
        class_property="fuel_code",
        scheme=SYNTHETIC_DEMO_SCHEME,
        template=template,
    )
    parameters = burned.provenance.transformations[-1].parameters
    assert parameters["cells_burned"] == 0
    assert parameters["codes_that_burned_no_cells"] == [4]
    assert "features_burned" not in parameters  # the misleading key is gone
    assert "burned no cells at all" in burned.provenance.transformations[-1].notes


def test_polygons_that_do_burn_report_cells_not_shapes():
    from wildfireguardian_data.fuels import SYNTHETIC_DEMO_SCHEME, rasterize_fuel_vector
    from wildfireguardian_data.vector import Feature, VectorLayer

    template = make_raster(np.zeros((10, 10)), origin=(0.0, 0.0), cell_size_m=30.0)
    big = Polygon([(0, 150), (90, 150), (90, 300), (0, 300)])
    layer = VectorLayer(
        name="fuel_polys",
        features=(Feature(big, {"fuel_code": 5}),),
        crs=template.crs,
        provenance=make_provenance("fuel_polys", value_unit="class"),
    )
    burned = rasterize_fuel_vector(
        layer,
        name="fuels",
        class_property="fuel_code",
        scheme=SYNTHETIC_DEMO_SCHEME,
        template=template,
    )
    parameters = burned.provenance.transformations[-1].parameters
    assert parameters["cells_burned"] == int((burned.data == 5).sum()) > 0
    assert parameters["codes_that_burned_no_cells"] == []


# --------------------------------------------------------------------------- #
# S13 — settlement points need a CRS
# --------------------------------------------------------------------------- #
def test_settlement_matching_refuses_points_from_another_crs():
    from test_road_graph_qa import road_layer

    from wildfireguardian_data.roads import build_road_graph, identify_settlement_nodes

    graph = build_road_graph(road_layer([("a", [(0, 1500), (1000, 1500)])]))
    with pytest.raises(CRSMismatchError):
        identify_settlement_nodes(
            graph, [("v1", Point(129.4004, 36.9930))], settlements_crs="EPSG:4326"
        )


def test_settlement_report_states_whether_the_crs_was_checked():
    from test_road_graph_qa import road_layer

    from wildfireguardian_data.roads import build_road_graph, identify_settlement_nodes

    graph = build_road_graph(road_layer([("a", [(0, 1500), (1000, 1500)])]))
    unchecked = identify_settlement_nodes(graph, [("v1", Point(500, 1500))])
    checked = identify_settlement_nodes(
        graph, [("v1", Point(500, 1500))], settlements_crs="EPSG:5187"
    )
    assert unchecked.to_dict()["crs_checked"] is False
    assert checked.to_dict()["crs_checked"] is True


# --------------------------------------------------------------------------- #
# S16 — the documented behaviour of a vertical slope
# --------------------------------------------------------------------------- #
def test_vertical_slope_in_percent_is_large_and_finite_not_infinite():
    from wildfireguardian_data.units import SlopeUnit, convert_slope

    value = convert_slope(90.0, SlopeUnit.DEGREE, SlopeUnit.PERCENT)
    assert math.isfinite(value)  # NOT inf, whatever intuition says
    assert value > 1e15


# --------------------------------------------------------------------------- #
# S9 — licence obligations reach the manifest
# --------------------------------------------------------------------------- #
def test_manifest_aggregates_licences_and_surfaces_obligations(tmp_path):
    from wildfireguardian_data.study_area import (
        StudyAreaConfig,
        build_study_area,
        write_bundle,
    )

    bundle = build_study_area(
        StudyAreaConfig.from_yaml("configs/uljin_valley_synthetic.yaml")
    )
    # Give one layer a share-alike licence, as the real OSM-derived bundle has.
    dem = bundle.terrain.dem
    odbl = SourceRecord(
        name="OpenStreetMap (test)",
        licence="Open Database License (ODbL) 1.0",
        source_date="not_applicable",
    )
    bundle.terrain.dem = dem.with_data(
        dem.data, provenance=dem.provenance.with_updates(sources=(odbl,))
    )
    directory = write_bundle(bundle, tmp_path / "bundle")
    manifest = json.loads((directory / "manifest.json").read_text())

    assert manifest["licences"], "per-layer licences must reach the manifest"
    assert any("ODbL" in entry["licence"] for entry in manifest["licences"])
    # And the obligation is in the caveats, where the stability and safety
    # caveats already live -- INTERFACES.md names the manifest as step 1.
    assert any("share-alike" in caveat for caveat in manifest["caveats"])


def test_repository_has_a_licence_file_distinguishing_code_from_data():
    from pathlib import Path

    text = Path("LICENSE").read_text()
    assert "MIT License" in text
    assert "DATA IS NOT COVERED BY THE ABOVE" in text
    assert "ODbL" in text
    assert "ESA / Copernicus" in text


# --------------------------------------------------------------------------- #
# Verification follow-up: the grid-north/true-north convergence is quantified
# --------------------------------------------------------------------------- #
def test_meridian_convergence_matches_the_closed_form():
    # Checked against atan(tan(dlon) * sin(lat)) rather than against itself.
    from pyproj import Transformer

    from wildfireguardian_data.crs import meridian_convergence_deg

    transformer = Transformer.from_crs("EPSG:5187", "EPSG:4326", always_xy=True)
    for x, y in [(227_500.0, 478_500.0), (231_500.0, 482_500.0)]:
        longitude, latitude = transformer.transform(x, y)
        expected = math.degrees(
            math.atan(
                math.tan(math.radians(longitude - 129.0)) * math.sin(math.radians(latitude))
            )
        )
        assert meridian_convergence_deg("EPSG:5187", x, y) == pytest.approx(
            expected, abs=1e-6
        )


def test_convergence_is_small_on_the_right_belt_and_larger_nationwide():
    # The documented magnitudes in FAILURE_MODES.md F-TER-5.
    from pyproj import Transformer

    from wildfireguardian_data.crs import meridian_convergence_deg

    belt = meridian_convergence_deg("EPSG:5187", 229_500.0, 480_500.0)
    assert 0.15 < belt < 0.25

    to_5179 = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    x, y = to_5179.transform(129.32, 36.93)
    nationwide = meridian_convergence_deg("EPSG:5179", x, y)
    assert 1.0 < nationwide < 1.2


def test_convergence_is_undefined_for_a_geographic_crs():
    from wildfireguardian_data.crs import meridian_convergence_deg
    from wildfireguardian_data.errors import GeographicCRSError

    with pytest.raises(GeographicCRSError):
        meridian_convergence_deg("EPSG:4326", 129.32, 36.93)
