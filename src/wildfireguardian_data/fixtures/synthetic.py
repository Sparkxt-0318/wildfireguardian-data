"""Deterministic synthetic fixtures with analytically known properties.

Everything here is :attr:`DataClass.SYNTHETIC` and says so in its provenance.
These are **test inputs with known answers**, not simulated observations of a
modelled truth (``docs/GLOSSARY.md``; OSSE work is out of scope per
``docs/SCOPE.md``).

Deterministic by construction: the surfaces are closed-form, and the one
fixture with noise takes a recorded seed (A-REP-1).

The eight fixtures the phase-1 plan requires:

============================ ======================================== ==========================
fixture                      what it is                               what is analytically known
============================ ======================================== ==========================
``tilted_plane``             planar surface ``z = A*x + B*y + C``      slope and aspect, exactly
``flat_terrain``             constant elevation                        slope 0, aspect NaN
``single_exit_network``      chain from boundary to a hamlet           1 component, 1 exit, 3 critical links
``two_exit_network``         loop touching the boundary twice          1 component, 2 exits, 0 critical links
``disconnected_network``     two components, one with no exit          2 components, 1 isolated segment
``incompatible_crs_pair``    same geometry in EPSG:5187 and EPSG:4326  every cross-layer op must raise
``missing_cells_raster``     tilted plane with a void and a sentinel   exact count of cells lost to derivatives
``village_shelter_station``  village polygon + shelter + fire station  geometry and counts
============================ ======================================== ==========================

Plus ``korean_valley*``: a composite, larger, still explicitly synthetic study
area used for the example bundle. Its identifier carries ``synthetic`` because
it is placed on real Korean coordinates, and a bundle named after a real place
must not be mistakable for real data (``AGENTS.md`` §4).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np
from shapely.geometry import LineString, Point, Polygon

from ..bounds import Bounds
from ..crs import crs_to_string
from ..fuels.classes import SYNTHETIC_DEMO_SCHEME, FuelClassScheme
from ..fuels.io import fuel_layer_from_array
from ..provenance.models import (
    NOT_APPLICABLE,
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
    Transformation,
)
from ..raster import GridTransform, RasterKind, RasterLayer
from ..terrain.io import resolution_unit_text
from ..vector import Feature, VectorLayer

__all__ = [
    "SYNTHETIC_CRS",
    "SYNTHETIC_ORIGIN",
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
    "village_shelter_station",
    "korean_valley_dem",
    "korean_valley_roads",
    "korean_valley_villages",
    "korean_valley_facilities",
    "korean_valley_fuels",
    "korean_valley_bounds",
]

#: The projected CRS the fixtures use. EPSG:5187 (KGD2002 / East Belt 2010)
#: covers the eastern Korean coast, including the Uljin area that the 2022
#: Uljin-Samcheok wildfire burned -- a realistic setting for the geometry
#: *shape*, while the data itself remains entirely synthetic.
SYNTHETIC_CRS = "EPSG:5187"

#: Lower-left corner used by the composite fixtures, in :data:`SYNTHETIC_CRS`.
#: Chosen to fall in the rural hills inland of Uljin so that coordinates look
#: like real Korean coordinates and a CRS mix-up is visible rather than
#: plausible.
SYNTHETIC_ORIGIN = (226_000.0, 477_000.0)


def _synthetic_source(generator: str, seed: int | None = None) -> SourceRecord:
    return SourceRecord(
        name=f"wildfireguardian_data.fixtures.synthetic.{generator}",
        url_or_identifier="in-repository synthetic generator",
        source_date="not_applicable",
        acquisition_date="not_applicable",
        publisher="wildfireguardian-data (this repository)",
        licence="same as this repository",
        notes=(
            "SYNTHETIC test input. describes no real place. "
            + (f"seed={seed}. " if seed is not None else "")
            + "any resemblance to Korean terrain is limited to the coordinate "
            "range used."
        ),
    )


def _synthetic_raster_provenance(
    *,
    name: str,
    generator: str,
    parameters: dict[str, Any],
    crs: Any,
    transform: GridTransform,
    value_unit: str,
    nodata_text: str,
    seed: int | None = None,
    notes: str = "",
) -> ProvenanceRecord:
    return ProvenanceRecord(
        layer_name=name,
        data_class=DataClass.SYNTHETIC,
        temporal_class=TemporalProvenance.STATIC,
        temporal_reference="not_applicable",
        sources=(_synthetic_source(generator, seed),),
        transformations=(
            Transformation(
                operation=f"synthesise_{generator}",
                parameters=parameters,
                notes="closed-form construction; deterministic",
            ),
        ),
        original_crs=crs_to_string(crs),
        output_crs=crs_to_string(crs),
        spatial_resolution=(transform.x_size, transform.y_size),
        # From the CRS, not assumed: incompatible_crs_pair builds a fixture on a
        # degree grid, whose resolution is in degrees and not in metres.
        resolution_unit=resolution_unit_text(crs),
        value_unit=value_unit,
        vertical_datum="not_applicable (synthetic)",
        nodata_representation=nodata_text,
        # A synthetic surface has no canopy, so it *is* bare earth. "dtm" is
        # accurate rather than a placeholder, and it is what lets the analytic
        # tests assert that a computed slope is terrain slope and not a canopy
        # artifact (F-TER-3).
        surface_model="dtm",
        # A synthetic construct describes no moment in the world, so there is
        # no validity interval to be unknown about. NOT_APPLICABLE, not
        # UNKNOWN: only UNKNOWN counts as a gap (D-0009).
        valid_from=NOT_APPLICABLE,
        valid_to=NOT_APPLICABLE,
        random_seed=seed,
        notes=("SYNTHETIC. " + notes).strip(),
    )


def _synthetic_vector_provenance(
    *, name: str, generator: str, parameters: dict[str, Any], crs: Any, notes: str = ""
) -> ProvenanceRecord:
    return ProvenanceRecord(
        layer_name=name,
        data_class=DataClass.SYNTHETIC,
        temporal_class=TemporalProvenance.STATIC,
        temporal_reference="not_applicable",
        sources=(_synthetic_source(generator),),
        transformations=(
            Transformation(
                operation=f"synthesise_{generator}",
                parameters=parameters,
                notes="hand-constructed geometry with known topology",
            ),
        ),
        original_crs=crs_to_string(crs),
        output_crs=crs_to_string(crs),
        value_unit="not_applicable",
        nodata_representation="not_applicable",
        surface_model=NOT_APPLICABLE,
        valid_from=NOT_APPLICABLE,
        valid_to=NOT_APPLICABLE,
        notes=("SYNTHETIC. " + notes).strip(),
    )


# --------------------------------------------------------------------------- #
# Terrain fixtures
# --------------------------------------------------------------------------- #
def tilted_plane(
    *,
    name: str = "synthetic_tilted_plane",
    shape: tuple[int, int] = (25, 25),
    cell_size_m: float = 30.0,
    dz_dx: float = 0.20,
    dz_dy: float = -0.10,
    base_elevation_m: float = 400.0,
    crs: Any = SYNTHETIC_CRS,
    origin: tuple[float, float] = SYNTHETIC_ORIGIN,
) -> RasterLayer:
    """A planar surface ``z = dz_dx * x + dz_dy * y + c``.

    Horn's estimator is **exact** on a plane, so the expected results are
    closed-form and a test against them is a correctness test, not a tolerance
    exercise:

    * slope ``= atan(hypot(dz_dx, dz_dy))`` everywhere in the interior;
    * aspect ``= atan2(-dz_dx, -dz_dy) mod 360`` everywhere in the interior.

    With the defaults (``dz_dx=0.2`` rising eastward, ``dz_dy=-0.1`` falling
    northward) the surface faces west-north-west: slope 12.6026 degrees, aspect
    296.5651 degrees.
    """
    height, width = shape
    rows, cols = np.mgrid[0:height, 0:width]
    # Local metres east / north of the grid's lower-left corner. Using local
    # offsets rather than absolute easting keeps the elevation values plausible;
    # the gradient, which is what the test checks, is identical either way.
    east = cols * cell_size_m
    north = (height - 1 - rows) * cell_size_m
    data = (base_elevation_m + dz_dx * east + dz_dy * north).astype(np.float64)

    transform = GridTransform(
        x_origin=origin[0],
        y_origin=origin[1] + height * cell_size_m,
        x_size=cell_size_m,
        y_size=cell_size_m,
    )
    expected_slope = math.degrees(math.atan(math.hypot(dz_dx, dz_dy)))
    expected_aspect = math.degrees(math.atan2(-dz_dx, -dz_dy)) % 360.0
    provenance = _synthetic_raster_provenance(
        name=name,
        generator="tilted_plane",
        parameters={
            "shape": [height, width],
            "cell_size_m": cell_size_m,
            "dz_dx": dz_dx,
            "dz_dy": dz_dy,
            "base_elevation_m": base_elevation_m,
            "expected_slope_deg": expected_slope,
            "expected_aspect_deg": expected_aspect,
        },
        crs=crs,
        transform=transform,
        value_unit="m",
        nodata_text="nan",
        notes=(
            f"planar surface; Horn slope should equal {expected_slope:.6f} deg and "
            f"aspect {expected_aspect:.6f} deg in the interior"
        ),
    )
    return RasterLayer(
        name=name,
        data=data,
        transform=transform,
        crs=crs,
        nodata=float("nan"),
        kind=RasterKind.CONTINUOUS,
        value_unit="m",
        provenance=provenance,
    )


def flat_terrain(
    *,
    name: str = "synthetic_flat_terrain",
    shape: tuple[int, int] = (20, 20),
    cell_size_m: float = 30.0,
    elevation_m: float = 412.0,
    crs: Any = SYNTHETIC_CRS,
    origin: tuple[float, float] = SYNTHETIC_ORIGIN,
) -> RasterLayer:
    """Constant elevation: slope is exactly 0 and aspect is entirely ``NaN``.

    The fixture that catches the ``0``-for-flat-aspect bug (D-0006): a flat
    surface must produce *no* aspect value, not "faces north".
    """
    height, width = shape
    data = np.full((height, width), float(elevation_m), dtype=np.float64)
    transform = GridTransform(
        origin[0], origin[1] + height * cell_size_m, cell_size_m, cell_size_m
    )
    provenance = _synthetic_raster_provenance(
        name=name,
        generator="flat_terrain",
        parameters={
            "shape": [height, width],
            "cell_size_m": cell_size_m,
            "elevation_m": elevation_m,
            "expected_slope_deg": 0.0,
            "expected_aspect": "nan everywhere",
        },
        crs=crs,
        transform=transform,
        value_unit="m",
        nodata_text="nan",
        notes="slope is exactly 0; aspect must be NaN everywhere, never 0",
    )
    return RasterLayer(
        name=name,
        data=data,
        transform=transform,
        crs=crs,
        nodata=float("nan"),
        kind=RasterKind.CONTINUOUS,
        value_unit="m",
        provenance=provenance,
    )


def missing_cells_raster(
    *,
    name: str = "synthetic_missing_cells",
    shape: tuple[int, int] = (15, 15),
    cell_size_m: float = 30.0,
    void_slice: tuple[slice, slice] | None = None,
    sentinel_cells: Iterable[tuple[int, int]] = ((10, 10),),
    sentinel_value: float = -9999.0,
    crs: Any = SYNTHETIC_CRS,
    origin: tuple[float, float] = SYNTHETIC_ORIGIN,
) -> tuple[RasterLayer, RasterLayer]:
    """A tilted plane with a missing-data void, in **two** representations.

    Returns ``(nan_layer, sentinel_layer)``:

    * ``nan_layer`` marks the void with ``NaN`` and declares ``nodata=NaN``;
    * ``sentinel_layer`` marks the *same* void with ``-9999`` and declares
      ``nodata=-9999``.

    Two representations because they fail differently. ``NaN`` propagates
    loudly through any arithmetic, whereas ``-9999`` propagates *silently* and
    produces a plausible-looking mean. A statistic computed correctly must give
    the **same answer for both**, and that equality is the strongest available
    test that missing data is being honoured rather than averaged
    (``docs/FAILURE_MODES.md`` F-MD-2).
    """
    base = tilted_plane(
        name=f"{name}_base", shape=shape, cell_size_m=cell_size_m, crs=crs, origin=origin
    )
    height, width = shape
    if void_slice is None:
        void_slice = (slice(3, 7), slice(3, 7))

    nan_data = np.array(base.data, dtype=np.float64)
    nan_data[void_slice] = np.nan
    for row, col in sentinel_cells:
        nan_data[row, col] = np.nan

    sentinel_data = np.array(base.data, dtype=np.float64)
    sentinel_data[void_slice] = sentinel_value
    for row, col in sentinel_cells:
        sentinel_data[row, col] = sentinel_value

    missing_count = int(np.isnan(nan_data).sum())
    parameters = {
        "shape": [height, width],
        "cell_size_m": cell_size_m,
        "void_rows": [void_slice[0].start, void_slice[0].stop],
        "void_cols": [void_slice[1].start, void_slice[1].stop],
        "extra_missing_cells": [list(c) for c in sentinel_cells],
        "missing_cell_count": missing_count,
        "parent_fixture": "tilted_plane",
    }

    nan_layer = RasterLayer(
        name=f"{name}_nan",
        data=nan_data,
        transform=base.transform,
        crs=crs,
        nodata=float("nan"),
        kind=RasterKind.CONTINUOUS,
        value_unit="m",
        provenance=_synthetic_raster_provenance(
            name=f"{name}_nan",
            generator="missing_cells_raster",
            parameters={**parameters, "representation": "nan"},
            crs=crs,
            transform=base.transform,
            value_unit="m",
            nodata_text="nan",
            notes=f"{missing_count} missing cells marked with NaN",
        ),
    )
    sentinel_layer = RasterLayer(
        name=f"{name}_sentinel",
        data=sentinel_data,
        transform=base.transform,
        crs=crs,
        nodata=sentinel_value,
        kind=RasterKind.CONTINUOUS,
        value_unit="m",
        provenance=_synthetic_raster_provenance(
            name=f"{name}_sentinel",
            generator="missing_cells_raster",
            parameters={**parameters, "representation": repr(sentinel_value)},
            crs=crs,
            transform=base.transform,
            value_unit="m",
            nodata_text=repr(sentinel_value),
            notes=(
                f"{missing_count} missing cells marked with {sentinel_value}; "
                "statistics must match the NaN variant exactly"
            ),
        ),
    )
    return nan_layer, sentinel_layer


# --------------------------------------------------------------------------- #
# Road-network fixtures
# --------------------------------------------------------------------------- #
def _road_layer(
    name: str,
    segments: list[tuple[str, list[tuple[float, float]], dict[str, Any]]],
    *,
    crs: Any,
    generator: str,
    expected: dict[str, Any],
) -> VectorLayer:
    features = tuple(
        Feature(LineString(coords), {"segment_id": segment_id, **properties})
        for segment_id, coords, properties in segments
    )
    provenance = _synthetic_vector_provenance(
        name=name,
        generator=generator,
        parameters={"segments": len(features), "expected": expected},
        crs=crs,
        notes=(
            "topology is known by construction: "
            + ", ".join(f"{k}={v}" for k, v in expected.items())
        ),
    )
    return VectorLayer(
        name=name, features=features, crs=crs, provenance=provenance, feature_kind="road segment"
    )


def single_exit_network(
    *, name: str = "synthetic_single_exit_roads", crs: Any = SYNTHETIC_CRS, origin: tuple[float, float] = SYNTHETIC_ORIGIN
) -> VectorLayer:
    """One hamlet reachable only through a single chain from the boundary.

    Known topology within the 3 km box starting at ``origin``: 1 connected
    component, 1 boundary exit node, 3 edges, and every edge a critical link for
    the hamlet.
    """
    x0, y0 = origin
    segments = [
        ("access_road", [(x0, y0 + 1500), (x0 + 1000, y0 + 1500)], {"road_class": "local"}),
        ("valley_road", [(x0 + 1000, y0 + 1500), (x0 + 2000, y0 + 1500)], {"road_class": "local"}),
        ("hamlet_lane", [(x0 + 2000, y0 + 1500), (x0 + 2000, y0 + 1900)], {"road_class": "track"}),
    ]
    return _road_layer(
        name,
        segments,
        crs=crs,
        generator="single_exit_network",
        expected={
            "components": 1,
            "boundary_exit_nodes": 1,
            "edges": 3,
            "critical_links_for_hamlet": 3,
        },
    )


def two_exit_network(
    *, name: str = "synthetic_two_exit_roads", crs: Any = SYNTHETIC_CRS, origin: tuple[float, float] = SYNTHETIC_ORIGIN
) -> VectorLayer:
    """A hamlet on a loop that touches the study boundary twice.

    Known topology: 1 component, 2 boundary exit nodes, and **no** critical
    link for the hamlet -- removing any single edge still leaves a path to an
    exit. The counterpart to :func:`single_exit_network`, and the fixture that
    catches a critical-link finder that reports every bridge regardless of
    whether it actually cuts a settlement off.
    """
    x0, y0 = origin
    segments = [
        ("west_access", [(x0, y0 + 1500), (x0 + 1000, y0 + 1500)], {"road_class": "local"}),
        ("hamlet_north", [(x0 + 1000, y0 + 1500), (x0 + 1500, y0 + 2000)], {"road_class": "local"}),
        ("hamlet_south", [(x0 + 1000, y0 + 1500), (x0 + 1500, y0 + 1000)], {"road_class": "local"}),
        ("east_north", [(x0 + 1500, y0 + 2000), (x0 + 3000, y0 + 1750)], {"road_class": "local"}),
        ("east_south", [(x0 + 1500, y0 + 1000), (x0 + 3000, y0 + 1250)], {"road_class": "local"}),
        ("hamlet_link", [(x0 + 1500, y0 + 2000), (x0 + 1500, y0 + 1000)], {"road_class": "track"}),
    ]
    return _road_layer(
        name,
        segments,
        crs=crs,
        generator="two_exit_network",
        expected={"components": 1, "boundary_exit_nodes": 3, "critical_links_for_hamlet": 0},
    )


def disconnected_network(
    *, name: str = "synthetic_disconnected_roads", crs: Any = SYNTHETIC_CRS, origin: tuple[float, float] = SYNTHETIC_ORIGIN
) -> VectorLayer:
    """Two components, one of them a single orphan segment with no exit.

    Known topology: 2 components, 1 isolated segment, and -- with the two
    crossing-but-unnoded segments included -- exactly 1 crossing without a
    shared node, which is the D-0007 diagnostic's positive case.
    """
    x0, y0 = origin
    segments = [
        ("main_road", [(x0, y0 + 1500), (x0 + 1500, y0 + 1500)], {"road_class": "local"}),
        ("side_road", [(x0 + 1500, y0 + 1500), (x0 + 1500, y0 + 2200)], {"road_class": "track"}),
        # Crosses main_road mid-segment with no shared node: a grade-separated
        # crossing as far as the data is concerned (D-0007).
        ("overpass", [(x0 + 800, y0 + 1000), (x0 + 800, y0 + 2000)], {"road_class": "local", "layer": 1}),
        # Orphan: touches nothing.
        ("orphan_track", [(x0 + 2500, y0 + 400), (x0 + 2700, y0 + 400)], {"road_class": "track"}),
    ]
    return _road_layer(
        name,
        segments,
        crs=crs,
        generator="disconnected_network",
        expected={
            "components": 3,
            "isolated_segments": 2,
            "crossings_without_node": 1,
        },
    )


# --------------------------------------------------------------------------- #
# CRS-incompatibility fixture
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class IncompatibleCRSPair:
    """The *same* place in two CRSs, for testing that mixing them fails.

    ``projected`` and ``geographic`` describe the same geometry. Any operation
    that combines them must raise
    :class:`~wildfireguardian_data.errors.CRSMismatchError`; a function that
    quietly reprojects one would produce output that looks fine and is wrong by
    the whole width of the projection (``docs/DECISIONS.md`` D-0002).
    """

    projected: VectorLayer
    geographic: VectorLayer
    projected_raster: RasterLayer
    geographic_raster: RasterLayer


def incompatible_crs_pair(*, name: str = "synthetic_incompatible_crs") -> IncompatibleCRSPair:
    """Build an :class:`IncompatibleCRSPair`."""
    projected = single_exit_network(name=f"{name}_projected_5187", crs="EPSG:5187")
    geographic = projected.reproject("EPSG:4326", name=f"{name}_geographic_4326")

    projected_raster = tilted_plane(name=f"{name}_raster_5187", shape=(12, 12), crs="EPSG:5187")
    geographic_raster = RasterLayer(
        name=f"{name}_raster_4326",
        data=np.array(projected_raster.data, dtype=np.float64),
        # Degrees, with a cell size that looks like a Copernicus GLO-30 grid.
        # Deliberately the same array on a degree grid: the numbers are
        # identical, so only the CRS metadata distinguishes them, which is
        # exactly the situation in which a silent combination goes unnoticed.
        transform=GridTransform(129.30, 36.96, 1.0 / 3600.0, 1.0 / 3600.0),
        crs="EPSG:4326",
        nodata=float("nan"),
        kind=RasterKind.CONTINUOUS,
        value_unit="m",
        provenance=_synthetic_raster_provenance(
            name=f"{name}_raster_4326",
            generator="incompatible_crs_pair",
            parameters={"note": "same values as the 5187 raster, on a degree grid"},
            crs="EPSG:4326",
            transform=GridTransform(129.30, 36.96, 1.0 / 3600.0, 1.0 / 3600.0),
            value_unit="m",
            nodata_text="nan",
            notes=(
                "geographic CRS: slope and length operations must refuse this "
                "layer (docs/DECISIONS.md D-0010)"
            ),
        ),
    )
    return IncompatibleCRSPair(
        projected=projected,
        geographic=geographic,
        projected_raster=projected_raster,
        geographic_raster=geographic_raster,
    )


# --------------------------------------------------------------------------- #
# Village / facility fixture
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class VillageShelterStation:
    """A minimal settlement-and-facility scene."""

    villages: VectorLayer
    facilities: VectorLayer


def village_shelter_station(
    *,
    name: str = "synthetic_village_scene",
    crs: Any = SYNTHETIC_CRS,
    origin: tuple[float, float] = SYNTHETIC_ORIGIN,
) -> VillageShelterStation:
    """One village polygon, one shelter, and one fire station.

    Population figures are invented and labelled synthetic. They are *aggregate*
    only, and the age strata sum exactly to the stated total so that the
    consistency check has a clean positive case; ``korean_valley_villages``
    provides the mismatching case.
    """
    x0, y0 = origin
    village_polygon = Polygon(
        [
            (x0 + 1850, y0 + 1400),
            (x0 + 2150, y0 + 1400),
            (x0 + 2150, y0 + 1700),
            (x0 + 1850, y0 + 1700),
        ]
    )
    villages = VectorLayer(
        name=f"{name}_villages",
        features=(
            Feature(
                village_polygon,
                {
                    "settlement_id": "SYN-V-001",
                    "name": "Synthetic Hamlet A",
                    "population_total": 42,
                    "pop_0_14": 2,
                    "pop_15_64": 15,
                    "pop_65_plus": 25,
                    "count_basis": "synthetic_invented",
                    "reference_date": "not_applicable",
                },
            ),
        ),
        crs=crs,
        provenance=_synthetic_vector_provenance(
            name=f"{name}_villages",
            generator="village_shelter_station",
            parameters={
                "settlements": 1,
                "population_total": 42,
                "strata_sum": 42,
                "aggregation_level": "synthetic_village",
            },
            crs=crs,
            notes=(
                "aggregate population only; invented figures. strata sum equals "
                "the total by construction"
            ),
        ),
        feature_kind="settlement",
    )
    facilities = VectorLayer(
        name=f"{name}_facilities",
        features=(
            Feature(
                Point(x0 + 2000, y0 + 1550),
                {
                    "facility_id": "SYN-F-001",
                    "name": "Synthetic village hall",
                    "kind": "village_hall",
                    "capacity_persons": None,
                    "operational_status": "UNKNOWN",
                },
            ),
            Feature(
                Point(x0 + 700, y0 + 1500),
                {
                    "facility_id": "SYN-F-002",
                    "name": "Synthetic fire station",
                    "kind": "fire_station",
                    "capacity_persons": None,
                    "operational_status": "UNKNOWN",
                },
            ),
        ),
        crs=crs,
        provenance=_synthetic_vector_provenance(
            name=f"{name}_facilities",
            generator="village_shelter_station",
            parameters={"facilities": 2, "kinds": ["village_hall", "fire_station"]},
            crs=crs,
            notes=(
                "roles are declared tags only; neither facility is assessed for "
                "suitability, and capacity is deliberately absent "
                "(docs/ASSUMPTIONS.md A-FAC-1/3)"
            ),
        ),
        feature_kind="facility",
    )
    return VillageShelterStation(villages=villages, facilities=facilities)


# --------------------------------------------------------------------------- #
# Composite "Korean valley" fixture, used by the example bundle
# --------------------------------------------------------------------------- #
#: Extent of the composite synthetic study area, in :data:`SYNTHETIC_CRS`.
KOREAN_VALLEY_SIZE_M = 6_000.0


def korean_valley_bounds(
    *, crs: Any = SYNTHETIC_CRS, origin: tuple[float, float] = SYNTHETIC_ORIGIN
) -> Bounds:
    """The composite fixture's study-area extent."""
    return Bounds(
        origin[0],
        origin[1],
        origin[0] + KOREAN_VALLEY_SIZE_M,
        origin[1] + KOREAN_VALLEY_SIZE_M,
        crs=crs,
    )


