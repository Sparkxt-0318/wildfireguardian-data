"""The raster model's geometric invariants."""

from __future__ import annotations

import numpy as np
import pytest

from helpers import make_provenance, make_raster, planar_surface
from wildfireguardian_data import GridTransform, RasterKind, RasterLayer
from wildfireguardian_data.errors import RasterGeometryError


def test_grid_transform_rejects_rotation():
    from affine import Affine

    with pytest.raises(RasterGeometryError) as excinfo:
        GridTransform.from_affine(Affine(30.0, 0.5, 0.0, 0.0, -30.0, 100.0))
    assert "rotation" in str(excinfo.value)


def test_grid_transform_rejects_south_up_grids():
    from affine import Affine

    with pytest.raises(RasterGeometryError) as excinfo:
        GridTransform.from_affine(Affine(30.0, 0.0, 0.0, 0.0, 30.0, 100.0))
    assert "south-up" in str(excinfo.value)


def test_grid_transform_rejects_negative_cell_size():
    with pytest.raises(RasterGeometryError):
        GridTransform(0.0, 100.0, 30.0, -30.0)


def test_gdal_geotransform_round_trip():
    transform = GridTransform(226_000.0, 483_000.0, 30.0, 30.0)
    assert transform.to_gdal() == (226_000.0, 30.0, 0.0, 483_000.0, 0.0, -30.0)
    assert GridTransform.from_gdal(transform.to_gdal()) == transform


def test_cell_centre_is_half_a_cell_from_the_corner():
    # An off-by-half-a-cell error is a silent 15 m shift on a 30 m DEM.
    transform = GridTransform(1000.0, 2000.0, 30.0, 30.0)
    assert transform.xy(0, 0, center=False) == (1000.0, 2000.0)
    assert transform.xy(0, 0, center=True) == (1015.0, 1985.0)


def test_rowcol_uses_floor_and_does_not_clamp():
    transform = GridTransform(1000.0, 2000.0, 30.0, 30.0)
    assert transform.rowcol(1000.0, 2000.0) == (0, 0)
    assert transform.rowcol(1029.9, 1970.1) == (0, 0)
    assert transform.rowcol(1030.0, 1970.0) == (1, 1)
    # Out of range indices are returned, not clamped: clamping would silently
    # sample the wrong cell at the edge.
    assert transform.rowcol(900.0, 2100.0) == (-4, -4)


def test_bounds_follow_from_shape_and_transform():
    layer = make_raster(np.zeros((10, 20)), cell_size_m=30.0, origin=(0.0, 0.0))
    bounds = layer.bounds
    assert bounds.width == pytest.approx(600.0)
    assert bounds.height == pytest.approx(300.0)
    assert layer.resolution == (30.0, 30.0)


def test_layer_rejects_non_2d_data():
    with pytest.raises(RasterGeometryError):
        make_raster(np.zeros((2, 3, 4)))


def test_layer_rejects_empty_data():
    with pytest.raises(RasterGeometryError):
        make_raster(np.zeros((0, 3)))


def test_layer_name_must_match_provenance():
    with pytest.raises(RasterGeometryError) as excinfo:
        RasterLayer(
            name="dem",
            data=np.zeros((3, 3)),
            transform=GridTransform(0, 100, 30, 30),
            crs="EPSG:5187",
            nodata=np.nan,
            kind=RasterKind.CONTINUOUS,
            value_unit="m",
            provenance=make_provenance("something_else"),
        )
    assert "does not match provenance" in str(excinfo.value)


def test_cell_area_requires_a_metre_crs():
    from wildfireguardian_data.errors import GeographicCRSError

    layer = make_raster(np.zeros((3, 3)), crs="EPSG:4326", cell_size_m=1 / 3600)
    with pytest.raises(GeographicCRSError):
        layer.cell_area()


def test_derived_carries_the_new_crs_not_the_parents():
    # Regression guard: a reprojected layer whose crs field still named the
    # source CRS would be exactly the silent error this package prevents.
    from wildfireguardian_data.crs import crs_to_string
    from wildfireguardian_data.provenance import Transformation

    layer = make_raster(planar_surface(shape=(4, 4)))
    derived = layer.derived(
        np.zeros((4, 4)),
        name="reprojected",
        transformation=Transformation(operation="test_reproject"),
        crs="EPSG:32652",
    )
    assert crs_to_string(derived.crs) == "EPSG:32652"
    assert derived.provenance.output_crs == "EPSG:32652"


def test_describe_is_json_safe():
    import json

    layer = make_raster(planar_surface(shape=(4, 4)))
    json.dumps(layer.describe())
