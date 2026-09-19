"""Real-source fetchers: tile naming, guards, and (opt-in) live fetches.

Live fetches are marked ``network`` and excluded from the default run
(``AGENTS.md`` §7). Run them with ``pytest -m network``.
"""

from __future__ import annotations

import numpy as np
import pytest

from wildfireguardian_data.bounds import Bounds
from wildfireguardian_data.errors import IngestError, NetworkAccessError
from wildfireguardian_data.provenance import DataClass, TemporalProvenance
from wildfireguardian_data.sources import (
    COPERNICUS_GLO30_BUCKET,
    DEFAULT_OSM_HIGHWAY_VALUES,
    copernicus_glo30_tile_name,
    copernicus_glo30_tile_url,
    fetch_copernicus_dem,
    fetch_osm_roads,
)

# A small extent over the rural hills inland of Uljin, in EPSG:4326.
ULJIN_WGS84 = Bounds(129.31, 36.91, 129.33, 36.93, crs="EPSG:4326")


# --------------------------------------------------------------------------- #
# No network needed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        (36, 129, "Copernicus_DSM_COG_10_N36_00_E129_00_DEM"),
        (0, 0, "Copernicus_DSM_COG_10_N00_00_E000_00_DEM"),
        (-34, 151, "Copernicus_DSM_COG_10_S34_00_E151_00_DEM"),
        (40, -74, "Copernicus_DSM_COG_10_N40_00_W074_00_DEM"),
    ],
)
def test_tile_naming_covers_all_four_hemispheres(lat, lon, expected):
    assert copernicus_glo30_tile_name(lat, lon) == expected
    url = copernicus_glo30_tile_url(lat, lon)
    assert url.startswith(COPERNICUS_GLO30_BUCKET)
    assert url.endswith(f"{expected}/{expected}.tif")


def test_network_is_opt_in_for_both_fetchers():
    with pytest.raises(NetworkAccessError) as excinfo:
        fetch_copernicus_dem(ULJIN_WGS84)
    assert "allow_network" in str(excinfo.value)
    with pytest.raises(NetworkAccessError):
        fetch_osm_roads(ULJIN_WGS84)


def test_fetchers_refuse_bounds_that_are_not_wgs84():
    # They do not reproject their input: a caller holding projected bounds
    # converts them explicitly and can see the conversion in its own code.
    projected = Bounds(227_500.0, 478_500.0, 231_500.0, 482_500.0, crs="EPSG:5187")
    with pytest.raises(IngestError) as excinfo:
        fetch_copernicus_dem(projected, allow_network=True)
    assert "EPSG:4326" in str(excinfo.value)
    with pytest.raises(IngestError):
        fetch_osm_roads(projected, allow_network=True)


def test_multi_tile_request_is_refused_rather_than_silently_truncated():
    # F-BND-5: returning one tile's worth of a two-tile request would hand back
    # a study area with an artificial straight edge.
    straddling = Bounds(128.95, 36.5, 129.15, 36.7, crs="EPSG:4326")
    with pytest.raises(IngestError) as excinfo:
        fetch_copernicus_dem(straddling, allow_network=True)
    assert "more than one Copernicus GLO-30 tile" in str(excinfo.value)


def test_default_osm_filter_keeps_rural_access_classes():
    # Rural Korean access depends on tracks and unclassified roads; a
    # "major roads only" filter would drop exactly the segments a single-egress
    # hamlet relies on.
    assert "track" in DEFAULT_OSM_HIGHWAY_VALUES
    assert "unclassified" in DEFAULT_OSM_HIGHWAY_VALUES
    assert "residential" in DEFAULT_OSM_HIGHWAY_VALUES
    # Non-vehicle ways are excluded. That is a filtering decision, not a
    # usability claim.
    assert "footway" not in DEFAULT_OSM_HIGHWAY_VALUES
    assert "path" not in DEFAULT_OSM_HIGHWAY_VALUES
    assert "steps" not in DEFAULT_OSM_HIGHWAY_VALUES


# --------------------------------------------------------------------------- #
# Live fetches (opt-in)
# --------------------------------------------------------------------------- #
@pytest.mark.network
def test_copernicus_fetch_returns_a_usable_geographic_layer():
    layer = fetch_copernicus_dem(ULJIN_WGS84, allow_network=True)
    from wildfireguardian_data.crs import crs_to_string

    # Returned in the tile's own CRS; reprojection is the caller's explicit step.
    assert crs_to_string(layer.crs) == "EPSG:4326"
    assert layer.provenance.data_class is DataClass.OBSERVED
    assert layer.provenance.temporal_class is TemporalProvenance.STATIC
    assert layer.provenance.temporal_reference == "2011"
    assert "EGM2008" in layer.provenance.vertical_datum
    source = layer.provenance.sources[0]
    assert "Copernicus" in source.name
    assert "attribution" in source.licence
    # DSM status must be recorded, since slope over forest is canopy slope.
    assert "DIGITAL SURFACE MODEL" in source.notes
    # Plausible Korean coastal-hill elevations, and no missing cells inland.
    values = layer.data[layer.valid_mask()]
    assert 0.0 <= float(values.min()) < 500.0
    assert float(values.max()) < 2000.0


@pytest.mark.network
def test_osm_fetch_is_observation_time_and_odbl():
    layer = fetch_osm_roads(ULJIN_WGS84, allow_network=True)
    assert len(layer) > 0
    assert layer.geom_types == {"LineString"}
    # Continuously edited, so it is valid as of the download moment - not static.
    assert layer.provenance.temporal_class is TemporalProvenance.OBSERVATION_TIME
    source = layer.provenance.sources[0]
    assert "ODbL" in source.licence
    assert "share-alike" in source.notes
    # Tags are carried opaquely, with the OSM identity preserved.
    assert "osm_id" in layer.property_keys
    assert "highway" in layer.property_keys
    for feature in layer:
        assert feature.properties["highway"] in DEFAULT_OSM_HIGHWAY_VALUES


@pytest.mark.network
def test_real_pipeline_produces_slope_in_a_plausible_range():
    # End to end on real data: fetch -> reproject -> clip -> slope.
    from wildfireguardian_data.terrain import clip_raster, reproject_raster, slope

    fetched = fetch_copernicus_dem(ULJIN_WGS84, allow_network=True)
    projected = reproject_raster(fetched, "EPSG:5187", dst_resolution=30.0)
    clipped = clip_raster(projected, projected.bounds.buffered(-60.0), buffer_cells=1)
    slope_layer = slope(clipped)

    values = slope_layer.data[slope_layer.valid_mask()]
    assert values.size > 0
    # Korean coastal hills: steep, but nothing is a vertical cliff at 30 m.
    assert 0.0 <= float(values.min())
    assert 10.0 < float(np.median(values)) < 45.0
    assert float(values.max()) < 80.0
    # The whole chain is recorded on the final artifact.
    assert [t.operation for t in slope_layer.provenance.transformations] == [
        "fetch_copernicus_dem_window",
        "reproject_raster",
        "clip_raster",
        "slope",
    ]