def korean_valley_dem(
    *,
    name: str = "dem",
    cell_size_m: float = 30.0,
    crs: Any = SYNTHETIC_CRS,
    origin: tuple[float, float] = SYNTHETIC_ORIGIN,
    seed: int = 20260919,
    noise_m: float = 3.0,
    margin_cells: int = 4,
) -> RasterLayer:
    """A synthetic valley: two ridges, a valley floor, and seeded noise.

    Closed-form plus seeded noise, so it is reproducible (A-REP-1) while still
    exercising code paths that a perfect plane does not -- a real aspect
    distribution, a non-trivial slope histogram, and a valley axis that a reader
    can recognise in a plot.

    Built with ``margin_cells`` of extra grid beyond the study bounds so the
    3x3 derivative has a neighbourhood at the study-area edge (D-0005).
    """
    size = KOREAN_VALLEY_SIZE_M
    cells = int(round(size / cell_size_m)) + 2 * margin_cells
    transform = GridTransform(
        x_origin=origin[0] - margin_cells * cell_size_m,
        y_origin=origin[1] + size + margin_cells * cell_size_m,
        x_size=cell_size_m,
        y_size=cell_size_m,
    )
    rows, cols = np.mgrid[0:cells, 0:cells]
    east = cols * cell_size_m
    north = (cells - 1 - rows) * cell_size_m

    # A valley running roughly WSW-ENE, with a ridge either side and a gentle
    # downstream gradient towards the east coast. The coefficients are chosen so
    # the surface lands in a plausible range for the rural hills inland of the
    # eastern Korean coast -- roughly 170-800 m elevation with mean slope in the
    # mid-teens of degrees. That plausibility matters only for making the
    # example bundle legible to a reader; the layer is still synthetic and
    # describes no real valley.
    axis_north = 0.5 * size + 0.10 * (east - 0.5 * size)
    across = north - axis_north
    valley = 200.0 + 0.11 * np.abs(across) - 0.008 * east
    ridge = 150.0 * np.exp(-((across - 1800.0) / 700.0) ** 2)
    ridge += 110.0 * np.exp(-((across + 1700.0) / 600.0) ** 2)
    data = valley + ridge

    rng = np.random.default_rng(seed)
    data = data + rng.normal(0.0, noise_m, size=data.shape)
    data = np.asarray(data, dtype=np.float32)

    provenance = _synthetic_raster_provenance(
        name=name,
        generator="korean_valley_dem",
        parameters={
            "shape": [cells, cells],
            "cell_size_m": cell_size_m,
            "study_area_size_m": size,
            "margin_cells": margin_cells,
            "noise_sigma_m": noise_m,
            "seed": seed,
            "form": "valley axis + two gaussian ridges + eastward gradient",
            "intended_elevation_range_m": "approximately 170-800",
        },
        crs=crs,
        transform=transform,
        value_unit="m",
        nodata_text="nan",
        seed=seed,
        notes=(
            "composite synthetic terrain for the example bundle. NOT a model of "
            "any real Korean valley, and not a DSM or DTM of anywhere. includes "
            f"{margin_cells} cells of margin so slope covers the study area"
        ),
    )
    return RasterLayer(
        name=name,
        data=data,
        transform=transform,
        crs=crs,
        nodata=float("nan"),
        kind=RasterKind.CONTINUOUS,
        value_unit="m",
        provenance=provenance,
    )


