"""Independently written test helpers.

These constructors are written from scratch rather than imported from
:mod:`wildfireguardian_data.fixtures`: a test of the slope estimator must not
build its input with the same module it is testing, or a shared mistake would
cancel out and the test would pass.

Nothing here reaches the network, and nothing here is random without a seed.
Tests that need network access are marked ``@pytest.mark.network`` and are
excluded from the default run (``AGENTS.md`` §7).
"""

from __future__ import annotations

import numpy as np

from wildfireguardian_data import (
    DataClass,
    GridTransform,
    ProvenanceRecord,
    RasterKind,
    RasterLayer,
    SourceRecord,
    TemporalProvenance,
)

#: A projected, metre-based Korean CRS used throughout the tests.
TEST_CRS = "EPSG:5187"

#: Realistic-looking coordinates for the Uljin area in ``TEST_CRS``.
TEST_ORIGIN = (226_000.0, 477_000.0)


def make_provenance(
    name: str,
    *,
    data_class: DataClass = DataClass.SYNTHETIC,
    temporal_class: TemporalProvenance = TemporalProvenance.STATIC,
    value_unit: str = "m",
    **changes,
) -> ProvenanceRecord:
    """Minimal valid provenance for a test layer."""
    return ProvenanceRecord(
        layer_name=name,
        data_class=data_class,
        temporal_class=temporal_class,
        temporal_reference="not_applicable",
        sources=(SourceRecord(name=f"test:{name}", source_date="not_applicable"),),
        value_unit=value_unit,
        nodata_representation="nan",
        **changes,
    )


def make_raster(
    data: np.ndarray,
    *,
    name: str = "test_raster",
    cell_size_m: float = 30.0,
    crs: str | None = TEST_CRS,
    nodata=np.nan,
    kind: RasterKind = RasterKind.CONTINUOUS,
    value_unit: str = "m",
    origin: tuple[float, float] = TEST_ORIGIN,
) -> RasterLayer:
    """Wrap an array as a :class:`RasterLayer` with valid provenance."""
    array = np.asarray(data)
    transform = GridTransform(
        x_origin=origin[0],
        y_origin=origin[1] + array.shape[0] * cell_size_m,
        x_size=cell_size_m,
        y_size=cell_size_m,
    )
    return RasterLayer(
        name=name,
        data=array,
        transform=transform,
        crs=crs,
        nodata=nodata,
        kind=kind,
        value_unit=value_unit,
        provenance=make_provenance(name, value_unit=value_unit),
    )


def planar_surface(
    *,
    shape: tuple[int, int] = (21, 21),
    cell_size_m: float = 30.0,
    dz_dx: float = 0.2,
    dz_dy: float = -0.1,
    base: float = 400.0,
) -> np.ndarray:
    """A planar elevation array, built independently of the package.

    Deliberately re-derived here rather than imported from
    :mod:`wildfireguardian_data.fixtures`: a test of the slope estimator should
    not depend on the same module it is testing to construct its input.
    """
    height, width = shape
    rows, cols = np.mgrid[0:height, 0:width]
    east = cols * cell_size_m
    north = (height - 1 - rows) * cell_size_m
    return base + dz_dx * east + dz_dy * north


