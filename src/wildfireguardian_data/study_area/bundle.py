"""The internal study-area model.

``StudyAreaBundle`` is **internal and unstable** (``docs/DECISIONS.md`` D-0001,
``docs/INTERFACES.md``). It is not a contract for other WildfireGuardian
repositories, and no downstream repository should code against it yet.

What it does guarantee, at construction time:

* every layer in the bundle declares the **same** CRS as the bundle, or
  construction fails (D-0002) -- so a bundle cannot exist in a mixed-CRS state;
* every layer carries provenance, indexed by layer name;
* the bundle's bounds are non-degenerate and in the bundle's CRS.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ..bounds import Bounds
from ..crs import crs_equal, crs_to_string, require_crs
from ..errors import BundleError, CRSMismatchError
from ..provenance.models import ProvenanceRecord
from ..raster import RasterLayer
from ..vector import VectorLayer

__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "TerrainComponent",
    "RoadsComponent",
    "FuelsComponent",
    "PopulationComponent",
    "FacilitiesComponent",
    "StudyAreaBundle",
]

#: Bumped whenever the on-disk bundle layout changes. A reader that does not
#: recognise the version must fail rather than guess (``docs/INTERFACES.md``).
#:
#: 1.1.0 added ``extras_checksums_sha256``, ``provenance_checksums_sha256`` and
#: ``unchecksummed`` to the manifest. Additive, but the version is still bumped
#: and comparison stays exact: a 1.0.0 bundle has unverifiable extras, and
#: silently accepting one would mean the integrity guarantee differed between
#: bundles without a reader being able to tell.
BUNDLE_SCHEMA_VERSION = "1.1.0"


@dataclass
class TerrainComponent:
    """DEM plus any derivatives computed from it."""

    dem: RasterLayer
    slope: RasterLayer | None = None
    aspect: RasterLayer | None = None
    statistics: dict[str, Any] = field(default_factory=dict)

    def layers(self) -> list[RasterLayer]:
        return [layer for layer in (self.dem, self.slope, self.aspect) if layer is not None]


@dataclass
class RoadsComponent:
    """Road line layer, its graph, and its QA report."""

    layer: VectorLayer
    graph: Any = None  # roads.graph.RoadGraph; typed loosely to avoid a cycle
    qa: Any = None  # roads.qa.RoadNetworkQA

    def layers(self) -> list[VectorLayer]:
        return [self.layer]


@dataclass
class FuelsComponent:
    """Fuel/vegetation raster and the class scheme that gives it meaning.

    The scheme is part of the component, not a separate lookup: a fuel raster
    without its scheme is a grid of meaningless integers.
    """

    layer: RasterLayer
    scheme: Any = None  # fuels.classes.FuelClassScheme

    def layers(self) -> list[RasterLayer]:
        return [self.layer]


@dataclass
class PopulationComponent:
    """Aggregate settlement layer and its parsed village records."""

    layer: VectorLayer
    villages: tuple[Any, ...] = ()

    def layers(self) -> list[VectorLayer]:
        return [self.layer]


@dataclass
class FacilitiesComponent:
    """Facility layer and its parsed records."""

    layer: VectorLayer
    records: tuple[Any, ...] = ()

    def layers(self) -> list[VectorLayer]:
        return [self.layer]


@dataclass
class StudyAreaBundle:
    """Everything this repository knows about one study area.

    Parameters
    ----------
    study_area_id:
        Stable identifier. Must contain ``synthetic`` when the bundle is built
        from synthetic fixtures and is named after a real place, so that a
        synthetic bundle cannot be mistaken for a real one by its name alone
        (``AGENTS.md`` §4).
    crs:
        The one analysis CRS for the whole bundle.
    bounds:
        Study-area extent in ``crs``.
    """

    study_area_id: str
    crs: Any
    bounds: Bounds
    terrain: TerrainComponent | None = None
    roads: RoadsComponent | None = None
    fuels: FuelsComponent | None = None
    population: PopulationComponent | None = None
    facilities: FacilitiesComponent | None = None
    provenance: dict[str, ProvenanceRecord] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not str(self.study_area_id).strip():
            raise BundleError("study_area_id is required")
        analysis_crs = require_crs(self.crs, context="constructing a study-area bundle")
        self.crs = analysis_crs
        if not crs_equal(self.bounds.crs, analysis_crs):
            raise CRSMismatchError(
                crs_to_string(self.bounds.crs),
                crs_to_string(analysis_crs),
                context=f"bundle {self.study_area_id!r} bounds vs analysis CRS",
            )
        self.bounds.require_nondegenerate(f"study area {self.study_area_id!r}")

        for layer in self.all_layers():
            if not crs_equal(layer.crs, analysis_crs):
                raise CRSMismatchError(
                    crs_to_string(layer.crs),
                    crs_to_string(analysis_crs),
                    context=(
                        f"layer {layer.name!r} in bundle {self.study_area_id!r}; "
                        "reproject it explicitly before adding it to a bundle"
                    ),
                )
            self.provenance.setdefault(layer.name, layer.provenance)

    # -- access ------------------------------------------------------------- #
    def components(self) -> dict[str, Any]:
        return {
            "terrain": self.terrain,
            "roads": self.roads,
            "fuels": self.fuels,
            "population": self.population,
            "facilities": self.facilities,
        }

    def all_layers(self) -> list[Any]:
        """Every raster and vector layer in the bundle, in a stable order."""
        out: list[Any] = []
        for component in self.components().values():
            if component is not None:
                out.extend(component.layers())
        return out

    def raster_layers(self) -> list[RasterLayer]:
        return [layer for layer in self.all_layers() if isinstance(layer, RasterLayer)]

    def vector_layers(self) -> list[VectorLayer]:
        return [layer for layer in self.all_layers() if isinstance(layer, VectorLayer)]

    def layer(self, name: str) -> Any:
        for candidate in self.all_layers():
            if candidate.name == name:
                return candidate
        raise BundleError(
            f"bundle {self.study_area_id!r} has no layer {name!r}; it has "
            f"{[layer.name for layer in self.all_layers()]}"
        )

    @property
    def layer_names(self) -> list[str]:
        return [layer.name for layer in self.all_layers()]

    def __iter__(self) -> Iterator[Any]:
        return iter(self.all_layers())

    def describe(self) -> dict[str, Any]:
        """A JSON-safe description, used by ``wg-data summarize-study-area``."""
        return {
            "schema_version": self.schema_version,
            "study_area_id": self.study_area_id,
            "crs": crs_to_string(self.crs),
            "bounds": self.bounds.to_dict(),
            "extent_m": [self.bounds.width, self.bounds.height],
            "layers": [layer.describe() for layer in self.all_layers()],
            "components_present": sorted(
                key for key, value in self.components().items() if value is not None
            ),
            "metadata": dict(self.metadata),
            "caveats": [
                "StudyAreaBundle is internal and unstable; no downstream "
                "repository should depend on this layout yet "
                "(docs/DECISIONS.md D-0001)",
                "no layer, field, or metric in this bundle asserts that any "
                "road, shelter, refuge, or area is safe or passable "
                "(docs/SCOPE.md)",
            ],
        }
