"""Clipping keeps the grid; reprojection is explicit and recorded."""

from __future__ import annotations

import numpy as np
import pytest
from helpers import make_raster, planar_surface

from wildfireguardian_data.bounds import Bounds
from wildfireguardian_data.errors import ConfigError, MissingDataError, RasterGeometryError
from wildfireguardian_data.terrain import clip_raster, slope, snap_bounds_to_grid

pytest_geo = pytest.importorskip("rasterio")


def test_clip_snaps_outward_and_keeps_the_grid_origin_modulo_cell_size():
    layer = make_raster(planar_surface(shape=(20, 20)), origin=(226_000.0, 477_000.0))
    # A box deliberately offset from the cell boundaries by 7 m.
    request = Bounds(226_107.0, 477_107.0, 226_407.0, 477_407.0, crs=layer.crs)
    clipped = clip_raster(layer, request)

    # Grid origin stays on the parent's lattice: the clip cannot introduce a
    # sub-cell shift.
    dx = (clipped.transform.x_origin - layer.transform.x_origin) % layer.transform.x_size
    dy = (clipped.transform.y_origin - layer.transform.y_origin) % layer.transform.y_size
    assert dx == pytest.approx(0.0)
    assert dy == pytest.approx(0.0)
    assert clipped.transform.x_size == layer.transform.x_size

    # Outward snapping: the result fully contains the request.
    assert clipped.bounds.min_x <= request.min_x
    assert clipped.bounds.min_y <= request.min_y
    assert clipped.bounds.max_x >= request.max_x
    assert clipped.bounds.max_y >= request.max_y


def test_clip_buffer_adds_cells_on_every_side():
    layer = make_raster(planar_surface(shape=(30, 30)), origin=(0.0, 0.0))
    request = Bounds(300.0, 300.0, 600.0, 600.0, crs=layer.crs)
    plain = clip_raster(layer, request)
    buffered = clip_raster(layer, request, buffer_cells=2)
    assert buffered.shape[0] == plain.shape[0] + 4
    assert buffered.shape[1] == plain.shape[1] + 4


def test_clip_values_are_the_parents_values_unchanged():
    layer = make_raster(planar_surface(shape=(20, 20)), origin=(0.0, 0.0))
    clipped = clip_raster(layer, Bounds(90.0, 90.0, 300.0, 300.0, crs=layer.crs))
    # Locate the clip inside the parent and compare the arrays element-wise.
    row, col = layer.transform.rowcol(*clipped.transform.xy(0, 0, center=True))
    window = layer.data[row : row + clipped.height, col : col + clipped.width]
    assert np.array_equal(window, clipped.data, equal_nan=True)


def test_partial_clip_is_recorded_and_can_be_made_an_error():
    layer = make_raster(planar_surface(shape=(10, 10)), origin=(0.0, 0.0))
    too_big = Bounds(-500.0, -500.0, 900.0, 900.0, crs=layer.crs)

    clipped = clip_raster(layer, too_big, allow_partial=True)
    parameters = clipped.provenance.transformations[-1].parameters
    assert parameters["requested_window_truncated"] is True

    with pytest.raises(RasterGeometryError) as excinfo:
        clip_raster(layer, too_big, allow_partial=False)
    assert "not fully covered" in str(excinfo.value)


def test_clip_of_a_degenerate_box_is_refused():
    layer = make_raster(planar_surface(shape=(10, 10)), origin=(0.0, 0.0))
    with pytest.raises(ConfigError):
        clip_raster(layer, Bounds(100.0, 100.0, 100.0, 300.0, crs=layer.crs))


def test_snap_bounds_to_grid_returns_unclamped_indices():
    from wildfireguardian_data import GridTransform

    transform = GridTransform(0.0, 300.0, 30.0, 30.0)
    window = snap_bounds_to_grid(Bounds(-60.0, 0.0, 60.0, 120.0, crs="EPSG:5187"), transform)
    row_start, row_stop, col_start, col_stop = window
    assert col_start == -2
    assert row_stop == 10


