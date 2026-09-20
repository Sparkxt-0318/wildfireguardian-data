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
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from shapely.geometry import LineString

from .bounds import Bounds
from .crs import crs_to_string, parse_crs, require_crs
from .errors import IngestError, NetworkAccessError, OptionalDependencyError
from .provenance.models import (
    NOT_APPLICABLE,
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
    "WORLDCOVER_BUCKET",
    "WORLDCOVER_VERSION",
    "WORLDCOVER_YEAR",
    "WORLDCOVER_MANUAL_URL",
    "worldcover_tile_name",
    "worldcover_tile_url",
    "fetch_esa_worldcover",
    "COPERNICUS_COLLECTION_PAGE",
    "COPERNICUS_BUCKET_README",
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


#: Pages whose contents this repository actually fetched and read. Every factual
#: claim in :func:`_copernicus_source` is attributed to one of these or to the
#: tile's own metadata, so a reader can tell a checked fact from a recalled one
#: (``AGENTS.md`` §4).
COPERNICUS_COLLECTION_PAGE = (
    "https://dataspace.copernicus.eu/explore-data/data-collections/"
    "copernicus-contributing-missions/collections-description/COP-DEM"
)
COPERNICUS_BUCKET_README = "https://copernicus-dem-30m.s3.amazonaws.com/readme.html"


def _copernicus_source(url: str, *, tile_tags: dict[str, str] | None = None) -> SourceRecord:
    """Provenance for a Copernicus DEM GLO-30 tile read from the AWS mirror.

    Each claim below is attributed to where it was verified: the tile's own
    metadata, the COP-DEM collection description page, or the AWS bucket readme.
    Nothing here is asserted from recollection, and nothing claims a
    verification that did not happen.
    """
    grid_alignment = (
        (tile_tags or {}).get("AREA_OR_POINT", UNKNOWN) if tile_tags is not None else UNKNOWN
    )
    return SourceRecord(
        name="Copernicus DEM GLO-30 Public (source dataset COP-DEM_GLO-30-DGED)",
        url_or_identifier=url,
        # The product is compiled from TanDEM-X acquisitions of 2011-2015 (the
        # collection page states this verbatim). Year precision, because a range
        # is what the source gives; the range itself is in the notes rather than
        # collapsed into a single date.
        source_date="2011",
        acquisition_date=utc_now_iso(),
        publisher="European Space Agency / Copernicus Programme; AWS mirror by Sinergise",
        licence=(
            "free for the general public under the Copernicus DEM licence terms. "
            "the AWS registry states GLO-30 Public and GLO-90 'are available on a "
            "free basis for the general public under the terms and conditions of "
            "the Licence'. the licence document ITSELF WAS NOT RETRIEVED from "
            "this environment, so the detailed terms are UNVERIFIED HERE; check "
            "them before redistributing. attribution to ESA / Copernicus is "
            "required"
        ),
        licence_url=COPERNICUS_COLLECTION_PAGE,
        notes=(
            "VERIFIED FROM THE TILE METADATA: horizontal CRS EPSG:4326; float32; "
            "1/3600 degree cells; no vertical CRS and no nodata declared in the "
            f"file; grid alignment tag AREA_OR_POINT={grid_alignment!r}. "
            f"VERIFIED FROM {COPERNICUS_COLLECTION_PAGE}: it is a DIGITAL "
            "SURFACE MODEL including buildings, infrastructure and vegetation, "
            "so slope over Korean forest is canopy slope, not ground slope "
            "(docs/FAILURE_MODES.md F-TER-3); vertical reference EGM2008 "
            "(EPSG:3855) in metres - note this is the PAGE's statement, NOT a "
            "vertical CRS present in the tile; grid alignment RasterPixelIsPoint "
            "for the DGED format, consistent with the tile's AREA_OR_POINT tag; "
            "acquisitions by TanDEM-X between 2011 and 2015; derived from an "
            "EDITED DSM (WorldDEM) in which water bodies are flattened, river "
            "flow made consistent, and shorelines, airports and 'implausible "
            "terrain structures' edited - so the surface is not a raw "
            "measurement everywhere; and MIXED PROVENANCE: 'if any Copernicus "
            "DEM products show a sensing date earlier than 2011, this is because "
            "older elevation models were used to fill data gaps', which means "
            "individual cells may derive from older products and concentrate in "
            "radar-shadow terrain - that is, in steep Korean valleys. "
            f"VERIFIED FROM {COPERNICUS_BUCKET_README}: the source dataset is "
            "COP-DEM_GLO-30-DGED, from the Copernicus DEM 2021 release; GLO-30 "
            "Public has LIMITED worldwide coverage because some countries' tiles "
            "are not publicly released; the mirror REMOVED the shared row and "
            "column on each tile's east and south edges, so these tiles are not "
            "byte-identical to ESA's originals; and the tile list CAN BE UPDATED, "
            "so this fetch target is mutable and a re-fetch is not guaranteed to "
            "reproduce these bytes (docs/DECISIONS.md D-0012). the readme also "
            "suggests assuming height zero over ocean, which this package does "
            "NOT do: an absent tile is missing data, not sea level "
            "(docs/FAILURE_MODES.md F-MD-1)."
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
                # AREA_OR_POINT matters: the GLO-30 DGED tiles are
                # RasterPixelIsPoint, and GDAL pre-shifts the origin by half a
                # cell so an area-convention centre lands on the integer
                # arc-second sample. The arithmetic here is right either way,
                # but a downstream tool that honours the tag when resampling can
                # reintroduce the classic half-cell (~15 m) shift, so the tag is
                # recorded rather than discarded.
                tile_tags = dict(dataset.tags())
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
        sources=(_copernicus_source(url, tile_tags=tile_tags),),
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
                    "tile_tags": tile_tags,
                    "grid_alignment": tile_tags.get("AREA_OR_POINT", UNKNOWN),
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
        # Per the COP-DEM collection description page (fetched and read), which
        # states "Vertical EGM2008 (EPSG 3855)" with "Vertical Unit: meters".
        # The tile itself carries no vertical CRS, so the field names where the
        # fact comes from rather than implying the file declared it.
        vertical_datum=(
            "EGM2008 (EPSG:3855), metres - per the COP-DEM collection "
            "description page, not declared in the tile"
        ),
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
        # Both URLs below were verified reachable from this environment.
        licence_url="https://opendatacommons.org/licenses/odbl/1-0/",
        notes=(
            "ODbL is share-alike on derived databases: a study-area bundle "
            "containing this road layer carries ODbL obligations, and "
            "attribution to OpenStreetMap contributors is required "
            "(https://www.openstreetmap.org/copyright). rural Korean coverage "
            "is uneven and completeness is UNKNOWN, so any egress count derived "
            "from this layer is a statement about the data, not about the "
            "ground (docs/FAILURE_MODES.md F-RD-3)."
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
        # A vector layer has no spatial resolution and no vertical datum: those
        # facts do not exist rather than being unknown, and using UNKNOWN for
        # them would dilute the incompleteness metric D-0009 exists to keep
        # meaningful.
        resolution_unit=NOT_APPLICABLE,
        vertical_datum=NOT_APPLICABLE,
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

# --------------------------------------------------------------------------- #
# ESA WorldCover 10 m land cover
# --------------------------------------------------------------------------- #
#: Public S3 bucket serving ESA WorldCover as 3x3 degree COG tiles.
WORLDCOVER_BUCKET = "https://esa-worldcover.s3.eu-central-1.amazonaws.com"

#: Version and reference year this repository fetches. Pinned, because the class
#: set and the vintage are part of the layer's meaning: v100 is 2020 and v200 is
#: 2021, and silently following "latest" would change a bundle's temporal
#: reference without changing its config.
WORLDCOVER_VERSION = "v200"
WORLDCOVER_YEAR = "2021"

#: Pages and documents this repository fetched and read. Each factual claim in
#: the WorldCover provenance is attributed to one of these or to the tile's own
#: metadata (``AGENTS.md`` §4).
WORLDCOVER_MANUAL_URL = (
    f"{WORLDCOVER_BUCKET}/{WORLDCOVER_VERSION}/{WORLDCOVER_YEAR}/docs/"
    "WorldCover_PUM_V2.0.pdf"
)


def worldcover_tile_name(lat_south: int, lon_west: int) -> str:
    """Tile name for the 3x3 degree tile containing the given SW corner.

    Tiles are named by the SW corner of a 3-degree grid, so the corner must be
    snapped down to a multiple of 3 -- a point at 36.9N/129.3E lives in tile
    ``N36E129``, and a point at 35.9N would live in ``N33E129``, not ``N35``.
    """
    lat = (lat_south // 3) * 3
    lon = (lon_west // 3) * 3
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}"


def worldcover_tile_url(lat_south: int, lon_west: int) -> str:
    """Public URL of the WorldCover map COG for a tile."""
    tile = worldcover_tile_name(lat_south, lon_west)
    return (
        f"{WORLDCOVER_BUCKET}/{WORLDCOVER_VERSION}/{WORLDCOVER_YEAR}/map/"
        f"ESA_WorldCover_10m_{WORLDCOVER_YEAR}_{WORLDCOVER_VERSION}_{tile}_Map.tif"
    )


def _worldcover_source(url: str, *, tile_tags: dict[str, str] | None = None) -> SourceRecord:
    """Provenance for a WorldCover tile.

    Every claim is attributed to where it was verified: the tile's own GeoTIFF
    tags, or Table 3 / section 4 of the Product User Manual in the same bucket.
    The licence in particular is read from the **file**, not recalled.
    """
    tags = tile_tags or {}
    return SourceRecord(
        name=(
            f"ESA WorldCover 10 m {WORLDCOVER_YEAR} {WORLDCOVER_VERSION} "
            "(land cover)"
        ),
        url_or_identifier=url,
        # The product represents the reference year in full, per the manual's
        # section 3.4.3 and the tile's own time_start/time_end tags.
        source_date=WORLDCOVER_YEAR,
        acquisition_date=utc_now_iso(),
        publisher=tags.get(
            "copyright",
            "ESA WorldCover project / Contains modified Copernicus Sentinel data",
        ),
        licence=tags.get("license", UNKNOWN),
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        notes=(
            "VERIFIED FROM THE TILE METADATA: "
            f"licence {tags.get('license', UNKNOWN)!r}; "
            f"product_version {tags.get('product_version', UNKNOWN)!r}; "
            f"time_start {tags.get('time_start', UNKNOWN)!r} to "
            f"time_end {tags.get('time_end', UNKNOWN)!r}; "
            f"grid alignment AREA_OR_POINT={tags.get('AREA_OR_POINT', UNKNOWN)!r}; "
            "EPSG:4326, 1/12000 degree cells, uint8, nodata 0. "
            f"VERIFIED FROM {WORLDCOVER_MANUAL_URL}: the 11 class codes and "
            "their definitions (Table 3, page 15), and the limitations recorded "
            "on the class scheme (section 4). "
            "THIS IS LAND COVER, NOT FUEL: 'Tree cover' means canopy cover of "
            "10% or more, with no species, load, or moisture information. "
            "Mapping these classes onto a fire-behaviour fuel model is a "
            "modelling step this repository does not perform "
            "(docs/DECISIONS.md D-0024). Note the manual's own limitation that "
            "mountain shadows are sometimes misclassified as water, which is "
            "directly relevant in steep Korean valleys."
        ),
    )


def fetch_esa_worldcover(
    bounds_wgs84: Bounds,
    *,
    allow_network: bool = False,
    name: str = "landcover_esa_worldcover",
    buffer_deg: float = 0.005,
) -> RasterLayer:
    """Read an ESA WorldCover window covering ``bounds_wgs84``.

    Returns a **categorical** layer in EPSG:4326 at the product's native 10 m,
    carrying :data:`~wildfireguardian_data.fuels.classes.ESA_WORLDCOVER_V200_SCHEME`
    semantics. Reprojection to a metre CRS is the caller's explicit next step,
    and must use nearest-neighbour resampling: averaging class codes invents
    classes (A-FU-3).

    Same limitation as the DEM fetcher: a window spanning more than one 3-degree
    tile raises rather than silently returning part of the request
    (``docs/FAILURE_MODES.md`` F-BND-5).
    """
    _require_network(allow_network, "the ESA WorldCover tile")
    try:
        import rasterio
        from rasterio.windows import from_bounds as window_from_bounds
    except ImportError as exc:  # pragma: no cover
        raise OptionalDependencyError(
            "rasterio", purpose="reading remote ESA WorldCover COG tiles"
        ) from exc

    from .fuels.classes import ESA_WORLDCOVER_V200_SCHEME

    crs = require_crs(bounds_wgs84.crs, context="fetching ESA WorldCover")
    if not crs.equals(parse_crs("EPSG:4326")):
        raise IngestError(
            f"fetch_esa_worldcover needs bounds in EPSG:4326; got "
            f"{crs_to_string(crs)}. convert them explicitly -- this function does "
            "not reproject its input (docs/DECISIONS.md D-0002)."
        )

    requested = bounds_wgs84.buffered(buffer_deg)
    import math

    tiles = {
        worldcover_tile_name(math.floor(lat), math.floor(lon))
        for lat in (requested.min_y, requested.max_y)
        for lon in (requested.min_x, requested.max_x)
    }
    if len(tiles) > 1:
        raise IngestError(
            f"requested extent {requested} spans more than one WorldCover tile "
            f"({sorted(tiles)}). mosaicking is not implemented; returning one "
            "tile would give a study area with an artificial straight edge."
        )
    url = worldcover_tile_url(math.floor(requested.min_y), math.floor(requested.min_x))

    with rasterio.Env(**_gdal_http_options()):
        try:
            with rasterio.open(url) as dataset:
                window = (
                    window_from_bounds(
                        requested.min_x,
                        requested.min_y,
                        requested.max_x,
                        requested.max_y,
                        dataset.transform,
                    )
                    .round_offsets()
                    .round_lengths()
                )
                array = dataset.read(1, window=window)
                transform = GridTransform.from_affine(dataset.window_transform(window))
                tile_crs = parse_crs(dataset.crs.to_wkt())
                file_nodata = dataset.nodatavals[0]
                tile_tags = dict(dataset.tags())
        except rasterio.errors.RasterioIOError as exc:
            raise IngestError(
                f"could not read ESA WorldCover tile {url}: {exc}. the tile may "
                "not exist for this location (ocean), or the network may be "
                "unavailable. document the failure and fall back to fixtures "
                "rather than substituting another dataset silently (AGENTS.md §4)."
            ) from exc

    if array.size == 0:
        raise IngestError(
            f"ESA WorldCover read for {requested} returned an empty window from {url}"
        )

    scheme_nodata = ESA_WORLDCOVER_V200_SCHEME.nodata_code
    if file_nodata is not None and int(file_nodata) != int(scheme_nodata):
        raise IngestError(
            f"{url} declares nodata={file_nodata} but the shipped scheme uses "
            f"nodata_code={scheme_nodata}. two missing-data conventions in one "
            "layer would leave some missing cells indistinguishable from a real "
            "class."
        )
    undefined = ESA_WORLDCOVER_V200_SCHEME.unknown_codes(
        int(code) for code in set(array.ravel().tolist())
    )
    if undefined:
        raise IngestError(
            f"{url} contains class code(s) {list(undefined)} that the shipped "
            f"scheme {ESA_WORLDCOVER_V200_SCHEME.name!r} does not define. the "
            "product may have changed; verify against the Product User Manual "
            "before extending the scheme (AGENTS.md §4)."
        )

    provenance = ProvenanceRecord(
        layer_name=name,
        data_class=DataClass.OBSERVED,
        # One calendar year, stated by the product. Not STATIC: land cover
        # changes, and a 2021 map is a statement about 2021.
        temporal_class=TemporalProvenance.ANNUAL,
        temporal_reference=WORLDCOVER_YEAR,
        sources=(_worldcover_source(url, tile_tags=tile_tags),),
        transformations=(
            Transformation(
                operation="fetch_esa_worldcover_window",
                parameters={
                    "url": url,
                    "tile": worldcover_tile_name(
                        math.floor(requested.min_y), math.floor(requested.min_x)
                    ),
                    "product_version": tile_tags.get("product_version", UNKNOWN),
                    "requested_bounds_wgs84": list(bounds_wgs84.as_tuple()),
                    "buffer_deg": buffer_deg,
                    "read_bounds_wgs84": list(requested.as_tuple()),
                    "window_shape": list(array.shape),
                    "cell_size_deg": [transform.x_size, transform.y_size],
                    "file_nodata": repr(file_nodata),
                    "scheme": ESA_WORLDCOVER_V200_SCHEME.name,
                    "scheme_kind": ESA_WORLDCOVER_V200_SCHEME.scheme_kind.value,
                    "classes_present": sorted(
                        int(code) for code in set(array.ravel().tolist())
                    ),
                    "tile_tags": tile_tags,
                },
                notes=(
                    "windowed COG read over HTTPS; no resampling at this stage. "
                    "categorical: only nearest-neighbour resampling is valid "
                    "downstream (docs/ASSUMPTIONS.md A-FU-3)"
                ),
            ),
        ),
        original_crs=crs_to_string(tile_crs),
        output_crs=crs_to_string(tile_crs),
        spatial_resolution=(transform.x_size, transform.y_size),
        resolution_unit="degree",
        value_unit="class",
        vertical_datum=NOT_APPLICABLE,
        nodata_representation=repr(scheme_nodata),
        checksum=UNKNOWN,
        notes=(
            "LAND COVER, NOT A FUEL MODEL. geographic CRS: reproject with "
            "nearest-neighbour before use on a metre grid (D-0002, D-0010)."
        ),
    )
    return RasterLayer(
        name=name,
        data=array,
        transform=transform,
        crs=tile_crs,
        nodata=scheme_nodata,
        kind=RasterKind.CATEGORICAL,
        value_unit="class",
        provenance=provenance,
    )
