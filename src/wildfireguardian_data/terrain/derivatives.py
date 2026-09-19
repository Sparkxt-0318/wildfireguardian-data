"""Slope and aspect from a DEM, by Horn's (1981) 3x3 method.

Conventions, all enforced and all tested against analytically known surfaces in
``tests/test_terrain_derivatives.py``:

* the grid must be **projected with metre axes** -- a degree grid gives a slope
  that is wrong by ``1/cos(latitude)`` between the x and y directions
  (``docs/DECISIONS.md`` D-0010);
* vertical and horizontal units must **match** (A-TER-8);
* **slope** is degrees from horizontal by default, 0-90;
* **aspect** is degrees clockwise from **grid** north, 0-360, giving the
  **downslope-facing** direction, and is ``NaN`` where slope is exactly zero
  (D-0006);
* a cell on the array edge, or with any missing neighbour, is ``NaN`` -- the
  valid region shrinks by one cell and nothing is extrapolated (D-0005).

Why Horn rather than a plane fit or a 2-cell difference: D-0004.

Derivation of the estimator, so a reader can check it rather than trust it.
With rows increasing **southward** and the 3x3 window labelled::

    a b c        (row-1, col-1) (row-1, col) (row-1, col+1)
    d e f        (row,   col-1) (row,   col) (row,   col+1)
    g h i        (row+1, col-1) (row+1, col) (row+1, col+1)

Horn's weighted central differences are

    dz_dx     = ((c + 2f + i) - (a + 2d + g)) / (8 * x_size)
    dz_dsouth = ((g + 2h + i) - (a + 2b + c)) / (8 * y_size)

``dz_dsouth`` is the rise per metre travelled **southward**, so the northward
gradient is ``dz_dy = -dz_dsouth``. For a planar surface ``z = A*x + B*y + C``
these evaluate exactly to ``A`` and ``B``, which is what makes the tilted-plane
test a correctness test rather than a tolerance exercise.

Slope is ``atan(hypot(dz_dx, dz_dy))``. Aspect is the compass azimuth of the
downslope vector ``-grad z = (-dz_dx, -dz_dy)``, i.e.
``atan2(-dz_dx, -dz_dy)`` measured clockwise from +y (grid north).
"""

from __future__ import annotations

import numpy as np

from ..crs import crs_to_string, require_projected_metre_crs
from ..errors import RasterGeometryError, UnitMismatchError
from ..provenance.models import Transformation
from ..raster import RasterKind, RasterLayer
from ..units import LengthUnit, SlopeUnit, convert_slope, parse_length_unit, parse_slope_unit

__all__ = ["HORN_ESTIMATOR", "slope", "aspect", "gradient_components"]

#: Recorded in provenance so a downstream reader knows which estimator produced
#: the numbers, since estimators disagree by a few degrees on real DEMs.
HORN_ESTIMATOR = "horn_1981_3x3"


def _require_metre_grid(dem: RasterLayer, operation: str) -> LengthUnit:
    """Gate shared by slope and aspect: metre CRS, matching vertical unit."""
    require_projected_metre_crs(
        dem.crs, context=f"computing {operation} for layer {dem.name!r}"
    )
    if dem.kind is not RasterKind.CONTINUOUS:
        raise RasterGeometryError(
            f"cannot compute {operation} from layer {dem.name!r}: it is declared "
            f"{dem.kind.value}, and finite differences of category codes are "
            "meaningless (docs/ASSUMPTIONS.md A-FU-3)."
        )
    try:
        vertical = parse_length_unit(dem.value_unit)
    except Exception as exc:
        raise UnitMismatchError(
            f"layer {dem.name!r} has value_unit {dem.value_unit!r}, which is not a "
            f"length unit, so {operation} cannot be computed from it. an "
            "elevation layer must declare metres (or another length unit) "
            f"(docs/ASSUMPTIONS.md A-TER-1): {exc}"
        ) from exc
    if vertical is not LengthUnit.METRE:
        raise UnitMismatchError(
            f"layer {dem.name!r} has vertical unit {vertical.value} but its CRS "
            f"{crs_to_string(dem.crs)} has metre axes. slope requires matching "
            "vertical and horizontal units (docs/ASSUMPTIONS.md A-TER-8); "
            "convert the elevation values explicitly with "
            "units.convert_length and record the conversion in provenance."
        )
    return vertical


