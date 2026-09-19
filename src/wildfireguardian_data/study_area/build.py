"""The study-area build pipeline.

One function, :func:`build_study_area`, turning a
:class:`~.config.StudyAreaConfig` into a
:class:`~.bundle.StudyAreaBundle`. The order of operations is deliberate and
is the same every time:

1. **load** each source in whatever CRS it arrives in;
2. **reproject** explicitly to the study area's analysis CRS, recording the
   resampling method and the resulting cell size (D-0002, A-RAS-4);
3. **clip** to the study bounds *plus a buffer*, because the 3x3 derivative
   loses one cell at every edge (D-0005);
4. **derive** slope and aspect;
5. **assess** the road network's topology;
6. **assemble** the bundle, whose constructor re-checks that every layer shares
   the analysis CRS.

Reproject-then-clip, not clip-then-reproject: clipping first in the source CRS
would cut a box whose edges are not parallel to the analysis grid, leaving
wedge-shaped nodata regions along the study boundary after the warp -- exactly
where slope is then computed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from shapely.geometry import Point

from ..bounds import Bounds
from ..crs import crs_equal, crs_to_string, parse_crs, transformer_for
from ..errors import ConfigError, IngestError, NetworkAccessError
from ..facilities.io import facilities_from_layer, load_facility_layer
from ..facilities.models import FacilityKind
from ..fixtures import synthetic as fixtures
from ..fuels.classes import SYNTHETIC_DEMO_SCHEME, FuelClassScheme
from ..fuels.io import read_fuel_geotiff
from ..population.io import load_population_layer, villages_from_layer
from ..provenance.models import (
    NOT_APPLICABLE,
    UNKNOWN,
    DataClass,
    SourceRecord,
    TemporalProvenance,
    utc_now_iso,
)
from ..raster import RasterKind, RasterLayer
from ..roads.graph import build_road_graph
from ..roads.qa import assess_road_network
from ..terrain.clip import clip_raster, clip_vector
from ..terrain.derivatives import aspect as compute_aspect
from ..terrain.derivatives import slope as compute_slope
from ..terrain.io import read_geotiff
from ..terrain.reproject import reproject_raster
from ..terrain.stats import terrain_statistics
from ..vector import VectorLayer, read_geojson
from .bundle import (
    FacilitiesComponent,
    FuelsComponent,
    PopulationComponent,
    RoadsComponent,
    StudyAreaBundle,
    TerrainComponent,
)
from .config import SourceSpec, StudyAreaConfig

__all__ = ["build_study_area", "KNOWN_FUEL_SCHEMES"]

#: Fuel schemes a config may name. Only the explicitly synthetic demo scheme is
#: shipped: this repository does not invent Korean fuel crosswalks (A-FU-1).
KNOWN_FUEL_SCHEMES: dict[str, FuelClassScheme] = {
    SYNTHETIC_DEMO_SCHEME.name: SYNTHETIC_DEMO_SCHEME
}

#: Facility source tags the example configs use, mapped to roles. Exposed so a
#: config can reference role names as strings while the mapping itself stays
#: explicit (A-FAC-2).
_FACILITY_KINDS = {kind.value: kind for kind in FacilityKind}


def _source_record(spec: SourceSpec, *, fallback_name: str) -> SourceRecord:
    """Build a :class:`SourceRecord` from a config source spec.

    For a local file the caller must supply ``source_name`` and friends; what is
    not supplied stays ``UNKNOWN`` rather than being inferred from the path
    (``AGENTS.md`` §3).
    """
    return SourceRecord(
        name=spec.source_name or fallback_name,
        url_or_identifier=spec.source_url or UNKNOWN,
        source_date=spec.source_date or UNKNOWN,
        acquisition_date=utc_now_iso(),
        publisher=spec.publisher or UNKNOWN,
        licence=spec.licence or UNKNOWN,
        notes=(
            "provenance supplied by the study-area config; fields left UNKNOWN "
            "were not stated by the operator and are not inferred from the file "
            "path or name"
        ),
    )


def _data_class(spec: SourceSpec, default: DataClass) -> DataClass:
    return DataClass(spec.data_class) if spec.data_class else default


def _temporal_class(spec: SourceSpec, default: TemporalProvenance) -> TemporalProvenance:
    return TemporalProvenance(spec.temporal_class) if spec.temporal_class else default


def _bounds_to_wgs84(bounds: Bounds) -> Bounds:
    """Convert study bounds to EPSG:4326 for the remote fetchers.

    Converts all four corners and takes their envelope, rather than only the two
    diagonal corners: a Transverse Mercator box's edges are curved in geographic
    space, so a two-corner conversion under-covers the extent near the middle of
    an edge.
    """
    transformer = transformer_for(bounds.crs, "EPSG:4326")
    corners = [
        (bounds.min_x, bounds.min_y),
        (bounds.min_x, bounds.max_y),
        (bounds.max_x, bounds.min_y),
        (bounds.max_x, bounds.max_y),
    ]
    lons, lats = zip(*(transformer.transform(x, y) for x, y in corners))
    return Bounds(min(lons), min(lats), max(lons), max(lats), crs="EPSG:4326")


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def _load_dem(
    config: StudyAreaConfig, *, allow_network: bool, cache_dir: Path
) -> RasterLayer:
    spec = config.terrain.source  # type: ignore[union-attr]
    if spec.kind == "synthetic_fixture":
        layer = fixtures.make_fixture(spec.fixture, **spec.options)
        if isinstance(layer, tuple):
            raise ConfigError(
                f"terrain fixture {spec.fixture!r} returns several layers; name a "
                "fixture that returns one DEM"
            )
        return layer
    if spec.kind == "geotiff":
        return read_geotiff(
            spec.path,
            name="dem_source",
            source=_source_record(spec, fallback_name=f"local GeoTIFF {spec.path}"),
            data_class=_data_class(spec, DataClass.OBSERVED),
            temporal_class=_temporal_class(spec, TemporalProvenance.STATIC),
            temporal_reference=spec.temporal_reference or UNKNOWN,
            vertical_datum=config.terrain.vertical_datum or UNKNOWN,  # type: ignore[union-attr]
            kind=RasterKind.CONTINUOUS,
        )
    if spec.kind == "copernicus_dem_glo30":
        from ..sources import fetch_copernicus_dem

        return fetch_copernicus_dem(
            _bounds_to_wgs84(config.bounds),
            allow_network=allow_network,
            buffer_deg=float(spec.options.get("buffer_deg", 0.01)),
        )
    raise ConfigError(
        f"source kind {spec.kind!r} is not a terrain source; use "
        "'synthetic_fixture', 'geotiff' or 'copernicus_dem_glo30'"
    )


def _load_vector(
    spec: SourceSpec,
    *,
    name: str,
    feature_kind: str,
    default_data_class: DataClass,
    default_temporal_class: TemporalProvenance,
    config: StudyAreaConfig,
    allow_network: bool,
    cache_dir: Path,
    loader: Any = None,
    loader_kwargs: dict[str, Any] | None = None,
) -> VectorLayer:
    """Load one vector layer according to its source spec."""
    if spec.kind == "synthetic_fixture":
        produced = fixtures.make_fixture(spec.fixture, **spec.options)
        if isinstance(produced, VectorLayer):
            return produced
        raise ConfigError(
            f"fixture {spec.fixture!r} does not return a single VectorLayer; it "
            f"returned {type(produced).__name__}"
        )
    if spec.kind == "geojson":
        kwargs = dict(loader_kwargs or {})
        return (loader or load_facility_layer)(
            spec.path,
            name=name,
            source=_source_record(spec, fallback_name=f"local GeoJSON {spec.path}"),
            data_class=_data_class(spec, default_data_class),
            temporal_class=_temporal_class(spec, default_temporal_class),
            crs=parse_crs(spec.declared_crs) if spec.declared_crs else None,
            temporal_reference=spec.temporal_reference or UNKNOWN,
            **kwargs,
        )
    if spec.kind == "osm_api":
        from ..sources import fetch_osm_roads

        return fetch_osm_roads(
            _bounds_to_wgs84(config.bounds),
            allow_network=allow_network,
            name=name,
            cache_path=cache_dir / f"{config.study_area_id}_{name}_osm.xml",
        )
    raise ConfigError(f"source kind {spec.kind!r} cannot provide vector layer {name!r}")


def _to_analysis_crs_vector(layer: VectorLayer, config: StudyAreaConfig) -> VectorLayer:
    if crs_equal(layer.crs, config.crs):
        return layer
    return layer.reproject(config.crs, name=layer.name)


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build_study_area(
    config: StudyAreaConfig,
    *,
    allow_network: bool = False,
    cache_dir: str | Path = "data/raw",
) -> StudyAreaBundle:
    """Build a study-area bundle from a config.

    Parameters
    ----------
    allow_network:
        Required for any source kind that fetches from the network
        (D-0012). A build whose config needs the network without this raises
        :class:`~wildfireguardian_data.errors.NetworkAccessError` before any
        work is done, rather than part-way through.
    cache_dir:
        Where fetched raw data is written. Git-ignored by default.
    """
    if config.requires_network and not allow_network:
        raise NetworkAccessError(
            f"study area {config.study_area_id!r} has at least one network "
            "source but allow_network is false. pass --allow-network, or switch "
            "the source to a local file or a synthetic fixture "
            "(docs/DECISIONS.md D-0012)."
        )
    cache = Path(cache_dir)

    terrain_component: TerrainComponent | None = None
    roads_component: RoadsComponent | None = None
    fuels_component: FuelsComponent | None = None
    population_component: PopulationComponent | None = None
    facilities_component: FacilitiesComponent | None = None
    build_notes: list[str] = []

    # -- terrain ------------------------------------------------------------ #
    if config.terrain is not None:
        dem_source = _load_dem(config, allow_network=allow_network, cache_dir=cache)
        dem = dem_source
        if not crs_equal(dem.crs, config.crs):
            dem = reproject_raster(
                dem,
                config.crs,
                resampling=config.terrain.resampling,
                dst_resolution=config.terrain.target_resolution_m,
                name=f"{dem.name}_analysis_crs",
            )
            build_notes.append(
                f"terrain reprojected from {crs_to_string(dem_source.crs)} to "
                f"{crs_to_string(config.crs)} at "
                f"{dem.resolution[0]:g}x{dem.resolution[1]:g} m"
            )
        elif (
            config.terrain.target_resolution_m is not None
            and abs(dem.resolution[0] - config.terrain.target_resolution_m) > 1e-9
        ):
            raise ConfigError(
                f"terrain source is already in {crs_to_string(config.crs)} at "
                f"{dem.resolution[0]:g} m, but target_resolution_m is "
                f"{config.terrain.target_resolution_m:g}. resampling within one "
                "CRS is not performed implicitly: it would change every value "
                "with nothing in the request to show which estimator was used. "
                "reproject or resample explicitly, or drop target_resolution_m."
            )

        dem = clip_raster(
            dem,
            config.bounds,
            buffer_cells=config.terrain.clip_buffer_cells,
            name="dem",
        )
        dem.require_any_valid(context=f"clipping the DEM to {config.study_area_id!r}")

        slope_layer = None
        aspect_layer = None
        if "slope" in config.terrain.derivatives:
            slope_layer = compute_slope(
                dem, unit=config.terrain.slope_unit, name=f"slope_{config.terrain.slope_unit}"
            )
        if "aspect" in config.terrain.derivatives:
            aspect_layer = compute_aspect(dem, name="aspect_deg")

        terrain_component = TerrainComponent(
            dem=dem,
            slope=slope_layer,
            aspect=aspect_layer,
            statistics=terrain_statistics(
                dem, slope_layer=slope_layer, aspect_layer=aspect_layer
            ),
        )

    # -- population (loaded before roads, so settlements can be matched) ---- #
    if config.population is not None:
        spec = config.population.source
        population_layer = _load_vector(
            spec,
            name="villages",
            feature_kind="settlement",
            default_data_class=DataClass.OBSERVED,
            default_temporal_class=TemporalProvenance.ANNUAL,
            config=config,
            allow_network=allow_network,
            cache_dir=cache,
            loader=load_population_layer,
            loader_kwargs={"aggregation_level": config.population.aggregation_level},
        )
        population_layer = _to_analysis_crs_vector(population_layer, config)
        population_layer = clip_vector(
            population_layer, config.bounds, mode="intersects", name="villages"
        )
        villages = villages_from_layer(
            population_layer,
            id_property=config.population.id_property,
            name_property=config.population.name_property,
            total_property=config.population.total_property,
            age_stratum_properties=config.population.age_strata,
        )
        population_component = PopulationComponent(layer=population_layer, villages=villages)

    # -- roads -------------------------------------------------------------- #
    if config.roads is not None:
        roads_layer = _load_vector(
            config.roads.source,
            name="roads",
            feature_kind="road segment",
            default_data_class=DataClass.OBSERVED,
            default_temporal_class=TemporalProvenance.OBSERVATION_TIME,
            config=config,
            allow_network=allow_network,
            cache_dir=cache,
            loader=None,
        )
        roads_layer = _to_analysis_crs_vector(roads_layer, config)
        roads_layer = clip_vector(
            roads_layer, config.bounds, mode=config.roads.clip_mode, name="roads"
        )
        graph = build_road_graph(
            roads_layer,
            snap_tolerance_m=config.roads.snap_tolerance_m,
            node_crossings=config.roads.node_crossings,
        )
        settlements: list[tuple[str, Point]] = []
        if population_component is not None:
            for village in population_component.villages:
                if village.centroid is not None:
                    settlements.append((village.settlement_id, village.centroid))
        qa = assess_road_network(
            graph,
            study_bounds=config.bounds,
            boundary_tolerance_m=config.roads.boundary_tolerance_m,
            exit_property=config.roads.exit_property,
            settlements=settlements,
            settlement_snap_m=config.roads.settlement_snap_m,
        )
        roads_component = RoadsComponent(layer=roads_layer, graph=graph, qa=qa)

    # -- fuels -------------------------------------------------------------- #
    if config.fuels is not None:
        scheme = KNOWN_FUEL_SCHEMES.get(config.fuels.scheme)
        if scheme is None:
            raise ConfigError(
                f"unknown fuel scheme {config.fuels.scheme!r}; known: "
                f"{sorted(KNOWN_FUEL_SCHEMES)}. this repository ships no real "
                "Korean fuel scheme (docs/ASSUMPTIONS.md A-FU-1); register one "
                "explicitly with its source before referencing it."
            )
        spec = config.fuels.source
        if spec.kind == "synthetic_fixture":
            template = terrain_component.dem if terrain_component is not None else None
            fuel_layer = fixtures.make_fixture(
                spec.fixture, template=template, **spec.options
            )
        elif spec.kind == "geotiff":
            fuel_layer = read_fuel_geotiff(
                spec.path,
                name="fuels",
                scheme=scheme,
                source=_source_record(spec, fallback_name=f"local GeoTIFF {spec.path}"),
                data_class=_data_class(spec, DataClass.OBSERVED),
                temporal_class=_temporal_class(spec, TemporalProvenance.ANNUAL),
                temporal_reference=spec.temporal_reference or UNKNOWN,
            )
        else:
            raise ConfigError(
                f"source kind {spec.kind!r} is not a fuels source; use "
                "'synthetic_fixture' or 'geotiff'"
            )
        if not crs_equal(fuel_layer.crs, config.crs):
            fuel_layer = reproject_raster(
                fuel_layer,
                config.crs,
                resampling="nearest",
                dst_resolution=config.terrain.target_resolution_m
                if config.terrain is not None
                else None,
                name=f"{fuel_layer.name}_analysis_crs",
            )
        if terrain_component is not None:
            fuel_layer = clip_raster(
                fuel_layer, terrain_component.dem.bounds, allow_partial=True, name="fuels"
            )
        else:
            fuel_layer = clip_raster(fuel_layer, config.bounds, name="fuels")
        fuels_component = FuelsComponent(layer=fuel_layer, scheme=scheme)

    # -- facilities --------------------------------------------------------- #
    if config.facilities is not None:
        facility_layer = _load_vector(
            config.facilities.source,
            name="facilities",
            feature_kind="facility",
            default_data_class=DataClass.OBSERVED,
            default_temporal_class=TemporalProvenance.ANNUAL,
            config=config,
            allow_network=allow_network,
            cache_dir=cache,
            loader=load_facility_layer,
        )
        facility_layer = _to_analysis_crs_vector(facility_layer, config)
        facility_layer = clip_vector(
            facility_layer, config.bounds, mode="intersects", name="facilities"
        )
        kind_map = {}
        for tag, role in config.facilities.kind_map.items():
            if role not in _FACILITY_KINDS:
                raise ConfigError(
                    f"facilities.kind_map maps {tag!r} to unknown role {role!r}; "
                    f"known roles: {sorted(_FACILITY_KINDS)}"
                )
            kind_map[tag] = _FACILITY_KINDS[role]
        records = facilities_from_layer(
            facility_layer,
            kind_property=config.facilities.kind_property,
            kind_map=kind_map,
            unmapped=config.facilities.unmapped,
        )
        facilities_component = FacilitiesComponent(layer=facility_layer, records=records)

    bundle = StudyAreaBundle(
        study_area_id=config.study_area_id,
        crs=config.crs,
        bounds=config.bounds,
        terrain=terrain_component,
        roads=roads_component,
        fuels=fuels_component,
        population=population_component,
        facilities=facilities_component,
        metadata={
            "description": config.description,
            "notes": config.notes,
            "built_at": utc_now_iso(),
            "config": config.to_dict(),
            "allow_network": allow_network,
            "build_notes": build_notes,
            "pipeline_order": [
                "load",
                "reproject_to_analysis_crs",
                "clip_with_derivative_buffer",
                "derive_slope_aspect",
                "assess_road_topology",
                "assemble",
            ],
        },
    )
    return bundle
