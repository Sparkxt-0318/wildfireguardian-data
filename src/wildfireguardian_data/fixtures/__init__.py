"""Deterministic synthetic fixtures.

Every fixture is ``DataClass.SYNTHETIC`` and says so in its provenance. These
are test inputs with analytically known answers, not simulated observations
(``docs/GLOSSARY.md``).
"""

from __future__ import annotations

from .synthetic import (
    FIXTURES,
    KOREAN_VALLEY_SIZE_M,
    SYNTHETIC_CRS,
    SYNTHETIC_ORIGIN,
    IncompatibleCRSPair,
    VillageShelterStation,
    disconnected_network,
    fixture_names,
    flat_terrain,
    incompatible_crs_pair,
    korean_valley_bounds,
    korean_valley_dem,
    korean_valley_facilities,
    korean_valley_fuels,
    korean_valley_roads,
    korean_valley_villages,
    make_fixture,
    missing_cells_raster,
    single_exit_network,
    tilted_plane,
    two_exit_network,
    village_shelter_station,
)

__all__ = [
    "SYNTHETIC_CRS",
    "SYNTHETIC_ORIGIN",
    "KOREAN_VALLEY_SIZE_M",
    "FIXTURES",
    "fixture_names",
    "make_fixture",
    "tilted_plane",
    "flat_terrain",
    "missing_cells_raster",
    "single_exit_network",
    "two_exit_network",
    "disconnected_network",
    "incompatible_crs_pair",
    "IncompatibleCRSPair",
    "village_shelter_station",
    "VillageShelterStation",
    "korean_valley_bounds",
    "korean_valley_dem",
    "korean_valley_roads",
    "korean_valley_villages",
    "korean_valley_facilities",
    "korean_valley_fuels",
]