def gradient_components(dem: RasterLayer) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Horn's ``(dz_dx, dz_dy, valid)`` for a DEM.

    ``dz_dx`` is rise per metre eastward, ``dz_dy`` rise per metre **northward**
    (note the sign flip relative to row order, derived in the module docstring).
    ``valid`` is ``True`` where the full 3x3 neighbourhood was available.

    Returned in float64 regardless of input dtype: differencing ``int16``
    elevations in their own dtype overflows, and doing it in ``float32`` loses
    precision on the large false-easting coordinates the Korean CRSs use.
    """
    _require_metre_grid(dem, "terrain gradients")
    data = dem.data
    if data.shape[0] < 3 or data.shape[1] < 3:
        raise RasterGeometryError(
            f"layer {dem.name!r} is {data.shape[0]}x{data.shape[1]}; Horn's 3x3 "
            "estimator needs at least 3 rows and 3 columns. clip with "
            "buffer_cells>=1 so the derivative has a neighbourhood to use "
            "(docs/DECISIONS.md D-0005)."
        )

    values = np.asarray(data, dtype=np.float64)
    valid_in = dem.valid_mask()
    # Missing cells must not contribute a numeric value. Zero is as arbitrary as
    # any other filler; it is safe here only because every output cell whose
    # window touched a filled cell is masked out below.
    filled = np.where(valid_in, values, 0.0)

    x_size = float(dem.transform.x_size)
    y_size = float(dem.transform.y_size)

    def w(row_shift: int, col_shift: int) -> np.ndarray:
        """The 3x3 window element at the given (row, col) offset, as a view."""
        rows = slice(1 + row_shift, filled.shape[0] - 1 + row_shift)
        cols = slice(1 + col_shift, filled.shape[1] - 1 + col_shift)
        return filled[rows, cols]

    def m(row_shift: int, col_shift: int) -> np.ndarray:
        rows = slice(1 + row_shift, valid_in.shape[0] - 1 + row_shift)
        cols = slice(1 + col_shift, valid_in.shape[1] - 1 + col_shift)
        return valid_in[rows, cols]

    a, b, c = w(-1, -1), w(-1, 0), w(-1, 1)
    d, f = w(0, -1), w(0, 1)
    g, h, i = w(1, -1), w(1, 0), w(1, 1)

    dz_dx_inner = ((c + 2.0 * f + i) - (a + 2.0 * d + g)) / (8.0 * x_size)
    dz_dsouth_inner = ((g + 2.0 * h + i) - (a + 2.0 * b + c)) / (8.0 * y_size)
    dz_dy_inner = -dz_dsouth_inner

    window_valid = (
        m(-1, -1) & m(-1, 0) & m(-1, 1)
        & m(0, -1) & m(0, 0) & m(0, 1)
        & m(1, -1) & m(1, 0) & m(1, 1)
    )

    dz_dx = np.full(data.shape, np.nan, dtype=np.float64)
    dz_dy = np.full(data.shape, np.nan, dtype=np.float64)
    valid = np.zeros(data.shape, dtype=bool)

    interior = (slice(1, -1), slice(1, -1))
    dz_dx[interior] = np.where(window_valid, dz_dx_inner, np.nan)
    dz_dy[interior] = np.where(window_valid, dz_dy_inner, np.nan)
    valid[interior] = window_valid
    return dz_dx, dz_dy, valid


def slope(
    dem: RasterLayer,
    *,
    unit: SlopeUnit | str = SlopeUnit.DEGREE,
    name: str | None = None,
) -> RasterLayer:
    """Slope magnitude of ``dem``, as a new layer with ``NaN`` for missing.

    Parameters
    ----------
    dem:
        Continuous elevation layer in a projected metre CRS, with a metre
        ``value_unit``.
    unit:
        :class:`~wildfireguardian_data.units.SlopeUnit`. Degrees by default
        (A-TER-4). ``PERCENT`` is percent rise, so 45 degrees is 100 percent.
    name:
        Output layer name; defaults to ``"<dem>_slope_<unit>"``.

    Notes
    -----
    The output's ``nodata`` is ``NaN``, never ``0``: a zero slope is a real,
    common value on a Korean valley floor and must not be confused with a cell
    that could not be computed (D-0006).
    """
    slope_unit = parse_slope_unit(unit)
    dz_dx, dz_dy, valid = gradient_components(dem)

    with np.errstate(invalid="ignore"):
        radians = np.arctan(np.hypot(dz_dx, dz_dy))
    values = convert_slope(radians, SlopeUnit.RADIAN, slope_unit)
    values = np.where(valid, values, np.nan).astype(np.float32)

    out_name = name or f"{dem.name}_slope_{slope_unit.value}"
    transformation = Transformation(
        operation="slope",
        parameters={
            "estimator": HORN_ESTIMATOR,
            "unit": slope_unit.value,
            "x_size": dem.transform.x_size,
            "y_size": dem.transform.y_size,
            "crs": crs_to_string(dem.crs),
            "edge_policy": "nodata (no extrapolation)",
            "nodata_neighbour_policy": "nodata if any of 8 neighbours missing",
            "input_missing_cells": dem.missing_count,
            "output_missing_cells": int((~valid).sum()),
        },
        notes=(
            "Horn (1981) 3x3 weighted central differences; exact for a planar "
            "surface. valid extent shrinks by one cell per pass "
            "(docs/DECISIONS.md D-0005)."
        ),
    )
    return dem.derived(
        values,
        name=out_name,
        transformation=transformation,
        kind=RasterKind.CONTINUOUS,
        nodata=float("nan"),
        value_unit=slope_unit,
        provenance_changes={
            "value_unit": slope_unit.value,
            # Slope is a property of the surface at an instant, but the DEM's
            # own temporal class governs: slope from a 2011-2015 DSM is a
            # statement about 2011-2015, not about today.
            "notes": (
                (dem.provenance.notes + " | " if dem.provenance.notes else "")
                + f"slope by {HORN_ESTIMATOR}; NaN on edges and beside missing data"
            ),
        },
    )


def aspect(
    dem: RasterLayer,
    *,
    name: str | None = None,
    flat_slope_tolerance: float = 0.0,
) -> RasterLayer:
    """Aspect of ``dem`` in degrees clockwise from **grid** north, 0-360.

    Gives the direction the slope **faces** (downslope). ``NaN`` where the
    surface is flat and where the neighbourhood was incomplete.

    Parameters
    ----------
    flat_slope_tolerance:
        Gradient magnitude (rise per metre, dimensionless) at or below which a
        cell is treated as flat and its aspect is ``NaN``. Defaults to **0.0**,
        i.e. only an exactly zero gradient is flat, because any positive
        tolerance discards real aspect information on gentle terrain. Set it
        explicitly (for example ``1e-9``) when floating-point noise in a
        reprojected DEM produces meaningless aspects on a nominally flat
        surface; the value used is recorded in provenance.

    Notes
    -----
    Grid north, not true north (A-TER-5): the difference is the meridian
    convergence of the projection, which is non-zero away from the central
    meridian and is **not** corrected here. A downstream consumer comparing
    aspect to a true-north wind direction must apply the convergence itself.
    """
    if flat_slope_tolerance < 0:
        raise ValueError("flat_slope_tolerance must be >= 0")
    dz_dx, dz_dy, valid = gradient_components(dem)

    magnitude = np.hypot(dz_dx, dz_dy)
    # Azimuth clockwise from grid north of the downslope direction (-grad z).
    with np.errstate(invalid="ignore"):
        azimuth = np.degrees(np.arctan2(-dz_dx, -dz_dy))
    azimuth = np.mod(azimuth, 360.0)

    flat = magnitude <= flat_slope_tolerance
    values = np.where(valid & ~flat, azimuth, np.nan).astype(np.float32)

    out_name = name or f"{dem.name}_aspect_deg"
    transformation = Transformation(
        operation="aspect",
        parameters={
            "estimator": HORN_ESTIMATOR,
            "unit": SlopeUnit.DEGREE.value,
            "reference_direction": "grid_north_clockwise",
            "faces": "downslope",
            "flat_value": "nan",
            "flat_slope_tolerance": flat_slope_tolerance,
            "crs": crs_to_string(dem.crs),
            "flat_cells": int((flat & valid).sum()),
            "output_missing_cells": int((~valid | flat).sum()),
        },
        notes=(
            "aspect is measured from GRID north; meridian convergence to true "
            "north is not corrected (docs/ASSUMPTIONS.md A-TER-5). flat cells "
            "are NaN, not 0 (docs/DECISIONS.md D-0006)."
        ),
    )
    return dem.derived(
        values,
        name=out_name,
        transformation=transformation,
        kind=RasterKind.CONTINUOUS,
        nodata=float("nan"),
        value_unit=SlopeUnit.DEGREE,
        provenance_changes={
            "value_unit": "deg_azimuth_grid_north",
            "notes": (
                (dem.provenance.notes + " | " if dem.provenance.notes else "")
                + "aspect clockwise from grid north, downslope-facing; NaN where flat"
            ),
        },
    )