def korean_valley_roads(
    *, name: str = "roads", crs: Any = SYNTHETIC_CRS, origin: tuple[float, float] = SYNTHETIC_ORIGIN
) -> VectorLayer:
    """Road network for the composite fixture.

    Deliberately mixed topology, so the example bundle's QA report is
    interesting rather than clean: one hamlet on a single-egress spur, one on a
    two-way loop, one orphan forest track, and one unnoded crossing.
    """
    x0, y0 = origin
    segments = [
        # Valley trunk, crossing the study area west to east (two boundary exits).
        ("trunk_w", [(x0, y0 + 2600), (x0 + 1500, y0 + 2800)], {"road_class": "secondary"}),
        ("trunk_m", [(x0 + 1500, y0 + 2800), (x0 + 3200, y0 + 3000)], {"road_class": "secondary"}),
        ("trunk_e", [(x0 + 3200, y0 + 3000), (x0 + 6000, y0 + 3300)], {"road_class": "secondary"}),
        # Hamlet A: single-egress spur off the trunk.
        ("spur_a1", [(x0 + 1500, y0 + 2800), (x0 + 1400, y0 + 4000)], {"road_class": "local"}),
        ("spur_a2", [(x0 + 1400, y0 + 4000), (x0 + 1600, y0 + 4600)], {"road_class": "track"}),
        # Hamlet B: loop with two connections to the trunk.
        ("loop_b1", [(x0 + 3200, y0 + 3000), (x0 + 3600, y0 + 1800)], {"road_class": "local"}),
        ("loop_b2", [(x0 + 3600, y0 + 1800), (x0 + 4400, y0 + 1900)], {"road_class": "local"}),
        ("loop_b3", [(x0 + 4400, y0 + 1900), (x0 + 4600, y0 + 3100)], {"road_class": "local"}),
        ("loop_b4", [(x0 + 4600, y0 + 3100), (x0 + 3200, y0 + 3000)], {"road_class": "local"}),
        # Forest track crossing the trunk with no shared node (D-0007 case).
        ("forest_track", [(x0 + 2400, y0 + 2000), (x0 + 2600, y0 + 4200)], {"road_class": "track"}),
        # Orphan segment, disconnected as digitised.
        ("orphan_spur", [(x0 + 5200, y0 + 800), (x0 + 5500, y0 + 1000)], {"road_class": "track"}),
    ]
    return _road_layer(
        name,
        segments,
        crs=crs,
        generator="korean_valley_roads",
        expected={
            "hamlet_a": "single-egress spur",
            "hamlet_b": "two-connection loop",
            "orphan_spur": "isolated segment",
            "forest_track": "crosses trunk without a node",
        },
    )


