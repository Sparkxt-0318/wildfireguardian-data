"""The internal raster model: array + grid + CRS + nodata + units + provenance.

Everything numeric in this package operates on :class:`RasterLayer`, never on a
bare NumPy array. That is the point: a bare array plus a cell size is exactly
the state in which CRS, unit and missing-data errors become invisible
(``docs/DECISIONS.md`` D-0003).

Invariants enforced at construction:

* 2-D array (A-RAS-1);
* north-up, axis-aligned grid, no rotation (A-RAS-1) -- by construction, since
  :class:`GridTransform` cannot express rotation;
* a declared :class:`RasterKind`, because the correct resampling method depends
  on it and a wrong default would be believed (A-RAS-4);
* provenance present.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

import numpy as np

from .bounds import Bounds
from .crs import CRSLike, crs_to_string, parse_crs, require_projected_metre_crs
from .errors import MissingDataError, RasterGeometryError
from .provenance.models import ProvenanceRecord, Transformation, require_provenance

__all__ = ["GridTransform", "RasterKind", "RasterLayer"]


class RasterKind(str, Enum):
    """Whether a raster's values are continuous or categorical.

    Consequences, not labels: a ``CATEGORICAL`` raster may only be resampled by
    nearest neighbour, and arithmetic statistics (mean, standard deviation) over
    it are refused. Averaging fuel-class codes 2 and 4 into 3 produces a class
    that means something else entirely (A-FU-3).
    """

    CONTINUOUS = "continuous"
    CATEGORICAL = "categorical"


@dataclass(frozen=True)
class GridTransform:
    """A north-up, axis-aligned raster grid.

    ``(x_origin, y_origin)`` is the **upper-left corner of cell (0, 0)**, the
    GDAL convention (A-RAS-2). ``x_size`` and ``y_size`` are positive cell
    dimensions; the fact that row indices increase *southward* is implicit in
    "north-up" and is applied in :meth:`xy` rather than being carried as a
    negative number, so a sign error cannot silently flip the grid.

    Rotation is not representable here at all. A rotated raster is rejected at
    the door by :meth:`from_gdal` instead of being approximately handled.
    """

    x_origin: float
    y_origin: float
    x_size: float
    y_size: float

    def __post_init__(self) -> None:
        for name in ("x_origin", "y_origin", "x_size", "y_size"):
            object.__setattr__(self, name, float(getattr(self, name)))
        if self.x_size <= 0 or self.y_size <= 0:
            raise RasterGeometryError(
                f"cell size must be positive, got ({self.x_size}, {self.y_size}). "
                "north-up orientation is implied by GridTransform; do not pass a "
                "negative y step here."
            )

    # -- indexing ----------------------------------------------------------- #
    def xy(self, row: float, col: float, *, center: bool = True) -> tuple[float, float]:
        """Map (row, col) to (x, y).

        ``center=True`` (default) returns the **cell centre**; ``center=False``
        returns the cell's upper-left corner. The default is the centre because
        that is the location a cell's value is usually taken to represent, and
        an off-by-half-a-cell error is the classic silent 15 m shift on a 30 m
        DEM.
        """
        offset = 0.5 if center else 0.0
        return (
            self.x_origin + (col + offset) * self.x_size,
            self.y_origin - (row + offset) * self.y_size,
        )

    def rowcol(self, x: float, y: float) -> tuple[int, int]:
        """Map (x, y) to the (row, col) of the containing cell.

        Uses floor, so a coordinate exactly on a cell's left/top edge belongs to
        that cell (half-open cells, consistent with
        :meth:`Bounds.contains_point`). Returned indices are **not** clamped to
        the array: an out-of-range index is the caller's to detect, because
        clamping would silently sample the wrong cell at the edge.
        """
        col = int(np.floor((x - self.x_origin) / self.x_size))
        row = int(np.floor((self.y_origin - y) / self.y_size))
        return row, col

    def bounds_for(self, height: int, width: int, crs: CRSLike = None) -> Bounds:
        """The outer bounds of a ``(height, width)`` array on this grid."""
        return Bounds(
            self.x_origin,
            self.y_origin - height * self.y_size,
            self.x_origin + width * self.x_size,
            self.y_origin,
            crs=crs,
        )

    def offset(self, row_offset: int, col_offset: int) -> GridTransform:
        """A grid shifted by whole cells -- used when windowing or clipping."""
        return GridTransform(
            self.x_origin + col_offset * self.x_size,
            self.y_origin - row_offset * self.y_size,
            self.x_size,
            self.y_size,
        )

    @property
    def is_square(self) -> bool:
        """Whether cells are square to within a relative 1e-9."""
        return bool(np.isclose(self.x_size, self.y_size, rtol=1e-9, atol=0.0))

    # -- interop ------------------------------------------------------------ #
    def to_gdal(self) -> tuple[float, float, float, float, float, float]:
        """GDAL geotransform ``(x_origin, x_size, 0, y_origin, 0, -y_size)``."""
        return (self.x_origin, self.x_size, 0.0, self.y_origin, 0.0, -self.y_size)

    def to_affine(self) -> Any:
        """An ``affine.Affine`` for rasterio interop (lazy import)."""
        try:
            from affine import Affine
        except ImportError as exc:  # pragma: no cover - affine ships with rasterio
            from .errors import OptionalDependencyError

            raise OptionalDependencyError(
                "affine", purpose="rasterio interop"
            ) from exc
        return Affine(self.x_size, 0.0, self.x_origin, 0.0, -self.y_size, self.y_origin)

    @classmethod
    def from_affine(cls, affine: Any, *, rotation_tolerance: float = 0.0) -> GridTransform:
        """Build from an ``affine.Affine``, rejecting rotation and south-up grids.

        ``rotation_tolerance`` defaults to **exactly zero**: a raster with a
        non-zero rotation term is not approximately north-up, and accepting it
        with a small tolerance would mean silently discarding the rotation and
        mislocating every cell. See A-RAS-1.
        """
        a, b, c, d, e, f = (
            affine.a,
            affine.b,
            affine.c,
            affine.d,
            affine.e,
            affine.f,
        )
        if abs(b) > rotation_tolerance or abs(d) > rotation_tolerance:
            raise RasterGeometryError(
                f"raster transform has non-zero rotation terms (b={b}, d={d}); "
                "this package handles north-up, axis-aligned grids only "
                "(docs/ASSUMPTIONS.md A-RAS-1). reproject the raster to an "
                "axis-aligned grid first -- do not drop the rotation."
            )
        if e >= 0:
            raise RasterGeometryError(
                f"raster transform has a non-negative y step (e={e}), i.e. it is "
                "south-up or degenerate. flipping it here would invert every "
                "row index silently; reproject or flip it explicitly."
            )
        if a <= 0:
            raise RasterGeometryError(
                f"raster transform has a non-positive x step (a={a}); "
                "west-to-east ordering is required."
            )
        return cls(c, f, a, -e)

    @classmethod
    def from_gdal(cls, geotransform: Any) -> GridTransform:
        """Build from a 6-element GDAL geotransform tuple."""
        gt = tuple(float(v) for v in geotransform)
        if len(gt) != 6:
            raise RasterGeometryError(
                f"GDAL geotransform must have 6 elements; got {len(gt)}"
            )
        x_origin, x_size, rot_x, y_origin, rot_y, y_step = gt
        if rot_x != 0.0 or rot_y != 0.0:
            raise RasterGeometryError(
                f"GDAL geotransform has rotation terms ({rot_x}, {rot_y}); "
                "see docs/ASSUMPTIONS.md A-RAS-1."
            )
        if y_step >= 0:
            raise RasterGeometryError(
                f"GDAL geotransform y step is {y_step} (not negative); the grid "
                "is south-up and is not silently flipped."
            )
        if x_size <= 0:
            raise RasterGeometryError(f"GDAL geotransform x step is {x_size} (not positive)")
        return cls(x_origin, y_origin, x_size, -y_step)

    def to_dict(self) -> dict[str, float]:
        return {
            "x_origin": self.x_origin,
            "y_origin": self.y_origin,
            "x_size": self.x_size,
            "y_size": self.y_size,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GridTransform:
        return cls(
            payload["x_origin"], payload["y_origin"], payload["x_size"], payload["y_size"]
        )


@dataclass(frozen=True)
class RasterLayer:
    """A single-band raster with everything needed to use it correctly.

    Parameters
    ----------
    name:
        Layer name; must match ``provenance.layer_name``.
    data:
        2-D NumPy array. Stored as given (no copy, no cast): a silent cast of
        ``int16`` elevation to ``float32`` would change the nodata comparison.
    transform:
        :class:`GridTransform` for the array's grid.
    crs:
        Anything :func:`wildfireguardian_data.crs.parse_crs` accepts, or ``None``
        for an undeclared CRS. ``None`` is loadable but cannot be combined with
        anything (A-CRS-5).
    nodata:
        The value representing missing cells, or ``None`` for *no missing-data
        value declared* -- which is not the same as *no missing cells*
        (A-RAS-3).
    kind:
        :class:`RasterKind`; required.
    value_unit:
        Unit of the cell values: a :class:`~wildfireguardian_data.units.LengthUnit`
        for elevation, a ``SlopeUnit`` for slope/aspect, or a string such as
        ``"class"`` / ``"count"``.
    provenance:
        :class:`~wildfireguardian_data.provenance.models.ProvenanceRecord`.
    """

    name: str
    data: np.ndarray
    transform: GridTransform
    crs: Any
    nodata: Any
    kind: RasterKind
    value_unit: Any
    provenance: ProvenanceRecord

    def __post_init__(self) -> None:
        array = self.data
        if not isinstance(array, np.ndarray):
            array = np.asarray(array)
            object.__setattr__(self, "data", array)
        if array.ndim != 2:
            raise RasterGeometryError(
                f"RasterLayer data must be 2-D; got shape {array.shape}. "
                "multi-band data is handled as several layers, so that each band "
                "carries its own units and provenance."
            )
        if array.size == 0:
            raise RasterGeometryError(
                f"RasterLayer {self.name!r} has an empty array {array.shape}. an "
                "empty result usually means a clip missed the data entirely, "
                "which is reported rather than returned."
            )
        if not isinstance(self.transform, GridTransform):
            raise RasterGeometryError(
                f"transform must be a GridTransform; got {type(self.transform).__name__}"
            )
        object.__setattr__(self, "kind", RasterKind(self.kind))
        object.__setattr__(self, "crs", parse_crs(self.crs))
        require_provenance(self.provenance, context=f"constructing layer {self.name!r}")
        if self.provenance.layer_name != self.name:
            raise RasterGeometryError(
                f"layer name {self.name!r} does not match provenance layer_name "
                f"{self.provenance.layer_name!r}; a mismatch here means the "
                "provenance describes a different layer."
            )
        if self.nodata is not None and np.issubdtype(array.dtype, np.integer):
            if isinstance(self.nodata, float) and not float(self.nodata).is_integer():
                raise RasterGeometryError(
                    f"nodata={self.nodata!r} is not representable in integer dtype "
                    f"{array.dtype}; it would never match any cell, leaving "
                    "missing data silently indistinguishable from real values."
                )

    # -- geometry ----------------------------------------------------------- #
    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.data.shape[0]), int(self.data.shape[1]))

    @property
    def height(self) -> int:
        return int(self.data.shape[0])

    @property
    def width(self) -> int:
        return int(self.data.shape[1])

    @property
    def resolution(self) -> tuple[float, float]:
        """``(x_size, y_size)`` in CRS units. A pair, never one number."""
        return (self.transform.x_size, self.transform.y_size)

    @property
    def bounds(self) -> Bounds:
        return self.transform.bounds_for(self.height, self.width, crs=self.crs)

    def cell_center_coords(self, row: int, col: int) -> tuple[float, float]:
        """Coordinates of the centre of cell ``(row, col)``."""
        return self.transform.xy(row, col, center=True)

    def cell_area(self) -> float:
        """Area of one cell in squared CRS units.

        Requires a projected metre CRS: a cell area in square degrees is not an
        area, and its value varies with latitude (D-0010).
        """
        require_projected_metre_crs(
            self.crs, context=f"computing cell area of {self.name!r}"
        )
        return self.transform.x_size * self.transform.y_size

    # -- missing data ------------------------------------------------------- #
    def valid_mask(self) -> np.ndarray:
        """Boolean array, ``True`` where a cell holds real data.

        ``NaN`` is always invalid for floating-point data, whether or not
        ``nodata`` is ``NaN``: a ``NaN`` that arrived from an upstream tool is
        missing data regardless of what this layer declares.

        Comparison against a declared ``nodata`` is **exact**, not tolerant.
        A tolerant comparison would mask real elevations near a sentinel such as
        ``-9999`` (Korean coastal DEM cells can legitimately be near zero, and a
        tolerance around ``0`` would be catastrophic).
        """
        data = self.data
        if np.issubdtype(data.dtype, np.floating):
            mask = ~np.isnan(data)
        else:
            mask = np.ones(data.shape, dtype=bool)
        if self.nodata is not None:
            try:
                nodata_is_nan = bool(np.isnan(self.nodata))
            except TypeError:
                nodata_is_nan = False
            if not nodata_is_nan:
                mask &= data != self.nodata
        return mask

    def masked_array(self) -> np.ma.MaskedArray:
        """A ``numpy.ma.MaskedArray`` view with missing cells masked.

        Use this, or :meth:`valid_mask`, for any reduction. Never
        ``np.nan_to_num``: turning missing elevation into ``0`` puts sea level
        in the middle of a mountain (``docs/FAILURE_MODES.md`` F-MD-1).
        """
        return np.ma.masked_array(self.data, mask=~self.valid_mask())

    @property
    def valid_count(self) -> int:
        return int(self.valid_mask().sum())

    @property
    def missing_count(self) -> int:
        return int(self.data.size - self.valid_count)

    @property
    def has_missing(self) -> bool:
        return self.missing_count > 0

    def require_any_valid(self, context: str = "") -> None:
        """Raise if the layer is entirely missing.

        A statistic over a fully masked array is not zero, not ``NaN``-and-move
        on: it is an error, because it means an earlier step produced nothing.
        """
        if self.valid_count == 0:
            where = f" while {context}" if context else ""
            raise MissingDataError(
                f"layer {self.name!r} has no valid cells{where} "
                f"({self.data.size} cells, all missing). this usually means a "
                "clip fell outside the data or a nodata value was declared that "
                "matches every cell."
            )

    # -- derivation --------------------------------------------------------- #
    def with_data(
        self,
        data: np.ndarray,
        *,
        name: str | None = None,
        nodata: Any = ...,
        kind: RasterKind | None = None,
        value_unit: Any = ...,
        transform: GridTransform | None = None,
        provenance: ProvenanceRecord | None = None,
    ) -> RasterLayer:
        """A new layer sharing this one's grid and CRS, with new values.

        ``provenance`` is required in practice: passing ``None`` keeps this
        layer's record, which is only correct when the values did not change
        meaning. Derivation helpers use :meth:`derived` instead.
        """
        return replace(
            self,
            data=data,
            name=name if name is not None else self.name,
            nodata=self.nodata if nodata is ... else nodata,
            kind=kind if kind is not None else self.kind,
            value_unit=self.value_unit if value_unit is ... else value_unit,
            transform=transform if transform is not None else self.transform,
            provenance=provenance if provenance is not None else self.provenance,
        )

    def derived(
        self,
        data: np.ndarray,
        *,
        name: str,
        transformation: Transformation,
        kind: RasterKind | None = None,
        nodata: Any = ...,
        value_unit: Any = ...,
        transform: GridTransform | None = None,
        crs: Any = ...,
        provenance_changes: dict[str, Any] | None = None,
    ) -> RasterLayer:
        """A new layer derived from this one, with provenance carried forward.

        The single path by which a new raster comes into existence inside this
        package, so that "every artifact records its transformations" is
        structural rather than a habit (AGENTS.md §7).

        ``crs`` defaults to this layer's CRS, which is correct for every
        operation except reprojection. Reprojection must pass the target CRS
        here: a derived layer whose array was warped but whose ``crs`` field
        still named the source CRS would be the exact silent error this package
        exists to prevent.
        """
        changes: dict[str, Any] = dict(provenance_changes or {})
        new_transform = transform if transform is not None else self.transform
        new_crs = self.crs if crs is ... else parse_crs(crs)
        changes.setdefault("output_crs", crs_to_string(new_crs))
        changes.setdefault(
            "spatial_resolution", (new_transform.x_size, new_transform.y_size)
        )
        new_nodata = self.nodata if nodata is ... else nodata
        changes.setdefault("nodata_representation", _nodata_text(new_nodata))
        new_unit = self.value_unit if value_unit is ... else value_unit
        changes.setdefault("value_unit", _unit_text(new_unit))
        record = self.provenance.derive(name, transformation, **changes)
        return RasterLayer(
            name=name,
            data=data,
            transform=new_transform,
            crs=new_crs,
            nodata=new_nodata,
            kind=kind if kind is not None else self.kind,
            value_unit=new_unit,
            provenance=record,
        )

    # -- description -------------------------------------------------------- #
    def describe(self) -> dict[str, Any]:
        """A JSON-safe description, used by ``wg-data summarize-study-area``."""
        return {
            "name": self.name,
            "shape": list(self.shape),
            "dtype": str(self.data.dtype),
            "crs": crs_to_string(self.crs),
            "resolution": list(self.resolution),
            "bounds": self.bounds.to_dict(),
            "kind": self.kind.value,
            "value_unit": _unit_text(self.value_unit),
            "nodata": _nodata_text(self.nodata),
            "valid_cells": self.valid_count,
            "missing_cells": self.missing_count,
            "data_class": self.provenance.data_class.value,
            "temporal_class": self.provenance.temporal_class.value,
        }

    def __str__(self) -> str:
        return (
            f"RasterLayer({self.name!r}, {self.shape[0]}x{self.shape[1]}, "
            f"res={self.resolution[0]:g}x{self.resolution[1]:g}, "
            f"crs={crs_to_string(self.crs)}, unit={_unit_text(self.value_unit)}, "
            f"missing={self.missing_count})"
        )


def _unit_text(unit: Any) -> str:
    """Render a unit for provenance, without inventing one when absent."""
    if unit is None:
        return "UNKNOWN"
    value = getattr(unit, "value", unit)
    return str(value)


def _nodata_text(nodata: Any) -> str:
    """Render a nodata value as provenance text.

    ``None`` becomes ``"none_declared"``, deliberately distinct from
    ``"UNKNOWN"``: the former states that the layer declares no missing-data
    value, the latter that we do not know what it declares (A-RAS-3).
    """
    if nodata is None:
        return "none_declared"
    try:
        if np.isnan(nodata):
            return "nan"
    except TypeError:
        pass
    return repr(nodata)