def test_reproject_records_resampling_and_resolution_and_changes_crs():
    from wildfireguardian_data.crs import crs_to_string
    from wildfireguardian_data.terrain import reproject_raster

    layer = make_raster(planar_surface(shape=(20, 20)))
    out = reproject_raster(layer, "EPSG:32652", dst_resolution=30.0)

    assert crs_to_string(out.crs) == "EPSG:32652"
    assert out.provenance.output_crs == "EPSG:32652"
    parameters = out.provenance.transformations[-1].parameters
    assert parameters["resampling"] == "bilinear"
    assert parameters["dst_resolution"] == [30.0, 30.0]
    assert parameters["src_crs"] == "EPSG:5187"
    assert out.resolution == (30.0, 30.0)


def test_reproject_of_the_same_crs_is_a_no_op():
    from wildfireguardian_data.terrain import reproject_raster

    layer = make_raster(planar_surface(shape=(8, 8)))
    assert reproject_raster(layer, "EPSG:5187") is layer


def test_reproject_preserves_missing_cells_as_missing():
    from wildfireguardian_data.terrain import reproject_raster

    data = planar_surface(shape=(20, 20))
    data[8:12, 8:12] = np.nan
    layer = make_raster(data, nodata=np.nan)
    out = reproject_raster(layer, "EPSG:32652", dst_resolution=30.0)
    # The void survives, and no cell acquired a value from averaging a sentinel.
    assert out.missing_count >= 16
    assert np.isfinite(out.data[out.valid_mask()]).all()


def test_reproject_refuses_averaging_a_categorical_layer():
    from wildfireguardian_data import RasterKind
    from wildfireguardian_data.terrain import reproject_raster

    layer = make_raster(
        np.ones((8, 8), dtype=np.int32),
        kind=RasterKind.CATEGORICAL,
        value_unit="class",
        nodata=255,
    )
    with pytest.raises(ConfigError) as excinfo:
        reproject_raster(layer, "EPSG:32652", resampling="bilinear")
    assert "average" in str(excinfo.value)


def test_categorical_reproject_defaults_to_nearest():
    from wildfireguardian_data import RasterKind
    from wildfireguardian_data.terrain import reproject_raster

    data = np.tile(np.array([[1, 5]], dtype=np.int32), (8, 4))
    layer = make_raster(data, kind=RasterKind.CATEGORICAL, value_unit="class", nodata=255)
    out = reproject_raster(layer, "EPSG:32652", dst_resolution=30.0)
    assert out.provenance.transformations[-1].parameters["resampling"] == "nearest"
    # No invented intermediate class: only 1, 5 and the nodata code appear.
    assert set(np.unique(out.data)).issubset({1, 5, 255})


def test_integer_layer_without_nodata_refuses_to_reproject():
    from wildfireguardian_data import RasterKind
    from wildfireguardian_data.terrain import reproject_raster

    layer = make_raster(
        np.ones((8, 8), dtype=np.int32),
        kind=RasterKind.CATEGORICAL,
        value_unit="class",
        nodata=None,
    )
    with pytest.raises(MissingDataError) as excinfo:
        reproject_raster(layer, "EPSG:32652")
    assert "will not default it to 0" in str(excinfo.value)


def test_clip_then_slope_covers_the_requested_area_when_buffered():
    layer = make_raster(planar_surface(shape=(30, 30)), origin=(0.0, 0.0))
    request = Bounds(300.0, 300.0, 600.0, 600.0, crs=layer.crs)
    buffered = clip_raster(layer, request, buffer_cells=1)
    slope_layer = slope(buffered)
    # Every cell of the requested box has a slope, because the buffer supplied
    # the neighbourhood the estimator needs (docs/DECISIONS.md D-0005).
    inner = slope_layer.data[1:-1, 1:-1]
    assert np.isfinite(inner).all()