def korean_valley_villages(
    *, name: str = "villages", crs: Any = SYNTHETIC_CRS, origin: tuple[float, float] = SYNTHETIC_ORIGIN
) -> VectorLayer:
    """Three synthetic settlements with aggregate, invented population.

    One of them (``SYN-V-003``) deliberately has age strata that do **not** sum
    to its stated total, and another has a total below the k-anonymity floor, so
    the example bundle exercises POP-001 and POP-003 rather than only the happy
    path.
    """
    x0, y0 = origin

    def _square(cx: float, cy: float, half: float) -> Polygon:
        return Polygon(
            [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)]
        )

    features = (
        Feature(
            _square(x0 + 1600, y0 + 4600, 220.0),
            {
                "settlement_id": "SYN-V-001",
                "name": "Synthetic Hamlet A (single-egress spur)",
                "population_total": 38,
                "pop_0_14": 1,
                "pop_15_64": 12,
                "pop_65_plus": 25,
                "count_basis": "synthetic_invented",
                "reference_date": "not_applicable",
            },
        ),
        Feature(
            _square(x0 + 4000, y0 + 1850, 260.0),
            {
                "settlement_id": "SYN-V-002",
                "name": "Synthetic Hamlet B (loop)",
                "population_total": 120,
                "pop_0_14": 9,
                "pop_15_64": 66,
                "pop_65_plus": 45,
                "count_basis": "synthetic_invented",
                "reference_date": "not_applicable",
            },
        ),
        Feature(
            _square(x0 + 5350, y0 + 900, 150.0),
            {
                "settlement_id": "SYN-V-003",
                "name": "Synthetic Hamlet C (orphan track)",
                "population_total": 3,
                "pop_0_14": 0,
                "pop_15_64": 1,
                "pop_65_plus": 3,
                "count_basis": "UNKNOWN",
                "reference_date": "not_applicable",
            },
        ),
    )
    return VectorLayer(
        name=name,
        features=features,
        crs=crs,
        provenance=_synthetic_vector_provenance(
            name=name,
            generator="korean_valley_villages",
            parameters={
                "settlements": 3,
                "aggregation_level": "synthetic_village",
                "deliberate_findings": {
                    "SYN-V-003": "strata (4) do not sum to total (3); total below "
                    "k-anonymity floor; count_basis UNKNOWN"
                },
            },
            crs=crs,
            notes=(
                "aggregate, invented population. SYN-V-003 is deliberately "
                "inconsistent so the validation checks have a positive case"
            ),
        ),
        feature_kind="settlement",
    )


