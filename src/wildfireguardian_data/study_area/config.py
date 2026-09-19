"""Study-area build configuration.

Strict by design: an unrecognised key is a :class:`ConfigError`, not something
to ignore. A typo in ``target_resolution_m`` that silently fell back to a
default would produce a bundle whose grid differs from the one the analyst
asked for, with nothing in the output to show it.

Example (see ``configs/`` for working files)::

    study_area_id: uljin_synthetic_valley_v1
    description: Synthetic Korean rural valley, analytically checkable
    crs: EPSG:5187
    bounds: [920000.0, 1720000.0, 926000.0, 1726000.0]
    terrain:
      source:
        kind: synthetic_fixture
        fixture: korean_valley
      target_resolution_m: 30.0
      derivatives: [slope, aspect]
      clip_buffer_cells: 2
    roads:
      source:
        kind: synthetic_fixture
        fixture: korean_valley_roads
      snap_tolerance_m: 1.0
      boundary_tolerance_m: 30.0
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..bounds import Bounds
from ..crs import crs_to_string, parse_crs, require_projected_metre_crs
from ..errors import ConfigError, CRSError

__all__ = [
    "SourceSpec",
    "TerrainConfig",
    "RoadsConfig",
    "FuelsConfig",
    "PopulationConfig",
    "FacilitiesConfig",
    "StudyAreaConfig",
]

_SOURCE_KINDS = {
    "synthetic_fixture",
    "geotiff",
    "geojson",
    "copernicus_dem_glo30",
    "osm_api",
}


def _require_keys(payload: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unexpected = set(payload) - allowed
    if unexpected:
        raise ConfigError(
            f"unrecognised key(s) {sorted(unexpected)} in {where}; allowed: "
            f"{sorted(allowed)}. unknown keys are rejected rather than ignored, "
            "because a silently ignored option produces a bundle that differs "
            "from the one requested with nothing in the output to show it."
        )


@dataclass(frozen=True)
class SourceSpec:
    """Where one layer's data comes from.

    ``kind`` is one of ``synthetic_fixture``, ``geotiff``, ``geojson``,
    ``copernicus_dem_glo30``, ``osm_api``. The last two require network access
    and are refused unless the caller passes ``--allow-network``
    (``docs/DECISIONS.md`` D-0012).
    """

    kind: str
    fixture: str | None = None
    path: str | None = None
    #: Provenance the caller supplies for a local file. A local GeoTIFF does not
    #: know where it came from, and this package will not infer it from the
    #: filename (``AGENTS.md`` §3).
    source_name: str | None = None
    source_url: str | None = None
    source_date: str | None = None
    licence: str | None = None
    publisher: str | None = None
    data_class: str | None = None
    temporal_class: str | None = None
    temporal_reference: str | None = None
    declared_crs: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in _SOURCE_KINDS:
            raise ConfigError(
                f"unknown source kind {self.kind!r}; known: {sorted(_SOURCE_KINDS)}"
            )
        if self.kind == "synthetic_fixture" and not self.fixture:
            raise ConfigError("source kind 'synthetic_fixture' requires 'fixture'")
        if self.kind in {"geotiff", "geojson"} and not self.path:
            raise ConfigError(f"source kind {self.kind!r} requires 'path'")
        object.__setattr__(self, "options", dict(self.options))

    @property
    def requires_network(self) -> bool:
        return self.kind in {"copernicus_dem_glo30", "osm_api"}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str) -> SourceSpec:
        if not isinstance(payload, Mapping):
            raise ConfigError(f"{where}.source must be a mapping; got {type(payload).__name__}")
        _require_keys(payload, set(cls.__dataclass_fields__), f"{where}.source")
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if getattr(self, key) not in (None, {})
        }


@dataclass(frozen=True)
class TerrainConfig:
    """Terrain build options."""

    source: SourceSpec
    target_resolution_m: float | None = None
    derivatives: tuple[str, ...] = ("slope", "aspect")
    #: Extra cells kept around the study area before derivatives are computed.
    #: At least 1 is needed for Horn's 3x3 estimator to cover the whole study
    #: area (``docs/DECISIONS.md`` D-0005); the default of 2 leaves room for a
    #: second derivative pass.
    clip_buffer_cells: int = 2
    resampling: str | None = None
    slope_unit: str = "deg"
    vertical_datum: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "derivatives", tuple(self.derivatives))
        unknown = set(self.derivatives) - {"slope", "aspect"}
        if unknown:
            raise ConfigError(
                f"unknown terrain derivative(s) {sorted(unknown)}; this repository "
                "computes 'slope' and 'aspect' only (docs/SCOPE.md)"
            )
        if self.clip_buffer_cells < 1 and self.derivatives:
            raise ConfigError(
                "clip_buffer_cells must be >= 1 when derivatives are requested: "
                "Horn's 3x3 estimator drops one cell at every edge, so a "
                "zero-buffer clip would leave the study-area boundary with no "
                "slope (docs/DECISIONS.md D-0005)"
            )
        if self.target_resolution_m is not None and self.target_resolution_m <= 0:
            raise ConfigError(
                f"target_resolution_m must be positive; got {self.target_resolution_m}"
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TerrainConfig:
        _require_keys(payload, set(cls.__dataclass_fields__), "terrain")
        data = dict(payload)
        if "source" not in data:
            raise ConfigError("terrain requires a 'source'")
        data["source"] = SourceSpec.from_dict(data["source"], "terrain")
        if "derivatives" in data:
            data["derivatives"] = tuple(data["derivatives"])
        return cls(**data)


@dataclass(frozen=True)
class RoadsConfig:
    """Road build and QA options."""

    source: SourceSpec
    snap_tolerance_m: float = 1.0
    boundary_tolerance_m: float = 30.0
    settlement_snap_m: float = 250.0
    clip_mode: str = "intersects"
    node_crossings: bool = False
    exit_property: str | None = None

    def __post_init__(self) -> None:
        if self.clip_mode not in {"intersects", "within", "truncate"}:
            raise ConfigError(
                f"roads.clip_mode must be 'intersects', 'within' or 'truncate'; "
                f"got {self.clip_mode!r}"
            )
        if self.snap_tolerance_m < 0:
            raise ConfigError("roads.snap_tolerance_m must be >= 0")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RoadsConfig:
        _require_keys(payload, set(cls.__dataclass_fields__), "roads")
        data = dict(payload)
        if "source" not in data:
            raise ConfigError("roads requires a 'source'")
        data["source"] = SourceSpec.from_dict(data["source"], "roads")
        return cls(**data)


@dataclass(frozen=True)
class FuelsConfig:
    """Fuel layer options. ``scheme`` names a scheme known to the build."""

    source: SourceSpec
    scheme: str = "synthetic_demo_v1"
    class_property: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FuelsConfig:
        _require_keys(payload, set(cls.__dataclass_fields__), "fuels")
        data = dict(payload)
        if "source" not in data:
            raise ConfigError("fuels requires a 'source'")
        data["source"] = SourceSpec.from_dict(data["source"], "fuels")
        return cls(**data)


@dataclass(frozen=True)
class PopulationConfig:
    """Aggregate population layer options."""

    source: SourceSpec
    aggregation_level: str = "UNKNOWN"
    id_property: str = "settlement_id"
    name_property: str = "name"
    total_property: str = "population_total"
    age_strata: tuple[tuple[str, int, int | None], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "age_strata",
            tuple(
                (str(entry[0]), int(entry[1]), None if entry[2] is None else int(entry[2]))
                for entry in self.age_strata
            ),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PopulationConfig:
        _require_keys(payload, set(cls.__dataclass_fields__), "population")
        data = dict(payload)
        if "source" not in data:
            raise ConfigError("population requires a 'source'")
        data["source"] = SourceSpec.from_dict(data["source"], "population")
        if "age_strata" in data:
            data["age_strata"] = tuple(tuple(entry) for entry in data["age_strata"])
        return cls(**data)


@dataclass(frozen=True)
class FacilitiesConfig:
    """Facility layer options. ``kind_map`` is required and never inferred."""

    source: SourceSpec
    kind_property: str = "kind"
    kind_map: dict[str, str] = field(default_factory=dict)
    unmapped: str = "error"

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind_map", dict(self.kind_map))
        if not self.kind_map:
            raise ConfigError(
                "facilities.kind_map is required: a facility's role must be mapped "
                "explicitly from the source's own tag, never inferred from its "
                "spelling (docs/ASSUMPTIONS.md A-FAC-2)"
            )
        if self.unmapped not in {"error", "other"}:
            raise ConfigError("facilities.unmapped must be 'error' or 'other'")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FacilitiesConfig:
        _require_keys(payload, set(cls.__dataclass_fields__), "facilities")
        data = dict(payload)
        if "source" not in data:
            raise ConfigError("facilities requires a 'source'")
        data["source"] = SourceSpec.from_dict(data["source"], "facilities")
        return cls(**data)


@dataclass(frozen=True)
class StudyAreaConfig:
    """A complete study-area build specification."""

    study_area_id: str
    crs: Any
    bounds: Bounds
    description: str = ""
    terrain: TerrainConfig | None = None
    roads: RoadsConfig | None = None
    fuels: FuelsConfig | None = None
    population: PopulationConfig | None = None
    facilities: FacilitiesConfig | None = None
    notes: str = ""
    config_path: str | None = None

    def __post_init__(self) -> None:
        if not str(self.study_area_id).strip():
            raise ConfigError("study_area_id is required")
        try:
            crs = require_projected_metre_crs(
                self.crs, context=f"study area {self.study_area_id!r} analysis CRS"
            )
        except CRSError as exc:
            # Re-raised as a ConfigError so the CLI reports it as a usage error:
            # an unusable CRS *in a config file* is a mistake in the request,
            # not a problem with the data. The original message is preserved.
            raise ConfigError(
                f"study area {self.study_area_id!r} declares an unusable analysis "
                f"CRS: {exc}"
            ) from exc
        object.__setattr__(self, "crs", crs)
        self.bounds.require_nondegenerate(f"study area {self.study_area_id!r}")

    @property
    def requires_network(self) -> bool:
        return any(
            component.source.requires_network
            for component in (
                self.terrain,
                self.roads,
                self.fuels,
                self.population,
                self.facilities,
            )
            if component is not None
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "study_area_id": self.study_area_id,
            "description": self.description,
            "crs": crs_to_string(self.crs),
            "bounds": list(self.bounds.as_tuple()),
            "notes": self.notes,
            "config_path": self.config_path,
        }
        for key in ("terrain", "roads", "fuels", "population", "facilities"):
            component = getattr(self, key)
            if component is None:
                continue
            payload: dict[str, Any] = {}
            for field_name in component.__dataclass_fields__:
                value = getattr(component, field_name)
                if field_name == "source":
                    payload["source"] = value.to_dict()
                elif isinstance(value, tuple):
                    payload[field_name] = [
                        list(v) if isinstance(v, tuple) else v for v in value
                    ]
                else:
                    payload[field_name] = value
            out[key] = payload
        return out

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, config_path: str | None = None) -> StudyAreaConfig:
        allowed = {
            "study_area_id",
            "description",
            "crs",
            "bounds",
            "notes",
            "terrain",
            "roads",
            "fuels",
            "population",
            "facilities",
        }
        _require_keys(payload, allowed, "study-area config")
        for required in ("study_area_id", "crs", "bounds"):
            if required not in payload:
                raise ConfigError(
                    f"study-area config is missing required key {required!r}. "
                    "there is no repository-wide default analysis CRS or extent "
                    "(docs/ASSUMPTIONS.md A-CRS-1)."
                )
        crs = parse_crs(payload["crs"])
        bounds = Bounds.from_iterable(payload["bounds"], crs=crs)
        return cls(
            study_area_id=payload["study_area_id"],
            crs=crs,
            bounds=bounds,
            description=payload.get("description", ""),
            notes=payload.get("notes", ""),
            terrain=TerrainConfig.from_dict(payload["terrain"])
            if "terrain" in payload
            else None,
            roads=RoadsConfig.from_dict(payload["roads"]) if "roads" in payload else None,
            fuels=FuelsConfig.from_dict(payload["fuels"]) if "fuels" in payload else None,
            population=PopulationConfig.from_dict(payload["population"])
            if "population" in payload
            else None,
            facilities=FacilitiesConfig.from_dict(payload["facilities"])
            if "facilities" in payload
            else None,
            config_path=config_path,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> StudyAreaConfig:
        source = Path(path)
        if not source.exists():
            raise ConfigError(f"config file not found: {source}")
        try:
            payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"{source} is not valid YAML: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ConfigError(
                f"{source} must contain a YAML mapping at the top level; got "
                f"{type(payload).__name__}"
            )
        return cls.from_dict(payload, config_path=str(source))
