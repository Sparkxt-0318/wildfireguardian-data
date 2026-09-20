"""Vegetation / land-cover class schemes.

This repository stores a source's **own** classification. It does not invent
one, and it does not map one onto a fire-behaviour fuel model
(``docs/ASSUMPTIONS.md`` A-FU-1, Phase 2 brief §6/§7). The distinction is
carried in the type system by :class:`SchemeKind`.

Why that matters here. The bundle's layer is keyed ``fuels`` because that is
what downstream repositories asked for, but what this repository can actually
supply for Korea today is **land cover** -- ESA WorldCover's "Tree cover" is the
output of a classifier over Sentinel imagery, not a statement about how
something burns. Turning land cover into a fuel model requires species
composition, load, moisture and combustion assumptions, which belong to the
future ``wildfireguardian-fuels`` project. A consumer reading a scheme here can
tell which it has from ``scheme_kind`` and ``represents``.

Class codes are **nominal** categories (A-FU-3). Nothing here defines an
ordering, a flammability ranking, or a spread parameter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..errors import ConfigError
from ..provenance.models import UNKNOWN, DataClass

__all__ = [
    "SchemeKind",
    "VegetationClass",
    "VegetationClassScheme",
    "FuelClass",
    "FuelClassScheme",
    "SYNTHETIC_DEMO_SCHEME",
    "ESA_WORLDCOVER_V200_SCHEME",
]


class SchemeKind(str, Enum):
    """Whether a scheme is a source's own classification or a crosswalk.

    The distinction this repository must never blur. A **source class** is what
    the provider published. A **modeled crosswalk** is somebody's mapping of
    those classes onto a fire-behaviour fuel model -- a modelling claim about
    combustion, not an observation of the landscape.

    This repository ships source classes and **no** crosswalk.
    ``MODELED_CROSSWALK`` exists so that a crosswalk arriving from outside must
    label itself as one, and so a bundle can never present one as observed fuel
    truth.
    """

    SOURCE_CLASS = "source_class"
    MODELED_CROSSWALK = "modeled_crosswalk"


@dataclass(frozen=True)
class VegetationClass:
    """One nominal vegetation / land-cover class.

    ``description`` is the source's own definition where one exists. There is
    deliberately no ``flammability``, ``rate_of_spread`` or ``load`` field:
    assigning those is fire-behaviour modelling and does not belong here.
    """

    code: int
    label: str
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.code, int) or isinstance(self.code, bool):
            raise ConfigError(
                f"class code must be an int; got {self.code!r} "
                f"({type(self.code).__name__})"
            )
        if not self.label.strip():
            raise ConfigError(f"class {self.code} has an empty label")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "label": self.label, "description": self.description}


@dataclass(frozen=True)
class VegetationClassScheme:
    """A named set of vegetation classes, with its provenance obligations enforced.

    Parameters
    ----------
    name, classes, data_class, nodata_code:
        Identifier; the classes (codes must be unique); :class:`DataClass`, for
        which ``SYNTHETIC`` is the only value permitted without a real
        ``source``; and the code meaning "no class here", which must not collide
        with a real class -- without it a layer's gaps would have to be written
        as some real class, most likely ``0``, silently turning missing data
        into a land-cover type (``docs/FAILURE_MODES.md`` F-MD-1).
    scheme_kind:
        :class:`SchemeKind`. A ``MODELED_CROSSWALK`` may not also claim
        ``DataClass.OBSERVED``.
    represents:
        What one code describes, in words -- "land cover", "forest type",
        "fire-behaviour fuel model". Recorded because the bundle key ``fuels``
        does not tell a consumer which of those it is holding.
    spatial_resolution_m, vintage, coverage, licence:
        Source product facts, for the documentation set the Phase 2 brief
        requires of every candidate source.
    known_limitations:
        Limitations stated by the **source**, not inferred here.
    """

    name: str
    classes: tuple[VegetationClass, ...]
    data_class: DataClass
    nodata_code: int
    scheme_kind: SchemeKind = SchemeKind.SOURCE_CLASS
    source: str = UNKNOWN
    source_url: str = UNKNOWN
    represents: str = UNKNOWN
    spatial_resolution_m: float | None = None
    vintage: str = UNKNOWN
    coverage: str = UNKNOWN
    licence: str = UNKNOWN
    known_limitations: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "classes", tuple(self.classes))
        object.__setattr__(self, "data_class", DataClass(self.data_class))
        object.__setattr__(self, "scheme_kind", SchemeKind(self.scheme_kind))
        object.__setattr__(self, "known_limitations", tuple(self.known_limitations))
        if not self.name.strip():
            raise ConfigError("VegetationClassScheme.name is required")
        if not self.classes:
            raise ConfigError(f"scheme {self.name!r} defines no classes")
        codes = [c.code for c in self.classes]
        if len(set(codes)) != len(codes):
            duplicates = sorted({c for c in codes if codes.count(c) > 1})
            raise ConfigError(f"scheme {self.name!r} has duplicate class codes {duplicates}")
        if self.nodata_code in codes:
            raise ConfigError(
                f"scheme {self.name!r} uses nodata_code={self.nodata_code}, which is "
                "also a real class code. missing data and a real class must be "
                "distinguishable (docs/ASSUMPTIONS.md A-FU-2)."
            )
        if self.data_class is not DataClass.SYNTHETIC and self.source == UNKNOWN:
            raise ConfigError(
                f"scheme {self.name!r} is declared {self.data_class.value} but cites "
                "no source. a non-synthetic scheme must name where it comes from; "
                "this package does not invent Korean vegetation or fuel "
                "classifications (docs/ASSUMPTIONS.md A-FU-1). if it is a test "
                "construct, declare data_class=DataClass.SYNTHETIC."
            )
        if (
            self.scheme_kind is SchemeKind.MODELED_CROSSWALK
            and self.data_class is DataClass.OBSERVED
        ):
            raise ConfigError(
                f"scheme {self.name!r} is a MODELED_CROSSWALK but declares "
                "data_class=OBSERVED. a crosswalk onto a fuel model is a modelling "
                "claim about combustion, not an observation of the landscape, and "
                "presenting one as observed fuel truth is the specific thing this "
                "repository must not do."
            )

    @property
    def codes(self) -> tuple[int, ...]:
        return tuple(c.code for c in self.classes)

    def label_for(self, code: int) -> str:
        """Label for a code; ``"UNKNOWN"`` for an unrecognised one, never a guess."""
        if code == self.nodata_code:
            return "nodata"
        for entry in self.classes:
            if entry.code == code:
                return entry.label
        return UNKNOWN

    def unknown_codes(self, observed: Iterable[int]) -> tuple[int, ...]:
        """Observed codes the scheme does not define.

        A non-empty result means the raster and the scheme disagree, which is
        reported as a validation ERROR rather than passed through: an undefined
        code is data whose meaning nobody knows.
        """
        known = set(self.codes) | {self.nodata_code}
        return tuple(sorted({int(c) for c in observed} - known))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scheme_kind": self.scheme_kind.value,
            "represents": self.represents,
            "data_class": self.data_class.value,
            "source": self.source,
            "source_url": self.source_url,
            "spatial_resolution_m": self.spatial_resolution_m,
            "vintage": self.vintage,
            "coverage": self.coverage,
            "licence": self.licence,
            "units": "class",
            "nodata_code": self.nodata_code,
            "classes": [c.to_dict() for c in self.classes],
            "known_limitations": list(self.known_limitations),
            "notes": self.notes,
            "semantics": (
                "nominal categories; no ordering, no flammability ranking, no "
                "spread parameters (docs/ASSUMPTIONS.md A-FU-3). "
                + (
                    "these are the SOURCE's own classes, not a fuel model: mapping "
                    "them onto fire-behaviour fuels is a separate modelling step "
                    "that this repository does not perform"
                    if self.scheme_kind is SchemeKind.SOURCE_CLASS
                    else "this is a MODELED CROSSWALK, not observed landscape truth"
                )
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> VegetationClassScheme:
        return cls(
            name=payload["name"],
            classes=tuple(VegetationClass(**c) for c in payload["classes"]),
            data_class=DataClass(payload["data_class"]),
            nodata_code=int(payload["nodata_code"]),
            scheme_kind=SchemeKind(payload.get("scheme_kind", "source_class")),
            source=payload.get("source", UNKNOWN),
            source_url=payload.get("source_url", UNKNOWN),
            represents=payload.get("represents", UNKNOWN),
            spatial_resolution_m=payload.get("spatial_resolution_m"),
            vintage=payload.get("vintage", UNKNOWN),
            coverage=payload.get("coverage", UNKNOWN),
            licence=payload.get("licence", UNKNOWN),
            known_limitations=tuple(payload.get("known_limitations", ())),
            notes=payload.get("notes", ""),
        )


#: Kept as an alias so existing call sites and configs keep working. The name
#: ``FuelClassScheme`` is retained only for compatibility: what this repository
#: can supply for Korea today is land cover, not fuel.
FuelClassScheme = VegetationClassScheme
FuelClass = VegetationClass


#: A deliberately generic, explicitly synthetic scheme used by the fixtures and
#: the synthetic example bundle. The class names describe broad land-cover ideas
#: that are not specific to any Korean dataset, precisely so that nobody
#: mistakes this for a real Korean classification.
SYNTHETIC_DEMO_SCHEME = VegetationClassScheme(
    name="synthetic_demo_v1",
    data_class=DataClass.SYNTHETIC,
    scheme_kind=SchemeKind.SOURCE_CLASS,
    represents="invented land-cover-like classes, for testing only",
    nodata_code=255,
    vintage="not_applicable",
    coverage="not_applicable (synthetic)",
    licence="same as this repository",
    classes=(
        VegetationClass(1, "non_vegetated", "bare ground, rock, built-up, water"),
        VegetationClass(2, "grass_or_crop", "herbaceous cover including paddy and dry field"),
        VegetationClass(3, "shrub", "shrubland and regenerating cover"),
        VegetationClass(4, "broadleaf_forest", "broadleaf-dominated forest canopy"),
        VegetationClass(5, "conifer_forest", "conifer-dominated forest canopy"),
        VegetationClass(6, "mixed_forest", "mixed broadleaf and conifer canopy"),
    ),
    notes=(
        "SYNTHETIC. invented for testing and for the synthetic example bundle; it "
        "is not a crosswalk to any Korean classification and must not be treated "
        "as one."
    ),
)


#: ESA WorldCover 10 m 2021 v200 land cover.
#:
#: Every field below was **verified from the product itself or its manual**, not
#: recalled: the 11 class codes from the tile's colour table, the labels and
#: definitions from Table 3 (page 15) of ``WorldCover_PUM_V2.0.pdf`` in the
#: product bucket, the licence and copyright from the tile's own GeoTIFF tags,
#: and the temporal extent from the manual's §3.4.3 and the tile's
#: ``time_start``/``time_end`` tags. See ``docs/DATA_PROVENANCE.md``.
#:
#: It is **land cover, not fuel**: ``scheme_kind`` is ``SOURCE_CLASS`` and
#: ``represents`` says so.
ESA_WORLDCOVER_V200_SCHEME = VegetationClassScheme(
    name="esa_worldcover_v200",
    data_class=DataClass.OBSERVED,
    scheme_kind=SchemeKind.SOURCE_CLASS,
    represents=(
        "land cover, classified from Sentinel-1 and Sentinel-2 imagery. NOT a "
        "fire-behaviour fuel model and not a forest-type map: 'Tree cover' means "
        "canopy cover >= 10%, with no species, load, or moisture information"
    ),
    nodata_code=0,
    source="ESA WorldCover 10 m 2021 v200 (ESA WorldCover project / Copernicus Sentinel)",
    source_url="https://esa-worldcover.org",
    spatial_resolution_m=10.0,
    vintage="2021 (01 January to 31 December, per Product User Manual V2.0 §3.4.3)",
    coverage="global land areas observed by Sentinel-2, to 82.75 degrees N",
    licence="CC-BY 4.0 (https://creativecommons.org/licenses/by/4.0/)",
    classes=(
        VegetationClass(
            10,
            "tree_cover",
            "any area dominated by trees with cover of 10% or more; understorey, "
            "built-up and water may be present below the canopy",
        ),
        VegetationClass(
            20,
            "shrubland",
            "dominated by natural shrubs with cover of 10% or more; woody "
            "perennials under 5 m with no defined main stem",
        ),
        VegetationClass(
            30,
            "grassland",
            "dominated by natural herbaceous plants with cover of 10% or more; "
            "may include uncultivated cropland",
        ),
        VegetationClass(
            40,
            "cropland",
            "annual cropland, sowed or planted and harvestable at least once "
            "within 12 months of sowing",
        ),
        VegetationClass(50, "built_up", "buildings and other man-made structures"),
        VegetationClass(60, "bare_or_sparse_vegetation", "bare or sparsely vegetated areas"),
        VegetationClass(70, "snow_and_ice", "permanent snow and ice"),
        VegetationClass(80, "permanent_water_bodies", "permanent water bodies"),
        VegetationClass(90, "herbaceous_wetland", "herbaceous wetland"),
        VegetationClass(95, "mangroves", "mangroves"),
        VegetationClass(100, "moss_and_lichen", "moss and lichen"),
    ),
    known_limitations=(
        "cloud artefacts can persist in areas of high cloud cover despite the use "
        "of Sentinel-1 (Product User Manual V2.0 §4)",
        "Sentinel-2 orbit or processing-block borders can be visible as hard edges "
        "where classes are easily confused (PUM §4)",
        "mountain shadows and glacier ablation zones are sometimes misclassified as "
        "water bodies (PUM §4) -- directly relevant in steep Korean valleys, where "
        "a shadowed slope may read as class 80",
        "irrigated agriculture and herbaceous wetland are spectrally similar and "
        "are sometimes confused (PUM §4)",
        "10 m land cover is not a fuel model: it carries no species composition, "
        "fuel load, canopy bulk density, or moisture, all of which a "
        "fire-behaviour model needs",
    ),
    notes=(
        "nodata code is 0, which is the value the product itself uses and which "
        "the tile declares as its nodata. class 0 is therefore NOT a land-cover "
        "class."
    ),
)