def korean_valley_facilities(
    *, name: str = "facilities", crs: Any = SYNTHETIC_CRS, origin: tuple[float, float] = SYNTHETIC_ORIGIN
) -> VectorLayer:
    """Synthetic facilities: a fire station, a responder base, halls, a field."""
    x0, y0 = origin
    features = (
        Feature(
            Point(x0 + 700, y0 + 2620),
            {
                "facility_id": "SYN-FS-001",
                "name": "Synthetic fire station (valley mouth)",
                "kind": "fire_station",
                "operational_status": "UNKNOWN",
                "capacity_persons": None,
            },
        ),
        Feature(
            Point(x0 + 3300, y0 + 3050),
            {
                "facility_id": "SYN-RB-001",
                "name": "Synthetic responder base",
                "kind": "responder_base",
                "operational_status": "UNKNOWN",
                "capacity_persons": None,
            },
        ),
        Feature(
            Point(x0 + 1610, y0 + 4590),
            {
                "facility_id": "SYN-SH-001",
                "name": "Synthetic village hall A",
                "kind": "village_hall",
                "operational_status": "UNKNOWN",
                "capacity_persons": None,
            },
        ),
        Feature(
            Point(x0 + 4010, y0 + 1860),
            {
                "facility_id": "SYN-SH-002",
                "name": "Synthetic village hall B",
                "kind": "village_hall",
                "operational_status": "UNKNOWN",
                "capacity_persons": 60,
            },
        ),
        Feature(
            Polygon(
                [
                    (x0 + 3900, y0 + 2300),
                    (x0 + 4200, y0 + 2300),
                    (x0 + 4200, y0 + 2500),
                    (x0 + 3900, y0 + 2500),
                ]
            ),
            {
                "facility_id": "SYN-TR-001",
                "name": "Synthetic open field",
                "kind": "open_field",
                "operational_status": "UNKNOWN",
                "capacity_persons": None,
            },
        ),
    )
    return VectorLayer(
        name=name,
        features=features,
        crs=crs,
        provenance=_synthetic_vector_provenance(
            name=name,
            generator="korean_valley_facilities",
            parameters={
                "facilities": len(features),
                "kinds": ["fire_station", "responder_base", "village_hall", "open_field"],
            },
            crs=crs,
            notes=(
                "roles are source tags to be mapped explicitly by the caller. "
                "'open_field' is offered as a temporary-refuge *candidate* only; "
                "no suitability assessment exists (docs/ASSUMPTIONS.md A-FAC-1/2)"
            ),
        ),
        feature_kind="facility",
    )


