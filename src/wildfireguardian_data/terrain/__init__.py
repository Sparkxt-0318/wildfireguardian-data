"""Terrain: DEM ingestion, reprojection, clipping, slope, aspect, statistics.

See ``docs/ASSUMPTIONS.md`` §"Elevation, slope, and aspect" for the conventions
and ``docs/DECISIONS.md`` D-0004/D-0005/D-0006/D-0010 for why they were chosen.
"""

from __future__ import annotations

from .clip import clip_raster, clip_vector, snap_bounds_to_grid
from .derivatives import HORN_ESTIMATOR, aspect, gradient_components, slope
from .io import (
    read_geotiff,
    read_npz_raster,
    read_raster,
    write_geotiff,
    write_npz_raster,
    write_raster,
)
from .reproject import RESAMPLING_FOR_KIND, reproject_raster
from .stats import (
    CircularMean,
    categorical_statistics,
    circular_mean_deg,
    raster_statistics,
    terrain_statistics,
)

__all__ = [
    "HORN_ESTIMATOR",
    "slope",
    "aspect",
    "gradient_components",
    "clip_raster",
    "clip_vector",
    "snap_bounds_to_grid",
    "reproject_raster",
    "RESAMPLING_FOR_KIND",
    "read_geotiff",
    "write_geotiff",
    "read_npz_raster",
    "write_npz_raster",
    "read_raster",
    "write_raster",
    "raster_statistics",
    "categorical_statistics",
    "terrain_statistics",
    "circular_mean_deg",
    "CircularMean",
]
