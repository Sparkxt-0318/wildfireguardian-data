"""Terrain statistics that respect missing data and circular quantities.

Two things go wrong in naive terrain summaries, and both are prevented here:

* **Missing data contaminating a mean.** A ``-9999`` sentinel left in an array
  drags a 400 m mean elevation to something absurd, and a ``NaN`` silently
  poisons a whole reduction. Every statistic here is computed over
  :meth:`RasterLayer.valid_mask` only, and reports how many cells it ignored
  (``docs/FAILURE_MODES.md`` F-MD-2).
* **Averaging a compass direction arithmetically.** The arithmetic mean of
  aspects 350 degrees and 10 degrees is 180 degrees -- due south, the exact
  opposite of the correct due north. Aspect is summarised with circular
  statistics, and the resultant length is reported so a meaningless mean is
  visible as such (``docs/FAILURE_MODES.md`` F-TER-4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..crs import crs_to_string, require_same_crs
from ..errors import CRSError, MissingDataError, RasterAlignmentError
from ..raster import RasterKind, RasterLayer
from .io import resolution_unit_text

__all__ = [
    "CircularMean",
    "circular_mean_deg",
    "raster_statistics",
    "categorical_statistics",
    "terrain_statistics",
]


@dataclass(frozen=True)
class CircularMean:
    """Result of a circular mean over compass directions.

    ``resultant_length`` is the mean vector's length, in ``[0, 1]``: 1 means
    every direction agreed, and values near 0 mean the directions cancelled, in
    which case ``mean_deg`` is arbitrary. It is reported rather than hidden so a
    consumer can refuse to use a meaningless mean; anything below roughly 0.1
    should be treated as "no dominant aspect".
    """

    mean_deg: float | None
    resultant_length: float
    count_used: int
    count_ignored: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_deg": self.mean_deg,
            "resultant_length": self.resultant_length,
            "count_used": self.count_used,
            "count_ignored": self.count_ignored,
            "interpretation": (
                "no dominant direction (resultant_length < 0.1); mean_deg is not "
                "meaningful"
                if self.resultant_length < 0.1
                else "mean_deg is the circular mean azimuth"
            ),
        }


def circular_mean_deg(values: Any) -> CircularMean:
    """Circular mean of azimuths in degrees, ignoring ``NaN``.

    Returns ``mean_deg=None`` when nothing valid remains, rather than ``0.0``,
    which would read as due north.
    """
    array = np.asarray(values, dtype=np.float64).ravel()
    finite = np.isfinite(array)
    used = array[finite]
    ignored = int(array.size - used.size)
    if used.size == 0:
        return CircularMean(None, 0.0, 0, ignored)
    radians = np.radians(used)
    sin_sum = float(np.sin(radians).sum())
    cos_sum = float(np.cos(radians).sum())
    resultant = float(np.hypot(sin_sum, cos_sum) / used.size)
    mean = float(np.degrees(np.arctan2(sin_sum, cos_sum)))
    # Normalise into [0, 360) without using `% 360`, which maps a tiny negative
    # angle (as produced by averaging 350 and 10 degrees) to 360.0 rather than
    # to 0.0 and so reports an out-of-range azimuth.
    if mean < 0.0:
        mean += 360.0
    if mean >= 360.0:
        mean -= 360.0
    return CircularMean(mean, resultant, int(used.size), ignored)


def raster_statistics(
    layer: RasterLayer, *, percentiles: tuple[float, ...] = (5.0, 25.0, 50.0, 75.0, 95.0)
) -> dict[str, Any]:
    """Descriptive statistics of a continuous layer over valid cells only.

    Raises for a categorical layer: a mean fuel class is not a fuel class
    (A-FU-3). Use :func:`categorical_statistics` for those.
    """
    if layer.kind is not RasterKind.CONTINUOUS:
        raise MissingDataError(
            f"layer {layer.name!r} is {layer.kind.value}; arithmetic statistics "
            "over category codes are meaningless. use categorical_statistics()."
        )
    mask = layer.valid_mask()
    valid = np.asarray(layer.data, dtype=np.float64)[mask]
    total = int(layer.data.size)
    out: dict[str, Any] = {
        "layer": layer.name,
        "unit": getattr(layer.value_unit, "value", str(layer.value_unit)),
        "crs": crs_to_string(layer.crs),
        "cells_total": total,
        "cells_valid": int(valid.size),
        "cells_missing": int(total - valid.size),
        "missing_fraction": float((total - valid.size) / total) if total else None,
    }
    if valid.size == 0:
        # Explicit nulls, not zeros: "no data to summarise" and "the mean is 0"
        # are different facts and must not share a representation.
        out.update(
            {
                "min": None,
                "max": None,
                "mean": None,
                "std": None,
                "percentiles": {f"p{p:g}": None for p in percentiles},
                "note": "no valid cells; every statistic is null, not zero",
            }
        )
        return out
    out.update(
        {
            "min": float(valid.min()),
            "max": float(valid.max()),
            "mean": float(valid.mean()),
            "std": float(valid.std(ddof=0)),
            "percentiles": {
                f"p{p:g}": float(np.percentile(valid, p)) for p in percentiles
            },
        }
    )
    return out


def categorical_statistics(layer: RasterLayer) -> dict[str, Any]:
    """Class counts and fractions for a categorical layer, valid cells only."""
    if layer.kind is not RasterKind.CATEGORICAL:
        raise MissingDataError(
            f"layer {layer.name!r} is {layer.kind.value}; use raster_statistics()."
        )
    mask = layer.valid_mask()
    valid = layer.data[mask]
    total = int(layer.data.size)
    classes, counts = np.unique(valid, return_counts=True)
    return {
        "layer": layer.name,
        "crs": crs_to_string(layer.crs),
        "cells_total": total,
        "cells_valid": int(valid.size),
        "cells_missing": int(total - valid.size),
        "missing_fraction": float((total - valid.size) / total) if total else None,
        "classes": [
            {
                "code": cls.item() if hasattr(cls, "item") else cls,
                "cells": int(count),
                "fraction_of_valid": float(count / valid.size) if valid.size else None,
            }
            for cls, count in zip(classes, counts, strict=True)
        ],
        "note": (
            "fractions are of valid cells, not of the whole raster; compare "
            "cells_missing before using them as landscape composition"
        ),
    }


def terrain_statistics(
    dem: RasterLayer,
    *,
    slope_layer: RasterLayer | None = None,
    aspect_layer: RasterLayer | None = None,
) -> dict[str, Any]:
    """Combined elevation / slope / aspect summary for a study area.

    Every component reports its own valid and missing counts. Slope and aspect
    have systematically *more* missing cells than the DEM, because derivatives
    drop the array edge (D-0005) -- so a summary that reported only the DEM's
    coverage would overstate how much of the area has usable slope.
    """
    # This function combines up to three layers, so it goes through the same
    # choke point every other cross-layer operation does (D-0002). Without it a
    # "terrain summary" could carry a slope block from a different CRS,
    # resolution and extent under a single top-level crs field.
    companions = [layer for layer in (slope_layer, aspect_layer) if layer is not None]
    if companions:
        require_same_crs(
            [dem.crs, *(layer.crs for layer in companions)],
            context=f"summarising terrain for {dem.name!r}",
        )
        for layer in companions:
            if layer.shape != dem.shape or layer.transform != dem.transform:
                raise RasterAlignmentError(
                    f"layer {layer.name!r} is not on the DEM's grid "
                    f"(shape {layer.shape} vs {dem.shape}, cell size "
                    f"{layer.resolution} vs {dem.resolution}); a combined terrain "
                    "summary of layers that do not correspond cell-for-cell would "
                    "attribute one layer's statistics to another's extent."
                )

    elevation = raster_statistics(dem)
    out: dict[str, Any] = {
        "elevation": elevation,
        # The unit is a field, not part of the key: naming it `relief_m` was
        # wrong for a DEM in any other length unit, and a summary that hard-codes
        # a unit it did not check is the mislabelling this package exists to
        # prevent.
        "relief": (
            elevation["max"] - elevation["min"]
            if elevation["min"] is not None
            else None
        ),
        "relief_unit": elevation["unit"],
        "cell_size": list(dem.resolution),
        "cell_size_unit": resolution_unit_text(dem.crs),
        "grid_shape": list(dem.shape),
        "crs": crs_to_string(dem.crs),
    }
    try:
        out["valid_area_m2"] = float(dem.valid_count * dem.cell_area())
    except CRSError as exc:
        # A geographic or non-metre CRS has no metre area. Reported as a stated
        # reason rather than a null, and narrowed to the error that can actually
        # occur here so an unrelated failure is not swallowed (AGENTS.md §7).
        out["valid_area_m2"] = None
        out["valid_area_note"] = f"not computed: {type(exc).__name__}: {exc}"

    if slope_layer is not None:
        slope_stats = raster_statistics(slope_layer)
        slope_stats["note"] = (
            "derivative layers lose the array edge and every cell beside missing "
            "data, so cells_missing here exceeds the DEM's "
            "(docs/DECISIONS.md D-0005)"
        )
        out["slope"] = slope_stats

    if aspect_layer is not None:
        # Through the valid mask, not the raw array. circular_mean_deg drops
        # non-finite values only, so a declared numeric nodata would survive it:
        # an aspect layer arriving from another tool with nodata=-9999 would have
        # that read as a real north-easterly azimuth (-9999 deg == 81 deg),
        # shifting the reported dominant aspect by tens of degrees. This module
        # promises every statistic is over valid cells only; that has to include
        # this one.
        masked_aspect = np.where(
            aspect_layer.valid_mask(),
            np.asarray(aspect_layer.data, dtype=np.float64),
            np.nan,
        )
        circular = circular_mean_deg(masked_aspect)
        out["aspect"] = {
            "layer": aspect_layer.name,
            "unit": "deg_azimuth_grid_north",
            "circular_mean": circular.to_dict(),
            "cells_total": int(aspect_layer.data.size),
            "cells_valid": aspect_layer.valid_count,
            "cells_missing": aspect_layer.missing_count,
            "note": (
                "NaN here means either an incomplete neighbourhood or exactly "
                "flat ground; the two are not distinguished in the array. "
                "arithmetic means of azimuths are wrong and are not offered "
                "(docs/FAILURE_MODES.md F-TER-4)."
            ),
        }
    return out
