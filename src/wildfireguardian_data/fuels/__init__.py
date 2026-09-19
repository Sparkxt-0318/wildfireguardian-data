"""Fuels / vegetation: a generic ingestion architecture.

This package deliberately ships **no real Korean fuel dataset and no
crosswalk** (``docs/ASSUMPTIONS.md`` A-FU-1). What it ships is the machinery to
ingest one correctly, plus an explicitly synthetic demo scheme.
"""

from __future__ import annotations

from .classes import SYNTHETIC_DEMO_SCHEME, FuelClass, FuelClassScheme
from .io import fuel_layer_from_array, rasterize_fuel_vector, read_fuel_geotiff

__all__ = [
    "FuelClass",
    "FuelClassScheme",
    "SYNTHETIC_DEMO_SCHEME",
    "fuel_layer_from_array",
    "read_fuel_geotiff",
    "rasterize_fuel_vector",
]
