"""Slope and aspect against analytically known answers.

The central correctness tests of this repository. Horn's estimator is **exact**
for a planar surface, so the expected values are closed-form and computed here
independently of the implementation. A tolerance is stated per assertion and is
tight enough that a wrong convention -- transposed axes, a sign error, degrees
versus percent, aspect measured anticlockwise or as the *upslope* direction --
fails rather than squeaks through.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from helpers import make_raster, planar_surface

from wildfireguardian_data import RasterKind, SlopeUnit
from wildfireguardian_data.errors import RasterGeometryError, UnitMismatchError
from wildfireguardian_data.terrain import aspect, gradient_components, slope

#: Interior of the array: Horn's 3x3 estimator has no answer on the edge, so
#: the analytical comparison is made on the interior only (D-0005).
INTERIOR = (slice(1, -1), slice(1, -1))


@pytest.mark.parametrize(
    ("dz_dx", "dz_dy"),
    [
        (0.2, -0.1),    # rises east, falls north -> faces WNW
        (0.0, 0.5),     # rises north -> faces due south
        (-0.3, 0.0),    # falls east -> faces due east
        (0.0, -0.25),   # falls north -> faces due north
        (1.0, 1.0),     # 45-degree diagonal
        (0.05, 0.05),   # gentle
    ],
)
def test_slope_and_aspect_exact_on_a_plane(dz_dx, dz_dy):
    cell = 30.0
    data = planar_surface(shape=(15, 15), cell_size_m=cell, dz_dx=dz_dx, dz_dy=dz_dy)
    layer = make_raster(data, cell_size_m=cell)

    expected_slope_deg = math.degrees(math.atan(math.hypot(dz_dx, dz_dy)))
    # Aspect is the compass azimuth of the DOWNSLOPE direction, clockwise from
    # grid north: azimuth of -grad z = (-dz_dx, -dz_dy).
    expected_aspect_deg = math.degrees(math.atan2(-dz_dx, -dz_dy)) % 360.0

    slope_layer = slope(layer)
    aspect_layer = aspect(layer)

    assert slope_layer.data[INTERIOR] == pytest.approx(expected_slope_deg, abs=1e-4)
    # Compare on the unit circle so a result of 359.9999 vs 0.0 is not a failure.
    got = np.radians(aspect_layer.data[INTERIOR].astype(np.float64))
    want = math.radians(expected_aspect_deg)
    angular_error = np.abs(np.arctan2(np.sin(got - want), np.cos(got - want)))
    assert float(angular_error.max()) < 1e-5


def test_gradient_components_recover_the_plane_coefficients():
    # The estimator's own intermediate values, checked directly: dz_dy must be
    # the NORTHWARD gradient, not the per-row (southward) one.
    dz_dx, dz_dy = 0.37, -0.21
    layer = make_raster(planar_surface(dz_dx=dz_dx, dz_dy=dz_dy))
    gx, gy, valid = gradient_components(layer)
    assert gx[INTERIOR] == pytest.approx(dz_dx, abs=1e-9)
    assert gy[INTERIOR] == pytest.approx(dz_dy, abs=1e-9)
    assert valid[INTERIOR].all()
    assert not valid[0].any() and not valid[:, 0].any()


def test_aspect_cardinal_directions_are_not_transposed():
    # Each case fails loudly if x and y are swapped anywhere in the chain.
    cases = {
        (0.1, 0.0): 270.0,   # rises east -> faces west
        (-0.1, 0.0): 90.0,   # falls east -> faces east
        (0.0, 0.1): 180.0,   # rises north -> faces south
        (0.0, -0.1): 0.0,    # falls north -> faces north
    }
    for (dz_dx, dz_dy), expected in cases.items():
        layer = make_raster(planar_surface(dz_dx=dz_dx, dz_dy=dz_dy))
        got = float(np.nanmedian(aspect(layer).data[INTERIOR]))
        difference = abs((got - expected + 180.0) % 360.0 - 180.0)
        assert difference < 1e-3, f"dz=({dz_dx},{dz_dy}) gave {got}, expected {expected}"


def test_slope_units():
    dz_dx, dz_dy = 0.2, -0.1
    layer = make_raster(planar_surface(dz_dx=dz_dx, dz_dy=dz_dy))
    magnitude = math.hypot(dz_dx, dz_dy)

    degrees = slope(layer, unit=SlopeUnit.DEGREE).data[INTERIOR]
    radians = slope(layer, unit=SlopeUnit.RADIAN).data[INTERIOR]
    percent = slope(layer, unit=SlopeUnit.PERCENT).data[INTERIOR]

    assert degrees == pytest.approx(math.degrees(math.atan(magnitude)), abs=1e-4)
    assert radians == pytest.approx(math.atan(magnitude), abs=1e-6)
    assert percent == pytest.approx(magnitude * 100.0, abs=1e-3)


def test_slope_is_independent_of_absolute_elevation():
    # Adding a constant changes no gradient. A test that would catch an
    # implementation accidentally dividing by elevation.
    low = make_raster(planar_surface(base=10.0))
    high = make_raster(planar_surface(base=1_000_000.0))
    assert slope(low).data[INTERIOR] == pytest.approx(
        slope(high).data[INTERIOR], abs=1e-3
    )


def test_slope_scales_correctly_with_cell_size():
    # The same *array* on a coarser grid is a gentler surface: halving the
    # gradient by doubling the cell size must halve tan(slope).
    data = planar_surface(cell_size_m=1.0, dz_dx=1.0, dz_dy=0.0)
    fine = slope(make_raster(data, cell_size_m=10.0)).data[INTERIOR]
    coarse = slope(make_raster(data, cell_size_m=20.0)).data[INTERIOR]
    assert math.tan(math.radians(float(fine.mean()))) == pytest.approx(
        2 * math.tan(math.radians(float(coarse.mean()))), rel=1e-6
    )


def test_flat_terrain_has_zero_slope_and_no_aspect(flat_layer):
    slope_layer = slope(flat_layer)
    aspect_layer = aspect(flat_layer)
    interior_slope = slope_layer.data[INTERIOR]
    assert np.all(interior_slope == 0.0)
    # Aspect must be NaN, never 0: 0 is a legal aspect meaning "faces north"
    # (docs/DECISIONS.md D-0006).
    assert np.isnan(aspect_layer.data).all()
    assert aspect_layer.valid_count == 0


def test_aspect_flat_tolerance_is_opt_in_and_recorded():
    # A nominally flat surface with float noise: the default tolerance of 0
    # keeps the (meaningless but real) aspects; an explicit tolerance drops them.
    rng = np.random.default_rng(7)
    data = 400.0 + rng.normal(0.0, 1e-9, size=(11, 11))
    layer = make_raster(data)
    strict = aspect(layer)
    tolerant = aspect(layer, flat_slope_tolerance=1e-6)
    assert strict.valid_count > tolerant.valid_count
    assert tolerant.valid_count == 0
    assert tolerant.provenance.transformations[-1].parameters["flat_slope_tolerance"] == 1e-6


def test_derivative_edges_are_nodata_and_extent_shrinks_by_one_cell(tilted_plane_layer):
    slope_layer = slope(tilted_plane_layer)
    assert np.isnan(slope_layer.data[0]).all()
    assert np.isnan(slope_layer.data[-1]).all()
    assert np.isnan(slope_layer.data[:, 0]).all()
    assert np.isnan(slope_layer.data[:, -1]).all()
    # Exactly the border, and nothing more, is lost.
    height, width = tilted_plane_layer.shape
    expected_lost = height * width - (height - 2) * (width - 2)
    assert slope_layer.missing_count == expected_lost
    # The grid itself is unchanged, so slope and DEM still correspond cell-for-cell.
    assert slope_layer.transform == tilted_plane_layer.transform
    assert slope_layer.shape == tilted_plane_layer.shape


def test_slope_provenance_records_the_estimator_and_policies(tilted_plane_layer):
    record = slope(tilted_plane_layer).provenance
    transformation = record.transformations[-1]
    assert transformation.operation == "slope"
    assert transformation.parameters["estimator"] == "horn_1981_3x3"
    assert transformation.parameters["unit"] == "deg"
    assert "nodata" in transformation.parameters["edge_policy"]
    assert record.parents == ("plane",)
    assert record.value_unit == "deg"


def test_aspect_provenance_states_grid_north(tilted_plane_layer):
    transformation = aspect(tilted_plane_layer).provenance.transformations[-1]
    assert transformation.parameters["reference_direction"] == "grid_north_clockwise"
    assert transformation.parameters["faces"] == "downslope"
    assert "GRID north" in transformation.notes


def test_categorical_layer_is_refused():
    layer = make_raster(
        np.ones((5, 5), dtype=np.int32),
        kind=RasterKind.CATEGORICAL,
        value_unit="class",
        nodata=255,
    )
    with pytest.raises(RasterGeometryError):
        slope(layer)


def test_non_length_value_unit_is_refused():
    layer = make_raster(planar_surface(), value_unit="class")
    with pytest.raises(UnitMismatchError):
        slope(layer)


def test_foot_elevation_on_a_metre_grid_is_refused():
    # A-TER-8: mixing a foot vertical unit with a metre horizontal one is an
    # error, not a scaling opportunity.
    layer = make_raster(planar_surface(), value_unit="ft")
    with pytest.raises(UnitMismatchError) as excinfo:
        slope(layer)
    assert "matching vertical and horizontal units" in str(excinfo.value)


def test_too_small_array_is_refused():
    layer = make_raster(np.zeros((2, 5)))
    with pytest.raises(RasterGeometryError) as excinfo:
        slope(layer)
    assert "3x3" in str(excinfo.value)


def test_horn_matches_gdal_on_a_known_non_planar_window():
    # A hand-computed check on a non-planar surface, where Horn is an estimator
    # rather than exact. The window and the expected value are worked out by
    # hand from the published formula so this does not just re-run the code.
    data = np.array(
        [
            [100.0, 100.0, 100.0],
            [100.0, 100.0, 100.0],
            [110.0, 110.0, 110.0],
        ]
    )
    cell = 10.0
    layer = make_raster(data, cell_size_m=cell)
    # dz_dx = ((100 + 2*100 + 110) - (100 + 2*100 + 110)) / (8*10) = 0
    # dz_dsouth = ((110 + 2*110 + 110) - (100 + 2*100 + 100)) / (8*10) = 40/80... :
    #   south row sum with weights = 110 + 220 + 110 = 440
    #   north row sum with weights = 100 + 200 + 100 = 400
    #   dz_dsouth = (440 - 400) / 80 = 0.5  -> dz_dy = -0.5
    gx, gy, _ = gradient_components(layer)
    assert gx[1, 1] == pytest.approx(0.0, abs=1e-12)
    assert gy[1, 1] == pytest.approx(-0.5, abs=1e-12)
    assert slope(layer).data[1, 1] == pytest.approx(
        math.degrees(math.atan(0.5)), abs=1e-4
    )
    # Elevation RISES towards the south (row index increases southward and the
    # south row is 110), so the downslope direction points north and the aspect
    # is 0 degrees. Note that 0 here is a real aspect value, distinguishable
    # from the NaN that a flat cell gets (docs/DECISIONS.md D-0006).
    assert aspect(layer).data[1, 1] == pytest.approx(0.0, abs=1e-3)
    assert not np.isnan(aspect(layer).data[1, 1])
