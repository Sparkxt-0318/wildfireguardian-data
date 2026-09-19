"""Fetchers for real external sources.

Two sources are implemented, both chosen because they are openly licensed,
globally available, and actually reachable from this repository's development
environment (``docs/DATA_PROVENANCE.md`` §Access attempts):

* **Copernicus DEM GLO-30** -- 30 m global DSM, read as a windowed COG over
  HTTPS so a study area costs a few megabytes rather than a whole tile;
* **OpenStreetMap** via the main API's ``/api/0.6/map`` endpoint, which serves
  small bounding boxes.

Rules that apply to everything here:

* **Network access is opt-in.** Every fetcher takes ``allow_network`` and raises
  :class:`~wildfireguardian_data.errors.NetworkAccessError` when it is false, so
  a pipeline never reaches the network implicitly (D-0012).
* **Real provenance, including licence.** Each fetcher fills a
  :class:`SourceRecord` with the dataset's real name, URL, publisher and
  licence, and records the acquisition timestamp.
* **Raw data is not committed.** Fetched files land in a cache directory under
  ``data/raw/``, which is git-ignored (D-0012).
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from shapely.geometry import LineString

from .bounds import Bounds
from .crs import crs_to_string, parse_crs, require_crs
from .errors import IngestError, NetworkAccessError, OptionalDependencyError
from .provenance.models import (
    UNKNOWN,
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
    Transformation,
    utc_now_iso,
)
from .raster import GridTransform, RasterKind, RasterLayer
from .vector import Feature, VectorLayer

__all__ = [
    "COPERNICUS_GLO30_BUCKET",
    "OSM_API_MAP_URL",
    "DEFAULT_OSM_HIGHWAY_VALUES",
    "copernicus_glo30_tile_name",
    "copernicus_glo30_tile_url",
    "fetch_copernicus_dem",
    "fetch_osm_roads",
]

#: Public S3 mirror of Copernicus DEM GLO-30 (30 m), as COG tiles of 1x1 degree.
COPERNICUS_GLO30_BUCKET = "https://copernicus-dem-30m.s3.amazonaws.com"

#: OpenStreetMap main API map endpoint. Limited to small bounding boxes (0.25
#: square degrees, 50 000 nodes). Overpass is the usual tool for larger
#: extracts; it was not reachable from this environment
#: (``docs/DATA_PROVENANCE.md``).
OSM_API_MAP_URL = "https://api.openstreetmap.org/api/0.6/map"

#: The ``highway`` values kept by default. Rural Korean access is dominated by
#: ``track`` and ``unclassified``, so excluding them -- as a "major roads only"
#: filter would -- removes exactly the segments a single-egress hamlet depends
#: on. ``footway``/``path``/``steps`` are excluded because they are not vehicle
#: access; that is a filtering decision, not a claim about usability.
DEFAULT_OSM_HIGHWAY_VALUES: tuple[str, ...] = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "track",
    "road",
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
)


def _require_network(allow_network: bool, what: str) -> None:
    if not allow_network:
        raise NetworkAccessError(
            f"fetching {what} requires network access, which is opt-in. pass "
            "allow_network=True (CLI: --allow-network). builds from synthetic "
            "fixtures need no network and are what CI runs "
            "(docs/DECISIONS.md D-0012)."
        )


def _gdal_http_options() -> dict[str, str]:
    """GDAL/curl options derived from the environment, for remote COG reads.

    Only passes through what the environment already sets (proxy, CA bundle).
    Nothing here disables TLS verification.
    """
    options: dict[str, str] = {
        # Do not list the bucket directory: for a public COG this saves a
        # request per open and avoids a 403 on buckets that forbid listing.
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    }
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        options["GDAL_HTTP_PROXY"] = proxy
    ca_bundle = os.environ.get("CURL_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca_bundle:
        options["GDAL_CURL_CA_BUNDLE"] = ca_bundle
        options["CURL_CA_BUNDLE"] = ca_bundle
    return options


# --------------------------------------------------------------------------- #
# Copernicus DEM GLO-30
# --------------------------------------------------------------------------- #
def copernicus_glo30_tile_name(lat_south: int, lon_west: int) -> str:
    """Tile name for the 1x1 degree tile whose south-west corner is given."""
    ns = "N" if lat_south >= 0 else "S"
    ew = "E" if lon_west >= 0 else "W"
    return (
        f"Copernicus_DSM_COG_10_{ns}{abs(lat_south):02d}_00_"
        f"{ew}{abs(lon_west):03d}_00_DEM"
    )


def copernicus_glo30_tile_url(lat_south: int, lon_west: int) -> str:
    """Public URL of a Copernicus DEM GLO-30 COG tile."""
    name = copernicus_glo30_tile_name(lat_south, lon_west)
    return f"{COPERNICUS_GLO30_BUCKET}/{name}/{name}.tif"


def _copernicus_source(url: str) -> SourceRecord:
    return SourceRecord(
        name="Copernicus DEM GLO-30 (COP-DEM_GLO-30-DGED)",
        url_or_identifier=url,
        # The GLO-30 product is compiled from TanDEM-X acquisitions of
        # 2011-2015. That is the period the elevations describe, and it is a
        # range rather than a date, so year precision is recorded and the range
        # is stated in the notes rather than collapsed to a single day.
        source_date="2011",
        acquisition_date=utc_now_iso(),
        publisher="European Space Agency / Copernicus Programme",
        licence=(
            "free use with attribution; see the Copernicus DEM product licence"
        ),
        licence_url="https://spacedata.copernicus.eu/documents/20123/121286/CSCDA_ESA_Mission-specific-Annex.pdf",
        notes=(
            "GLO-30 is a DIGITAL SURFACE MODEL: it includes vegetation canopy "
            "and buildings, so slope derived from it over Korean forest is "
            "canopy slope, not ground slope (docs/FAILURE_MODES.md F-TER-3). "
            "elevations are referenced to the EGM2008 geoid. source "
            "acquisitions span 2011-2015; source_date records the start year at "
            "year precision."
        ),
    )


def fetch_copernicus_dem(
    bounds_wgs84: Bounds,
    *,
    allow_network: bool = False,
    name: str = "dem_copernicus_glo30",
    buffer_deg: float = 0.01,
) -> RasterLayer:
    """Read a Copernicus GLO-30 window covering ``bounds_wgs84``.

    Parameters
    ----------
    bounds_wgs84:
        Extent in EPSG:4326. Must be in EPSG:4326 -- this function does not
        reproject its input, so a caller holding projected bounds converts them
        explicitly and can see the conversion in its own code.
    buffer_deg:
        Extra margin read around the requested extent, so that a later
        reprojection and a 3x3 derivative have data to work with at the study
        boundary (D-0005). About 1 km at Korean latitudes by default.

    Returns a layer in **EPSG:4326** -- the tile's own CRS. Reprojection to a
    metre CRS is the caller's explicit next step (D-0002, D-0010).

    Limitation: a window spanning more than one 1-degree tile raises. Mosaicking
    is not implemented, and silently returning one tile's worth of a two-tile
    request would hand back a study area with a straight artificial edge.
    """
    _require_network(allow_network, "the Copernicus DEM GLO-30 tile")
    try:
        import rasterio
        from rasterio.windows import from_bounds as window_from_bounds
    except ImportError as exc:  # pragma: no cover
        raise OptionalDependencyError(
            "rasterio", purpose="reading remote Copernicus DEM COG tiles"
        ) from exc

    crs = require_crs(bounds_wgs84.crs, context="fetching Copernicus DEM")
    if not crs.equals(parse_crs("EPSG:4326")):
        raise IngestError(
            f"fetch_copernicus_dem needs bounds in EPSG:4326; got "
            f"{crs_to_string(crs)}. convert them explicitly -- this function "
            "does not reproject its input (docs/DECISIONS.md D-0002)."
        )

    requested = bounds_wgs84.buffered(buffer_deg)
    import math

    lat_tiles = {math.floor(requested.min_y), math.floor(requested.max_y)}
    lon_tiles = {math.floor(requested.min_x), math.floor(requested.max_x)}
    if len(lat_tiles) > 1 or len(lon_tiles) > 1:
        raise IngestError(
            f"requested extent {requested} spans more than one Copernicus GLO-30 "
            f"tile (lat tiles {sorted(lat_tiles)}, lon tiles {sorted(lon_tiles)}). "
            "mosaicking is not implemented; returning a single tile would give a "
            "study area with an artificial straight edge. use a smaller extent, "
            "or implement a mosaic step and record it in provenance."
        )

    lat_south = next(iter(lat_tiles))
    lon_west = next(iter(lon_tiles))
    url = copernicus_glo30_tile_url(lat_south, lon_west)

    with rasterio.Env(**_gdal_http_options()):
        try:
            with rasterio.open(url) as dataset:
                window = window_from_bounds(
                    requested.min_x,
                    requested.min_y,
                    requested.max_x,
                    requested.max_y,
                    dataset.transform,
                ).round_offsets().round_lengths()
                array = dataset.read(1, window=window)
                transform = GridTransform.from_affine(
                    dataset.window_transform(window)
                )
                tile_crs = parse_crs(dataset.crs.to_wkt())
                nodata = dataset.nodatavals[0]
        except rasterio.errors.RasterioIOError as exc:
            raise IngestError(
                f"could not read Copernicus DEM tile {url}: {exc}. the tile may "
                "not exist for this location (ocean), or the network may be "
                "unavailable. document the failure and fall back to fixtures "
                "rather than substituting another dataset silently "
                "(AGENTS.md §4)."
            ) from exc

    if array.size == 0:
        raise IngestError(
            f"Copernicus DEM read for {requested} returned an empty window from {url}"
        )

    provenance = ProvenanceRecord(
        layer_name=name,
        data_class=DataClass.OBSERVED,
        temporal_class=TemporalProvenance.STATIC,
        temporal_reference="2011",
        sources=(_copernicus_source(url),),
        transformations=(
            Transformation(
                operation="fetch_copernicus_dem_window",
                parameters={
                    "url": url,
                    "tile": copernicus_glo30_tile_name(lat_south, lon_west),
                    "requested_bounds_wgs84": list(bounds_wgs84.as_tuple()),
                    "buffer_deg": buffer_deg,
                    "read_bounds_wgs84": list(requested.as_tuple()),
                    "window_shape": list(array.shape),
                    "cell_size_deg": [transform.x_size, transform.y_size],
                    "tile_nodata": repr(nodata),
                    "dtype": str(array.dtype),
                },
                notes=(
                    "windowed COG read over HTTPS; no resampling applied at this "
                    "stage, so the values are the tile's own"
                ),
            ),
        ),
        original_crs=crs_to_string(tile_crs),
        output_crs=crs_to_string(tile_crs),
        spatial_resolution=(transform.x_size, transform.y_size),
        resolution_unit="degree",
        value_unit="m",
        vertical_datum="EGM2008 geoid (per Copernicus DEM product specification)",
        nodata_representation="none_declared" if nodata is None else repr(nodata),
        checksum=UNKNOWN,
        notes=(
            "DIGITAL SURFACE MODEL (includes canopy and buildings). geographic "
            "CRS: must be reprojected to a metre CRS before any slope or length "
            "computation (docs/DECISIONS.md D-0010)."
        ),
    )
    return RasterLayer(
        name=name,
        data=array,
        transform=transform,
        crs=tile_crs,
        nodata=nodata,
        kind=RasterKind.CONTINUOUS,
        value_unit="m",
        provenance=provenance,
    )


# --------------------------------------------------------------------------- #
# OpenStreetMap
# --------------------------------------------------------------------------- #
def _osm_source(url: str) -> SourceRecord:
    return SourceRecord(
        name="OpenStreetMap (api.openstreetmap.org /api/0.6/map)",
        url_or_identifier=url,
        # OSM is continuously edited: the data describes the world *as edited up
        # to the moment of download*, which is the download time itself. Hence
        # source_date == acquisition_date here, and the temporal class is
        # OBSERVATION_TIME rather than STATIC.
        source_date=utc_now_iso(),
        acquisition_date=utc_now_iso(),
        publisher="OpenStreetMap contributors",
        licence="Open Database License (ODbL) 1.0",
        licence_url="https://opendatacommons.org/licenses/odbl/1-0/",
        notes=(
            "ODbL is share-alike on derived databases: a study-area bundle "
            "containing this road layer carries ODbL obligations, and "
            "attribution to OpenStreetMap contributors is required. rural "
            "Korean coverage is uneven and completeness is UNKNOWN "
            "(docs/FAILURE_MODES.md F-RD-3)."
        ),
    )


def fetch_osm_roads(
    bounds_wgs84: Bounds,
    *,
    allow_network: bool = False,
    name: str = "roads_osm",
    highway_values: Sequence[str] = DEFAULT_OSM_HIGHWAY_VALUES,
    timeout_s: float = 120.0,
    cache_path: str | Path | None = None,
) -> VectorLayer:
    """Fetch highway ways from the OSM API for a small bounding box.

    Returns a layer in **EPSG:4326** with every OSM tag carried opaquely as a
    feature property, plus ``osm_id`` and ``osm_type``. No tag is interpreted:
    ``surface``, ``width`` and ``smoothness`` are data, not a usability
    judgement (A-RD-6).

    The API refuses boxes larger than 0.25 square degrees or containing more
    than 50 000 nodes; both come back as an HTTP error, which is raised rather
    than worked around by silently shrinking the request.
    """
    _require_network(allow_network, "OpenStreetMap road data")
    crs = require_crs(bounds_wgs84.crs, context="fetching OSM roads")
    if not crs.equals(parse_crs("EPSG:4326")):
        raise IngestError(
            f"fetch_osm_roads needs bounds in EPSG:4326; got {crs_to_string(crs)}"
        )

    bbox = ",".join(
        f"{value:.6f}"
        for value in (
            bounds_wgs84.min_x,
            bounds_wgs84.min_y,
            bounds_wgs84.max_x,
            bounds_wgs84.max_y,
        )
    )
    url = f"{OSM_API_MAP_URL}?bbox={bbox}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "wildfireguardian-data/0.1 (research data preparation; "
                "https://github.com/sparkxt-0318/wildfireguardian-data)"
            )
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise IngestError(
            f"OSM API request failed for {url}: {exc}. the API rejects boxes "
            "larger than 0.25 square degrees or with more than 50 000 nodes. "
            "document the failure and fall back to fixtures rather than "
            "substituting another source silently (AGENTS.md §4)."
        ) from exc

    if cache_path is not None:
        target = Path(cache_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise IngestError(f"OSM API returned unparseable XML for {url}: {exc}") from exc

    nodes: dict[str, tuple[float, float]] = {}
    for element in root.findall("node"):
        node_id = element.get("id")
        lat, lon = element.get("lat"), element.get("lon")
        if node_id is None or lat is None or lon is None:
            continue
        nodes[node_id] = (float(lon), float(lat))

    accepted = set(highway_values)
    features: list[Feature] = []
    skipped_missing_nodes = 0
    for way in root.findall("way"):
        tags = {
            tag.get("k"): tag.get("v")
            for tag in way.findall("tag")
            if tag.get("k") is not None
        }
        highway = tags.get("highway")
        if highway is None or highway not in accepted:
            continue
        refs = [nd.get("ref") for nd in way.findall("nd")]
        try:
            coords = [nodes[ref] for ref in refs if ref is not None]
        except KeyError:
            # A way whose nodes fall outside the bbox is truncated by the API.
            # Counted and reported rather than reconstructed.
            skipped_missing_nodes += 1
            continue
        if len(coords) < 2:
            continue
        properties: dict[str, Any] = {
            "osm_id": way.get("id"),
            "osm_type": "way",
            "osm_version": way.get("version"),
            "osm_timestamp": way.get("timestamp"),
        }
        properties.update(tags)
        features.append(Feature(LineString(coords), properties))

    if not features:
        raise IngestError(
            f"OSM API returned no ways with highway in {sorted(accepted)} for "
            f"{bounds_wgs84}. an empty road layer is reported, not returned."
        )

    provenance = ProvenanceRecord(
        layer_name=name,
        data_class=DataClass.OBSERVED,
        # Continuously edited, so the layer is valid as of the download moment,
        # not for a year or indefinitely.
        temporal_class=TemporalProvenance.OBSERVATION_TIME,
        temporal_reference=utc_now_iso(),
        sources=(_osm_source(url),),
        transformations=(
            Transformation(
                operation="fetch_osm_roads",
                parameters={
                    "url": url,
                    "bbox_wgs84": list(bounds_wgs84.as_tuple()),
                    "highway_values": sorted(accepted),
                    "nodes_returned": len(nodes),
                    "ways_kept": len(features),
                    "ways_skipped_incomplete": skipped_missing_nodes,
                },
                notes=(
                    "all OSM tags carried opaquely; none interpreted as a "
                    "usability claim (docs/ASSUMPTIONS.md A-RD-6). ways with "
                    "nodes outside the bbox were skipped, not reconstructed"
                ),
            ),
        ),
        original_crs="EPSG:4326",
        output_crs="EPSG:4326",
        value_unit="not_applicable",
        nodata_representation="not_applicable",
        checksum=UNKNOWN,
        notes=(
            "ODbL 1.0; attribution to OpenStreetMap contributors required, and "
            "share-alike applies to derived databases. rural completeness is "
            "UNKNOWN."
        ),
    )
    return VectorLayer(
        name=name,
        features=tuple(features),
        crs="EPSG:4326",
        provenance=provenance,
        feature_kind="road segment",
    )
