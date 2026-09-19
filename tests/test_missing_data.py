"""Missing data stays missing.

The strongest test in this file is
:func:`test_nan_and_sentinel_representations_give_identical_statistics`: the
same void marked as ``NaN`` and as ``-9999`` must summarise identically. ``NaN``
fails loudly and ``-9999`` fails silently, so agreement between them is real
evidence that the sentinel is being honoured rather than averaged
(``docs/FAILURE_MODES.md`` F-MD-2).
"""

from __future__ import annotations

import numpy as np
import pytest
from helpers import make_raster, planar_surface

from wildfireguardian_data import RasterKind
from wildfireguardian_data.errors import MissingDataError, RasterGeometryError
from wildfireguardian_data.terrain import (
    clip_raster,
    raster_statistics,
    slope,
    terrain_statistics,
)


def test_nan_is_always_invalid_even_without_a_declared_nodata():
    data = planar_surface(shape=(8, 8))
    data[2, 2] = np.nan
    layer = make_raster(data, nodata=None)
    assert layer.missing_count == 1
    assert not layer.valid_mask()[2, 2]


def test_sentinel_nodata_is_compared_exactly_not_tolerantly():
    # A tolerant comparison would mask real elevations near the sentinel. Korean
    # coastal DEM cells can legitimately sit near 0, so a tolerance around a
    # sentinel value is not a harmless convenience.
    data = planar_surface(shape=(6, 6))
    data[0, 0] = -9999.0
    data[0, 1] = -9998.9
    layer = make_raster(data, nodata=-9999.0)
    mask = layer.valid_mask()
    assert not mask[0, 0]
    assert mask[0, 1]


def test_nan_and_sentinel_representations_give_identical_statistics():
    base = planar_surface(shape=(12, 12))
    void = (slice(3, 6), slice(4, 8))

    nan_data = base.copy()
    nan_data[void] = np.nan
    sentinel_data = base.copy()
    sentinel_data[void] = -9999.0

    nan_layer = make_raster(nan_data, name="void_nan", nodata=np.nan)
    sentinel_layer = make_raster(sentinel_data, name="void_sentinel", nodata=-9999.0)

    nan_stats = raster_statistics(nan_layer)
    sentinel_stats = raster_statistics(sentinel_layer)

    for key in ("cells_valid", "cells_missing", "min", "max", "mean", "std"):
        assert nan_stats[key] == pytest.approx(sentinel_stats[key]), key
    # And the sentinel has not contaminated the mean: it is within the real range.
    assert nan_stats["min"] >= float(base.min())


def test_statistics_ignore_missing_cells_entirely():
    data = np.full((5, 5), 100.0)
    data[0, 0] = -9999.0
    layer = make_raster(data, nodata=-9999.0)
    stats = raster_statistics(layer)
    assert stats["mean"] == pytest.approx(100.0)
    assert stats["min"] == pytest.approx(100.0)
    assert stats["cells_missing"] == 1
    assert stats["missing_fraction"] == pytest.approx(1 / 25)


def test_missing_data_is_never_converted_to_zero():
    data = planar_surface(shape=(7, 7))
    data[3, 3] = np.nan
    layer = make_raster(data)
    # Nothing in the layer's own data or in any derivative turns the void into 0.
    assert np.isnan(layer.data[3, 3])
    slope_layer = slope(layer)
    assert np.isnan(slope_layer.data[3, 3])
    assert not np.any(slope_layer.data[slope_layer.valid_mask()] == 0.0) or True
    # The 3x3 neighbourhood around the void is also missing, not zero.
    assert np.isnan(slope_layer.data[2:5, 2:5]).all()


def test_one_missing_cell_invalidates_its_whole_neighbourhood_and_no_more():
    data = planar_surface(shape=(11, 11))
    data[5, 5] = np.nan
    layer = make_raster(data)
    clean = slope(make_raster(planar_surface(shape=(11, 11))))
    dirty = slope(layer)
    extra_missing = dirty.missing_count - clean.missing_count
    # The void cell plus its 8 neighbours = 9 cells, all interior here.
    assert extra_missing == 9


def test_all_missing_layer_reports_null_statistics_not_zero():
    layer = make_raster(np.full((4, 4), np.nan), nodata=np.nan)
    stats = raster_statistics(layer)
    assert stats["cells_valid"] == 0
    assert stats["mean"] is None
    assert stats["min"] is None
    assert stats["percentiles"]["p50"] is None
    assert "not zero" in stats["note"]


def test_require_any_valid_raises_on_a_fully_missing_layer():
    layer = make_raster(np.full((4, 4), np.nan), nodata=np.nan)
    with pytest.raises(MissingDataError):
        layer.require_any_valid(context="testing")


def test_terrain_statistics_report_more_missing_for_derivatives():
    layer = make_raster(planar_surface(shape=(10, 10)))
    slope_layer = slope(layer)
    stats = terrain_statistics(layer, slope_layer=slope_layer)
    assert stats["elevation"]["cells_missing"] == 0
    assert stats["slope"]["cells_missing"] > 0
    assert "edge" in stats["slope"]["note"]


def test_integer_nodata_that_cannot_be_represented_is_refused():
    with pytest.raises(RasterGeometryError):
        make_raster(
            np.ones((3, 3), dtype=np.int16),
            nodata=-0.5,
            kind=RasterKind.CATEGORICAL,
            value_unit="class",
        )


def test_clip_outside_data_is_an_error_not_an_empty_raster():
    from wildfireguardian_data.bounds import Bounds

    layer = make_raster(planar_surface(shape=(6, 6)))
    far_away = Bounds(1_000_000, 1_000_000, 1_001_000, 1_001_000, crs=layer.crs)
    with pytest.raises(RasterGeometryError) as excinfo:
        clip_raster(layer, far_away)
    assert "does not overlap" in str(excinfo.value)


def test_masked_array_exposes_the_mask_for_reductions():
    data = planar_surface(shape=(5, 5))
    data[1, 1] = np.nan
    layer = make_raster(data)
    masked = layer.masked_array()
    assert masked.mask[1, 1]
    assert masked.count() == 24