def korean_valley_fuels(
    *,
    name: str = "fuels",
    template: RasterLayer | None = None,
    scheme: FuelClassScheme = SYNTHETIC_DEMO_SCHEME,
    seed: int = 20260919,
) -> RasterLayer:
    """A categorical fuel layer on the same grid as :func:`korean_valley_dem`.

    Classes are assigned from elevation bands plus seeded noise, and a block of
    cells is left as the scheme's nodata code so that "missing fuel data" is
    represented honestly rather than as a real class
    (``docs/FAILURE_MODES.md`` F-MD-1).
    """
    dem = template if template is not None else korean_valley_dem()
    elevation = np.asarray(dem.data, dtype=np.float64)
    rng = np.random.default_rng(seed)

    codes = np.full(elevation.shape, 2, dtype=np.int32)  # grass_or_crop
    codes[elevation > 300] = 4  # broadleaf_forest
    codes[elevation > 380] = 6  # mixed_forest
    codes[elevation > 450] = 5  # conifer_forest
    codes[elevation < 250] = 1  # non_vegetated (valley floor / built-up)
    shrub = rng.random(elevation.shape) < 0.08
    codes[shrub & (codes >= 4)] = 3  # shrub patches within forest

    # An explicit unmapped block: the Korean vegetation product this layer
    # stands in for was never obtained (docs/DATA_PROVENANCE.md), and a real
    # ingestion would routinely have holes.
    hole = (slice(10, 25), slice(10, 25))
    codes[hole] = scheme.nodata_code
    codes[~dem.valid_mask()] = scheme.nodata_code

    return fuel_layer_from_array(
        codes,
        name=name,
        transform=dem.transform,
        crs=dem.crs,
        scheme=scheme,
        source=_synthetic_source("korean_valley_fuels", seed=seed),
        data_class=DataClass.SYNTHETIC,
        temporal_class=TemporalProvenance.STATIC,
        temporal_reference="not_applicable",
        # A synthetic construct describes no moment in the world, so there is
        # no validity interval to be unknown about (D-0025).
        valid_from=NOT_APPLICABLE,
        valid_to=NOT_APPLICABLE,
        notes=(
            "classes derived from synthetic elevation bands plus seeded noise; "
            f"seed={seed}. a deliberate {(hole[0].stop - hole[0].start)}x"
            f"{(hole[1].stop - hole[1].start)} cell block is nodata"
        ),
    )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
#: Fixture name -> zero-argument-callable factory. Used by the CLI
#: (``wg-data make-fixtures``) and by ``source.kind: synthetic_fixture``.
# --------------------------------------------------------------------------- #
# The downstream CI integration fixture (Phase 2 item 29)
# --------------------------------------------------------------------------- #
#: Where the integration fixture lives. Offset from ``SYNTHETIC_ORIGIN`` so a
#: bundle built from it cannot be confused with the larger valley fixture by
#: coordinate alone.
INTEGRATION_ORIGIN = (240_000.0, 490_000.0)

#: The **study area** is 12x12 cells at 30 m: a 360 m square. Small enough that
#: a downstream CI job can read the whole thing and assert on individual cells,
#: and small enough that the committed bundle is kilobytes, not megabytes.
INTEGRATION_STUDY_SHAPE = (12, 12)
INTEGRATION_CELL_M = 30.0

