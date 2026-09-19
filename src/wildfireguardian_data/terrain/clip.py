"""Clipping rasters and vectors to a study-area extent.

Clipping never moves the grid. The requested box is **snapped outward** to whole
cell boundaries of the layer's existing grid, so a clipped raster stays aligned
with its parent and with any sibling clipped from the same grid. Snapping
inward, or re-originating the grid at the requested corner, would introduce a
sub-cell shift that no later check could detect.

Clipping also interacts with derivatives: slope and aspect lose one cell of
valid extent per pass (``docs/DECISIONS.md`` D-0005), so clip with
``buffer_cells >= 1`` when the clipped extent is what you need slope *for*.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from shapely.geometry import box as shapely_box

from ..bounds import Bounds
from ..crs import crs_to_string, require_same_crs
from ..errors import ConfigError, RasterGeometryError
from ..provenance.models import Transformation
from ..raster import GridTransform, RasterLayer
from ..vector import Feature, VectorLayer

__all__ = ["clip_raster", "clip_vector", "snap_bounds_to_grid"]


def snap_bounds_to_grid(
    bounds: Bounds, transform: GridTransform, *, buffer_cells: int = 0
) -> tuple[int, int, int, int]:
    """Cell-index window ``(row_start, row_stop, col_start, col_stop)``.

    Half-open in both axes, snapped **outward** so the returned window fully
    contains ``bounds``, then expanded by ``buffer_cells`` on every side.
    Indices may be negative or beyond the array; the caller intersects them with
    the array's own extent, so "requested region is partly outside the data" is
    a fact the caller can report rather than one hidden by clamping here.
    """
    if buffer_cells < 0:
        raise ConfigError(f"buffer_cells must be >= 0; got {buffer_cells}")
    col_start = math.floor((bounds.min_x - transform.x_origin) / transform.x_size)
    col_stop = math.ceil((bounds.max_x - transform.x_origin) / transform.x_size)
    row_start = math.floor((transform.y_origin - bounds.max_y) / transform.y_size)
    row_stop = math.ceil((transform.y_origin - bounds.min_y) / transform.y_size)
    return (
        row_start - buffer_cells,
        row_stop + buffer_cells,
        col_start - buffer_cells,
        col_stop + buffer_cells,
    )


def clip_raster(
    layer: RasterLayer,
    bounds: Bounds,
    *,
    buffer_cells: int = 0,
    allow_partial: bool = True,
    name: str | None = None,
) -> RasterLayer:
    """Clip ``layer`` to ``bounds``, keeping the grid.

    Parameters
    ----------
    bounds:
        Target extent, **in the layer's CRS** -- mismatch raises rather than
        reprojecting (D-0002).
    buffer_cells:
        Extra cells kept on every side. Use ``>= 1`` when the result will feed
        a 3x3 derivative (D-0005).
    allow_partial:
        When the requested extent is not fully covered by the layer,
        ``True`` (default) returns the overlap and records the shortfall in
        provenance; ``False`` raises. Either way the shortfall is never filled
        with nodata cells that would imply coverage the source never had.
    """
    require_same_crs([layer.crs, bounds.crs], context=f"clipping raster {layer.name!r}")
    bounds.require_nondegenerate(f"clipping raster {layer.name!r}")

    row_start, row_stop, col_start, col_stop = snap_bounds_to_grid(
        bounds, layer.transform, buffer_cells=buffer_cells
    )
    clipped_row_start = max(0, row_start)
    clipped_col_start = max(0, col_start)
    clipped_row_stop = min(layer.height, row_stop)
    clipped_col_stop = min(layer.width, col_stop)

    if clipped_row_stop <= clipped_row_start or clipped_col_stop <= clipped_col_start:
        raise RasterGeometryError(
            f"clip extent {bounds} does not overlap layer {layer.name!r} "
            f"({layer.bounds}). an empty clip is an error, not an empty raster: "
            "an empty layer looks processed."
        )

    truncated = (
        clipped_row_start != row_start
        or clipped_col_start != col_start
        or clipped_row_stop != row_stop
        or clipped_col_stop != col_stop
    )
    if truncated and not allow_partial:
        raise RasterGeometryError(
            f"clip extent {bounds} (buffer_cells={buffer_cells}) is not fully "
            f"covered by layer {layer.name!r} ({layer.bounds}) and "
            "allow_partial=False. the source does not cover the requested "
            "study area; extending it would mean inventing cells."
        )

    data = layer.data[
        clipped_row_start:clipped_row_stop, clipped_col_start:clipped_col_stop
    ]
    new_transform = layer.transform.offset(clipped_row_start, clipped_col_start)

    transformation = Transformation(
        operation="clip_raster",
        parameters={
            "requested_bounds": list(bounds.as_tuple()),
            "crs": crs_to_string(layer.crs),
            "buffer_cells": buffer_cells,
            "window_rows": [clipped_row_start, clipped_row_stop],
            "window_cols": [clipped_col_start, clipped_col_stop],
            "snapped_to_source_grid": True,
            "requested_window_truncated": truncated,
            "shape_before": list(layer.shape),
            "shape_after": [int(data.shape[0]), int(data.shape[1])],
        },
        notes=(
            "bounds snapped outward to whole source cells; grid origin and cell "
            "size unchanged, so the clip stays aligned with its parent"
            + (
                " | requested extent exceeded the source coverage and was "
                "truncated to the overlap"
                if truncated
                else ""
            )
        ),
    )
    return layer.derived(
        np.ascontiguousarray(data),
        name=name or f"{layer.name}_clip",
        transformation=transformation,
        transform=new_transform,
    )


def clip_vector(
    layer: VectorLayer,
    bounds: Bounds,
    *,
    mode: Literal["intersects", "within", "truncate"] = "intersects",
    name: str | None = None,
) -> VectorLayer:
    """Clip a vector layer to ``bounds``.

    Parameters
    ----------
    mode:
        ``"intersects"`` (default) keeps whole features that touch the box;
        ``"within"`` keeps only features fully inside it; ``"truncate"`` cuts
        geometries at the boundary.

        The default is ``"intersects"`` because truncating a road network
        changes its topology: every cut end becomes a new degree-1 node, which
        this package would then report as a dead end. ``"truncate"`` is
        available but records a warning note in provenance, and
        ``roads.qa`` treats boundary-cut ends as *exit* nodes rather than dead
        ends when told the network was clipped (A-RD-4).
    """
    require_same_crs([layer.crs, bounds.crs], context=f"clipping vector {layer.name!r}")
    window = shapely_box(*bounds.as_tuple())

    kept: list[Feature] = []
    truncated_count = 0
    for feature in layer.features:
        geometry = feature.geometry
        if mode == "within":
            if geometry.within(window):
                kept.append(feature)
            continue
        if not geometry.intersects(window):
            continue
        if mode == "intersects":
            kept.append(feature)
            continue
        # truncate
        piece = geometry.intersection(window)
        if piece.is_empty:
            continue
        if not piece.equals(geometry):
            truncated_count += 1
        kept.append(Feature(piece, dict(feature.properties)))

    transformation = Transformation(
        operation="clip_vector",
        parameters={
            "requested_bounds": list(bounds.as_tuple()),
            "crs": crs_to_string(layer.crs),
            "mode": mode,
            "features_in": len(layer.features),
            "features_out": len(kept),
            "features_truncated": truncated_count,
        },
        notes=(
            "mode=truncate cuts geometries at the boundary, creating new "
            "degree-1 ends; treat those as boundary exits, not dead ends "
            "(docs/ASSUMPTIONS.md A-RD-4)"
            if mode == "truncate"
            else f"whole features kept by '{mode}' test; geometry unmodified"
        ),
    )
    return layer.derived(kept, name=name or f"{layer.name}_clip", transformation=transformation)
