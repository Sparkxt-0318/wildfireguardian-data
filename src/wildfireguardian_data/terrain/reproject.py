"""Explicit raster reprojection.

Reprojection happens **only** when a caller asks for it, and always records the
resampling method, the source and target CRS, and the before/after cell size
(``docs/DECISIONS.md`` D-0002, D-0004 and ``docs/ASSUMPTIONS.md`` A-RAS-4,
A-CRS-4).

Needs the ``[geo]`` extra (rasterio/GDAL); the rest of the package does not
(D-0003).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..crs import crs_to_string, require_crs
from ..errors import (
    ConfigError,
    MissingDataError,
    OptionalDependencyError,
)
from ..provenance.models import Transformation
from ..raster import GridTransform, RasterKind, RasterLayer
from .io import resolution_unit_text

__all__ = ["RESAMPLING_FOR_KIND", "reproject_raster"]

#: Default resampling per raster kind (A-RAS-4). Categorical data has exactly
#: one correct answer here: any averaging invents classes that do not exist.
RESAMPLING_FOR_KIND: dict[RasterKind, str] = {
    RasterKind.CONTINUOUS: "bilinear",
    RasterKind.CATEGORICAL: "nearest",
}

_CONTINUOUS_ONLY = {"bilinear", "cubic", "cubic_spline", "lanczos", "average", "rms"}


def _rasterio_modules() -> tuple[Any, Any, Any]:
    try:
        import rasterio  # noqa: F401
        from rasterio.enums import Resampling
        from rasterio.warp import calculate_default_transform, reproject
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OptionalDependencyError(
            "rasterio", purpose="raster reprojection"
        ) from exc
    return Resampling, calculate_default_transform, reproject


def reproject_raster(
    layer: RasterLayer,
    dst_crs: Any,
    *,
    resampling: str | None = None,
    dst_resolution: float | tuple[float, float] | None = None,
    dst_nodata: Any = None,
    target_grid: GridTransform | None = None,
    target_shape: tuple[int, int] | None = None,
    name: str | None = None,
) -> RasterLayer:
    """Reproject ``layer`` to ``dst_crs``.

    Parameters
    ----------
    resampling:
        A rasterio resampling name. Defaults to
        :data:`RESAMPLING_FOR_KIND` for the layer's kind. Asking for an
        averaging method on a ``CATEGORICAL`` layer raises (A-FU-3).
    dst_resolution:
        Target cell size, as one number for square cells or ``(x, y)``. When
        omitted, rasterio's default transform is used and the resulting cell
        size -- which is generally **not** square and generally not a round
        number -- is recorded in provenance. Passing an explicit resolution is
        strongly preferred for a study area, so that sibling layers share a
        grid.
    dst_nodata:
        Missing-data value in the output. Defaults to ``NaN`` for floating
        point. For an integer layer there is no safe default, so the layer must
        already declare a ``nodata`` value, or this must be supplied: filling
        the triangular gaps a rotation leaves behind with ``0`` would create
        fictitious sea-level cells or fictitious fuel class 0
        (``docs/FAILURE_MODES.md`` F-MD-1).
    target_grid, target_shape:
        Warp directly onto an **existing** grid rather than onto whatever grid
        rasterio's default transform produces. This is how sibling rasters in
        one bundle are co-registered: without it, each layer's target grid is
        derived from its own extent, so a DEM and a fuel raster reprojected
        independently land on origins offset by a fraction of a cell, and every
        fuel value is displaced relative to the DEM cell a consumer indexes it
        by (A-RAS-5). Mutually exclusive with ``dst_resolution``, whose cell
        size ``target_grid`` already fixes.
    """
    Resampling, calculate_default_transform, reproject = _rasterio_modules()

    src_crs = require_crs(layer.crs, context=f"reprojecting {layer.name!r}")
    target = require_crs(dst_crs, context=f"reprojecting {layer.name!r}")
    if src_crs.equals(target):
        return layer

    method_name = resampling or RESAMPLING_FOR_KIND[layer.kind]
    if layer.kind is RasterKind.CATEGORICAL and method_name in _CONTINUOUS_ONLY:
        raise ConfigError(
            f"resampling={method_name!r} averages neighbouring values, which is "
            f"invalid for the categorical layer {layer.name!r}: the average of "
            "class codes 2 and 4 is class 3, a different category "
            "(docs/ASSUMPTIONS.md A-FU-3). use 'nearest' or 'mode'."
        )
    try:
        method = Resampling[method_name]
    except KeyError as exc:
        raise ConfigError(
            f"unknown resampling method {method_name!r}; rasterio offers "
            f"{sorted(r.name for r in Resampling)}"
        ) from exc

    is_float = np.issubdtype(layer.data.dtype, np.floating)
    if dst_nodata is None:
        if is_float:
            dst_nodata = float("nan")
        elif layer.nodata is not None:
            dst_nodata = layer.nodata
        else:
            raise MissingDataError(
                f"layer {layer.name!r} has integer dtype {layer.data.dtype} and "
                "declares no nodata value, so reprojection has nothing to write "
                "into cells the warp does not cover. supply dst_nodata "
                "explicitly; this package will not default it to 0 "
                "(docs/FAILURE_MODES.md F-MD-1)."
            )

    # Source missing cells become NaN so the warp cannot average a sentinel such
    # as -9999 into a neighbouring real elevation, producing a plausible but
    # fabricated value.
    if is_float:
        source = np.asarray(layer.data, dtype=np.float64)
        source = np.where(layer.valid_mask(), source, np.nan)
        src_nodata: Any = float("nan")
        out_dtype = np.float32 if layer.data.dtype == np.float32 else np.float64
    else:
        source = layer.data
        src_nodata = layer.nodata
        out_dtype = layer.data.dtype

    src_transform = layer.transform.to_affine()
    left, bottom, right, top = layer.bounds.as_tuple()

    if target_grid is not None:
        if dst_resolution is not None:
            raise ConfigError(
                "pass either target_grid or dst_resolution, not both: the "
                "target grid already fixes the cell size"
            )
        if target_shape is None:
            raise ConfigError("target_grid requires target_shape")

    resolution_arg: tuple[float, float] | None
    if dst_resolution is None:
        resolution_arg = None
    elif isinstance(dst_resolution, (int, float)):
        resolution_arg = (float(dst_resolution), float(dst_resolution))
    else:
        resolution_arg = (float(dst_resolution[0]), float(dst_resolution[1]))

    if target_grid is not None:
        dst_transform = target_grid.to_affine()
        dst_height, dst_width = int(target_shape[0]), int(target_shape[1])
    else:
        kwargs: dict[str, Any] = {}
        if resolution_arg is not None:
            kwargs["resolution"] = resolution_arg
        dst_transform, dst_width, dst_height = calculate_default_transform(
            src_crs,
            target,
            layer.width,
            layer.height,
            left=left,
            bottom=bottom,
            right=right,
            top=top,
            **kwargs,
        )

    destination = np.full((int(dst_height), int(dst_width)), dst_nodata, dtype=out_dtype)
    reproject(
        source=source,
        destination=destination,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=src_nodata,
        dst_transform=dst_transform,
        dst_crs=target,
        dst_nodata=dst_nodata,
        resampling=method,
    )

    new_grid = GridTransform.from_affine(dst_transform)
    transformation = Transformation(
        operation="reproject_raster",
        parameters={
            "src_crs": crs_to_string(src_crs),
            "dst_crs": crs_to_string(target),
            "resampling": method_name,
            "src_resolution": list(layer.resolution),
            "dst_resolution": [new_grid.x_size, new_grid.y_size],
            "requested_dst_resolution": list(resolution_arg)
            if resolution_arg
            else None,
            "shape_before": list(layer.shape),
            "shape_after": [int(dst_height), int(dst_width)],
            "src_nodata": repr(src_nodata),
            "dst_nodata": repr(dst_nodata),
            "dst_cells_square": new_grid.is_square,
            "target_grid_supplied": target_grid is not None,
            # The co-registration contract (Phase 2 item 16). The guard above
            # already *refuses* an averaging method on a categorical layer, but
            # a refusal leaves no record. Writing the kind and both affines into
            # provenance means a consumer can audit, after the fact and without
            # re-running anything, whether class codes were ever averaged and
            # whether this layer really landed on the grid it claims to share.
            "raster_kind": layer.kind.value,
            "categorical_or_continuous": layer.kind.value,
            "source_grid": layer.transform.to_dict(),
            "target_grid": new_grid.to_dict(),
        },
        notes=(
            "resampling changes values; cell size and grid origin both change. "
            "missing cells were carried as NaN through the warp so sentinels "
            "could not be averaged into real values"
            + ("" if new_grid.is_square else " | WARNING: output cells are not square")
        ),
    )
    return layer.derived(
        destination,
        name=name or f"{layer.name}_{crs_to_string(target).replace(':', '')}",
        transformation=transformation,
        transform=new_grid,
        crs=target,
        nodata=dst_nodata,
        provenance_changes={
            "output_crs": crs_to_string(target),
            "spatial_resolution": (new_grid.x_size, new_grid.y_size),
            # Read from the target CRS, never hard-coded: reprojecting to a
            # geographic CRS gives a resolution in degrees, and a provenance
            # field stating a false unit is worse than UNKNOWN (D-0009).
            "resolution_unit": resolution_unit_text(target),
        },
    )