#: The **DEM** is 16x16, two cells larger on every side, exactly as a real fetch
#: is buffered before clipping. Without that margin, Horn's 3x3 estimator drops
#: the outer ring of the study area itself -- on a 12x12 grid that is 44 of 144
#: cells, so the bundle would carry a permanent 30.6% missing-data WARNING that
#: is an artifact of the fixture's size and nothing else. A downstream CI
#: fixture should not ship a finding its consumers must learn to ignore.
INTEGRATION_SHAPE = (16, 16)
INTEGRATION_MARGIN_CELLS = 2

#: The gradient of the integration DEM. Chosen so the closed-form answers are
#: unambiguous rather than round: a downstream job that computes slope itself
#: and gets 5.7106 degrees has almost certainly done it right, whereas an
#: expected 0 or 45 could be produced by several different mistakes.
INTEGRATION_DZ_DX = 0.10
INTEGRATION_DZ_DY = 0.00


def integration_fixture_dem(
    *,
    name: str = "dem",
    crs: Any = SYNTHETIC_CRS,
    origin: tuple[float, float] = INTEGRATION_ORIGIN,
) -> RasterLayer:
    """A 12x12 tilted plane rising due east at 10%.

    Deliberately *analytic*, not arbitrary. Horn's estimator is exact on a
    plane, so a downstream consumer that computes slope and aspect from this
    DEM has a closed-form expectation to check against:

    * slope  = atan(0.10)          = 5.710593 degrees;
    * aspect = 270 degrees (downslope faces due west);
    * at every cell of the 12x12 **study area**, because the DEM is 16x16 --
      two cells larger on each side, so the estimator's unavoidable edge loss
      falls outside the study area instead of inside it. Cells on the DEM's own
      outer ring are nodata, since a 3x3 estimator has no 3x3 neighbourhood
      there and this repository does not extrapolate (D-0005).

    A fixture whose right answer is known is worth far more to a downstream CI
    job than a realistic-looking one whose right answer is whatever this
    repository last produced.
    """
    margin = INTEGRATION_MARGIN_CELLS * INTEGRATION_CELL_M
    return tilted_plane(
        name=name,
        shape=INTEGRATION_SHAPE,
        cell_size_m=INTEGRATION_CELL_M,
        dz_dx=INTEGRATION_DZ_DX,
        dz_dy=INTEGRATION_DZ_DY,
        base_elevation_m=100.0,
        crs=crs,
        # Shifted out by the margin so the study area sits inside the DEM with
        # room for the estimator's edge loss, rather than at its very edge.
        origin=(origin[0] - margin, origin[1] - margin),
    )


def integration_fixture_fuels(
    *,
    name: str = "fuels",
    template: RasterLayer | None = None,
    crs: Any = SYNTHETIC_CRS,
    origin: tuple[float, float] = INTEGRATION_ORIGIN,
) -> RasterLayer:
    """A 12x12 categorical raster on the DEM's grid: two classes and a hole.

    Three properties a downstream consumer can assert:

    * it is co-registered with :func:`integration_fixture_dem` cell-for-cell;
    * the western half is one class and the eastern half another, so a
      consumer can tell immediately whether it has transposed x and y --
      which matters here, because EPSG:5187's authority axis order is
      (northing, easting) and this package stores (easting, northing);
    * the 2x2 block at rows 5-6, columns 5-6 is **nodata**, so the
      missing-data path is exercised by default rather than only by a special
      test. A consumer that reads nodata as a class, or as zero, will get a
      wrong answer here instead of silently later.
    """
    if template is not None:
        base = template
    else:
        # Built on the STUDY-AREA grid, not the buffered DEM grid: the pipeline
        # passes the clipped DEM as the template, and this branch exists only
        # for direct use, where matching the study area is what a caller means.
        base = tilted_plane(
            name="integration_fixture_fuels_grid",
            shape=INTEGRATION_STUDY_SHAPE,
            cell_size_m=INTEGRATION_CELL_M,
            crs=crs,
            origin=origin,
        )
    height, width = base.shape
    scheme = SYNTHETIC_DEMO_SCHEME
    codes = [code for code in sorted(scheme.codes) if code != scheme.nodata_code]
    west_class, east_class = codes[0], codes[1]

    data = np.full((height, width), west_class, dtype=np.int16)
    data[:, width // 2 :] = east_class
    hole = (slice(5, 7), slice(5, 7))
    data[hole] = scheme.nodata_code

    return fuel_layer_from_array(
        data,
        name=name,
        transform=base.transform,
        crs=base.crs,
        scheme=scheme,
        source=_synthetic_source("integration_fixture_fuels"),
        data_class=DataClass.SYNTHETIC,
        temporal_class=TemporalProvenance.STATIC,
        temporal_reference="not_applicable",
        valid_from=NOT_APPLICABLE,
        valid_to=NOT_APPLICABLE,
        notes=(
            f"west half class {west_class}, east half class {east_class}, and a "
            "deliberate 2x2 nodata block at rows 5-6 / cols 5-6. the east-west "
            "split is there so a consumer that transposes x and y sees it "
            "immediately"
        ),
    )


def integration_fixture_roads(
    *,
    name: str = "roads",
    crs: Any = SYNTHETIC_CRS,
    origin: tuple[float, float] = INTEGRATION_ORIGIN,
) -> VectorLayer:
    """Three segments forming a T with two exits. Topology is hand-derived.

    Laid out in local metres from ``origin``, inside the 360 m study area::

        (0,180) A ---- B (180,180) ---- C (360,180)
                       |
                       D (180, 60)   <- the settlement

    Hand-derived, and all of it confirmed against the pipeline: **1 connected
    component**, 4 nodes, 3 edges, 480 m total. A and C reach the study-area
    boundary, so there are **2 exits**. D carries the settlement.

    The fixture's real value is that it makes one specific trap concrete:

    * ``single_egress_candidates`` is **empty**, because D's component has two
      exit nodes;
    * ``critical_links`` contains **one** edge, B-D, whose removal leaves D
      with no path to any exit.

    So a consumer reading only ``single_egress_candidates`` concludes this
    settlement is comfortably served, and is wrong. That is exactly what
    ``docs/DECISIONS.md`` D-0008 and ``docs/FAILURE_MODES.md`` F-RD-6 warn
    about, and a downstream CI job that asserts both numbers is checking it has
    read both.

    (The one critical link is also a correction: this docstring first claimed
    zero, reasoning that D "has two ways out". It does not -- it reaches both
    exits only through B, so B-D is a single point of failure. The metric was
    right and the hand-derivation was wrong.)
    """
    x0, y0 = origin
    segments = (
        ("A-B", ((0.0, 180.0), (180.0, 180.0))),
        ("B-C", ((180.0, 180.0), (360.0, 180.0))),
        ("B-D", ((180.0, 180.0), (180.0, 60.0))),
    )
    features = tuple(
        Feature(
            LineString([(x0 + ex, y0 + ny) for ex, ny in coords]),
            {
                "road_id": f"SYN-INT-{segment_id}",
                "name": f"Synthetic integration segment {segment_id}",
                # highway is the one attribute the source genuinely has. The
                # rest are absent rather than defaulted (D-0022), which is
                # itself something a consumer should be exercised against.
                "highway": "unclassified",
            },
        )
        for segment_id, coords in segments
    )
    provenance = _synthetic_vector_provenance(
        name=name,
        generator="integration_fixture_roads",
        parameters={
            "segments": [segment_id for segment_id, _ in segments],
            "expected_component_count": 1,
            "expected_node_count": 4,
            "expected_edge_count": 3,
            "expected_exit_count": 2,
            "expected_single_egress_candidate_count": 0,
            "expected_critical_link_count": 1,
            "expected_critical_link": "B-D",
            "crs": crs_to_string(crs),
        },
        crs=crs,
        notes=(
            "a T with two boundary exits. 1 component, 4 nodes, 3 edges, 480 m, "
            "2 exits, 0 single-egress candidates but 1 critical link (B-D). "
            "the gap between those last two is the point: a consumer reading "
            "only single_egress_candidates would call this settlement well "
            "served (D-0008, F-RD-6)"
        ),
    )
    return VectorLayer(name=name, features=features, crs=crs, provenance=provenance)


def integration_fixture_villages(
    *,
    name: str = "villages",
    crs: Any = SYNTHETIC_CRS,
    origin: tuple[float, float] = INTEGRATION_ORIGIN,
) -> VectorLayer:
    """One settlement with an aggregate, invented count.

    The count is 40, above the k-anonymity floor of 5, and the age strata sum
    to it exactly. That is deliberate: the larger valley fixture exists to
    exercise POP-001 and POP-003, and a *downstream CI* fixture should not
    emit findings a consumer then has to learn to ignore. A consumer wanting
    the unhappy paths uses ``korean_valley_villages``.

    There is no per-person, per-household or medical attribute here, and there
    never will be: this package's loaders raise on attribute names that look
    like one (F-POP-1).
    """
    x0, y0 = origin
    square = Polygon(
        [
            (x0 + 150.0, y0 + 30.0),
            (x0 + 210.0, y0 + 30.0),
            (x0 + 210.0, y0 + 90.0),
            (x0 + 150.0, y0 + 90.0),
        ]
    )
    features = (
        Feature(
            square,
            {
                "settlement_id": "SYN-INT-V-001",
                "name": "Synthetic integration hamlet",
                "population_total": 40,
                "pop_0_14": 4,
                "pop_15_64": 24,
                "pop_65_plus": 12,
                # Recorded, not omitted: a count with no stated basis is
                # reported by POP-004, and "invented" is the honest basis for
                # a synthetic figure.
                "count_basis": "synthetic_invented",
                "reference_date": "not_applicable",
            },
        ),
    )
    provenance = _synthetic_vector_provenance(
        name=name,
        generator="integration_fixture_villages",
        parameters={
            "settlement_count": 1,
            "population_total": 40,
            "strata_sum": 40,
            "crs": crs_to_string(crs),
        },
        crs=crs,
        notes=(
            "one settlement, aggregate only. the count is invented and "
            "describes no real place. age strata sum to the stated total and "
            "the total is above the k-anonymity floor, so this fixture emits "
            "no population findings for a consumer to learn to ignore"
        ),
    )
    return VectorLayer(name=name, features=features, crs=crs, provenance=provenance)


def integration_fixture_facilities(
    *,
    name: str = "facilities",
    crs: Any = SYNTHETIC_CRS,
    origin: tuple[float, float] = INTEGRATION_ORIGIN,
) -> VectorLayer:
    """One responder base and one candidate destination. Exactly item 29's ask.

    Both carry ``operational_status: UNKNOWN`` and ``capacity_persons: None``,
    because nobody established either. A downstream consumer is therefore
    exercised against the real case -- a facility that exists and whose
    capabilities are unknown -- rather than against a convenient invention.

    ``kind: shelter`` records what the source calls it. It is **not** a finding
    that the place is a viable wildfire refuge; this repository makes no such
    assessment and no field here implies one (``AGENTS.md`` §5, A-FAC-1).
    """
    x0, y0 = origin
    features = (
        Feature(
            Point(x0 + 20.0, y0 + 180.0),
            {
                "facility_id": "SYN-INT-RB-001",
                "name": "Synthetic integration responder base",
                "kind": "responder_base",
                "operational_status": "UNKNOWN",
                "capacity_persons": None,
            },
        ),
        Feature(
            Point(x0 + 340.0, y0 + 180.0),
            {
                "facility_id": "SYN-INT-SH-001",
                "name": "Synthetic integration candidate destination",
                "kind": "shelter",
                "operational_status": "UNKNOWN",
                "capacity_persons": None,
            },
        ),
    )
    provenance = _synthetic_vector_provenance(
        name=name,
        generator="integration_fixture_facilities",
        parameters={
            "responder_bases": 1,
            "candidate_destinations": 1,
            "crs": crs_to_string(crs),
        },
        crs=crs,
        notes=(
            "one responder base at the west exit, one candidate destination at "
            "the east exit. 'kind' records what a source calls a place and is "
            "not an assessment that it is a viable refuge; operational status "
            "and capacity are UNKNOWN because nobody established them"
        ),
    )
    return VectorLayer(name=name, features=features, crs=crs, provenance=provenance)



FIXTURES: dict[str, Callable[..., Any]] = {
    "tilted_plane": tilted_plane,
    "flat_terrain": flat_terrain,
    "missing_cells_raster": missing_cells_raster,
    "single_exit_network": single_exit_network,
    "two_exit_network": two_exit_network,
    "disconnected_network": disconnected_network,
    "incompatible_crs_pair": incompatible_crs_pair,
    "village_shelter_station": village_shelter_station,
    "korean_valley_dem": korean_valley_dem,
    "korean_valley_roads": korean_valley_roads,
    "korean_valley_villages": korean_valley_villages,
    "korean_valley_facilities": korean_valley_facilities,
    "korean_valley_fuels": korean_valley_fuels,
    "integration_fixture_dem": integration_fixture_dem,
    "integration_fixture_fuels": integration_fixture_fuels,
    "integration_fixture_roads": integration_fixture_roads,
    "integration_fixture_villages": integration_fixture_villages,
    "integration_fixture_facilities": integration_fixture_facilities,
}


def fixture_names() -> list[str]:
    """Sorted fixture names."""
    return sorted(FIXTURES)


def make_fixture(name: str, **kwargs: Any) -> Any:
    """Build a fixture by name, raising on an unknown name."""
    if name not in FIXTURES:
        raise KeyError(
            f"unknown fixture {name!r}; known fixtures: {fixture_names()}"
        )
    return FIXTURES[name](**kwargs)
