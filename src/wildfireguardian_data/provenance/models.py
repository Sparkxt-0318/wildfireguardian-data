"""Provenance data model.

A layer without provenance is not a layer this package will emit. Every artifact
records where it came from, when, what was done to it, in which CRS, at which
resolution, with which units, and with which checksum
(``docs/DATA_PROVENANCE.md``).

Two rules govern this module:

* **UNKNOWN is a value** (``docs/DECISIONS.md`` D-0009). A fact the source does
  not supply is the literal string ``"UNKNOWN"`` -- never omitted, never
  ``None``, never inferred.
* **Nothing has a default that makes a scientific claim.** ``data_class`` and
  ``temporal_class`` are required arguments precisely because a wrong default
  (``OBSERVED``, ``STATIC``) would be believed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ..errors import ProvenanceError

__all__ = [
    "UNKNOWN",
    "NOT_APPLICABLE",
    "combine_data_classes",
    "governance_class_name",
    "PROVENANCE_SCHEMA_VERSION",
    "DataClass",
    "TemporalProvenance",
    "SourceRecord",
    "Transformation",
    "ProvenanceRecord",
    "utc_now_iso",
    "validate_temporal_string",
]

#: The one permitted stand-in for a fact this repository does not know.
UNKNOWN = "UNKNOWN"

#: For a fact that does not exist rather than one that is unknown. A synthetic
#: generator has no ``source_date``: the data describes no moment in the world,
#: so ``"UNKNOWN"`` would falsely imply there is a date nobody looked up.
#: Distinguished from ``UNKNOWN`` because only ``UNKNOWN`` counts as a gap in
#: :meth:`ProvenanceRecord.unknown_fields` and in validation
#: (``docs/DECISIONS.md`` D-0009).
NOT_APPLICABLE = "not_applicable"

#: Bumped whenever the serialised shape of :class:`ProvenanceRecord` changes.
#: A reader that does not recognise the version must fail, not guess
#: (``docs/INTERFACES.md``).
PROVENANCE_SCHEMA_VERSION = "1.0.0"


class DataClass(str, Enum):
    """What kind of thing a layer's values are.

    Aligned with ``wildfireguardian-research-governance``
    ``governance/DATA_CLASSES.md`` (AUTHORITATIVE, Tier 1), whose governing rule
    is that **there is no unclassified scientific input**. All seven governance
    classes are present, but this repository only ever *emits* four of them:

    * ``OBSERVED``, ``DERIVED``, ``MODELED``, ``SYNTHETIC`` -- what a data
      foundation can produce.
    * ``ASSUMED`` exists so an inbound value can be labelled and so this
      repository can **refuse** to emit one: ``AGENTS.md`` §3 forbids setting a
      value from what is typical, which is exactly what ``ASSUMED`` describes.
    * ``RETROSPECTIVE`` and ``ORACLE_ONLY`` exist for **rejection at the
      boundary**. This repository produces no oracle quantities, and a
      retrospective layer is refused as a landscape input for a forecast-time
      study (D-0023).

    There is no default. A layer must declare whether its numbers were measured,
    modelled, derived here, or made up for a test.

    Serialised lowercase in this repository's own artifacts; the export boundary
    emits **uppercase**, as governance requires (their ``OC-029``). See
    :func:`governance_class_name`.
    """

    OBSERVED = "observed"
    MODELED = "modeled"
    DERIVED = "derived"
    SYNTHETIC = "synthetic"
    ASSUMED = "assumed"
    RETROSPECTIVE = "retrospective"
    ORACLE_ONLY = "oracle_only"

    @property
    def planner_legal(self) -> bool:
        """Whether governance permits a planner to consume this class.

        ``RETROSPECTIVE`` and ``ORACLE_ONLY`` are never planner-legal, under any
        framing -- including "the planner only uses a summary of it", since
        governance's class algebra states that aggregation does not downgrade
        class. ``SYNTHETIC`` is legal only through a declared observation
        operator, which is not something this repository provides, so it is
        reported as not planner-legal from here.
        """
        return self in {
            DataClass.OBSERVED,
            DataClass.DERIVED,
            DataClass.MODELED,
            DataClass.ASSUMED,
        }


#: Governance's class algebra, in precedence order (``DATA_CLASSES.md`` §2):
#: any ORACLE_ONLY input makes the result ORACLE_ONLY; else any RETROSPECTIVE
#: input makes it RETROSPECTIVE; else a model output makes it MODELED; else
#: DERIVED. Aggregation never downgrades class, "because summarization is the
#: most common disguise for leakage".
_CLASS_PRECEDENCE: tuple[DataClass, ...] = (
    DataClass.ORACLE_ONLY,
    DataClass.RETROSPECTIVE,
    DataClass.MODELED,
    DataClass.ASSUMED,
    DataClass.DERIVED,
    DataClass.SYNTHETIC,
    DataClass.OBSERVED,
)


def combine_data_classes(classes: Iterable[DataClass]) -> DataClass:
    """Apply governance's class algebra to a set of input classes.

    Used when a layer is derived from several parents. The dominating class
    wins, so a derivation cannot launder an ``ORACLE_ONLY`` or
    ``RETROSPECTIVE`` input into something planner-legal.
    """
    present = {DataClass(c) for c in classes}
    if not present:
        raise ProvenanceError("combine_data_classes needs at least one class")
    for candidate in _CLASS_PRECEDENCE:
        if candidate in present:
            # A pure-SYNTHETIC or pure-OBSERVED set keeps its own class; a mix
            # of OBSERVED with anything transformed is DERIVED, which is what
            # the caller passes explicitly.
            return candidate
    raise ProvenanceError(f"unrecognised data classes: {present}")


def governance_class_name(data_class: DataClass) -> str:
    """The uppercase spelling governance requires at an integration boundary."""
    return DataClass(data_class).value.upper()


class TemporalProvenance(str, Enum):
    """What period a layer's values refer to. Definitions: ``docs/GLOSSARY.md``.

    ``RETROSPECTIVE`` versus ``OBSERVATION_TIME`` is the load-bearing
    distinction: a retrospective layer was compiled after the fact and could not
    have been available to a real-time decision. Mislabelling one as the other
    is the temporal analogue of data leakage.
    """

    STATIC = "static"
    ANNUAL = "annual"
    MONTHLY = "monthly"
    OBSERVATION_TIME = "observation_time"
    RETROSPECTIVE = "retrospective"


def utc_now_iso() -> str:
    """Current time as a timezone-aware ISO-8601 string (UTC).

    Timezone-aware by construction: naive timestamps are rejected everywhere in
    this package (``docs/ASSUMPTIONS.md`` A-T-2).
    """
    return datetime.now(timezone.utc).isoformat()


_YEAR = re.compile(r"^\d{4}$")
_YEAR_MONTH = re.compile(r"^\d{4}-\d{2}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_temporal_string(value: Any, field_name: str) -> str:
    """Validate a temporal field, preserving its *precision*.

    Accepts, and returns unchanged as a string:

    * ``"UNKNOWN"`` (the fact is not known) and ``"not_applicable"`` (there is
      no such fact -- a synthetic layer describes no moment in the world);
    * ``"2023"`` (year precision -- what an annual product actually knows);
    * ``"2023-05"`` (month precision);
    * ``"2023-05-01"`` (day precision);
    * a full ISO-8601 datetime **with an explicit UTC offset**.

    Rejects a full datetime without an offset. A naive ``"2026-03-14T09:00:00"``
    from a Korean source is nine hours from a naive one read as UTC, which is
    long enough to move an event to the wrong day and hence the wrong fire
    danger rating.

    Precision is preserved rather than normalised because padding ``"2023"`` to
    ``"2023-01-01"`` invents a day the source never stated.
    """
    if value is None:
        raise ProvenanceError(
            f"{field_name} is None; use the literal 'UNKNOWN' when the fact is "
            "not known (docs/DECISIONS.md D-0009)."
        )
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ProvenanceError(
                f"{field_name} is a naive datetime ({value!r}); attach a "
                "timezone (Korean local time is 'Asia/Seoul', UTC+09:00) "
                "(docs/ASSUMPTIONS.md A-T-2)."
            )
        return value.isoformat()

    text = str(value).strip()
    if text in (UNKNOWN, NOT_APPLICABLE):
        return text
    if not text:
        raise ProvenanceError(
            f"{field_name} is empty; use 'UNKNOWN' if the fact is not known."
        )
    if _YEAR.match(text) or _YEAR_MONTH.match(text) or _DATE.match(text):
        return text
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProvenanceError(
            f"{field_name}={text!r} is not ISO-8601 (expected 'UNKNOWN', "
            f"'YYYY', 'YYYY-MM', 'YYYY-MM-DD', or a datetime with offset): {exc}"
        ) from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ProvenanceError(
            f"{field_name}={text!r} is a datetime without a UTC offset. "
            "a Korean local timestamp read as UTC is off by 9 hours "
            "(docs/ASSUMPTIONS.md A-T-2)."
        )
    return text


@dataclass(frozen=True)
class SourceRecord:
    """Where a layer's data came from.

    ``source_date`` (when the data describes the world) and ``acquisition_date``
    (when this repository obtained it) are separate fields and neither
    substitutes for the other (A-T-3).
    """

    name: str
    url_or_identifier: str = UNKNOWN
    source_date: str = UNKNOWN
    acquisition_date: str = UNKNOWN
    publisher: str = UNKNOWN
    licence: str = UNKNOWN
    licence_url: str = UNKNOWN
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            raise ProvenanceError(
                "SourceRecord.name is required; name the dataset or the "
                "generator, e.g. 'Copernicus DEM GLO-30' or "
                "'wildfireguardian_data.fixtures.tilted_plane'."
            )
        object.__setattr__(
            self, "source_date", validate_temporal_string(self.source_date, "source_date")
        )
        object.__setattr__(
            self,
            "acquisition_date",
            validate_temporal_string(self.acquisition_date, "acquisition_date"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url_or_identifier": self.url_or_identifier,
            "source_date": self.source_date,
            "acquisition_date": self.acquisition_date,
            "publisher": self.publisher,
            "licence": self.licence,
            "licence_url": self.licence_url,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SourceRecord:
        known = {f for f in cls.__dataclass_fields__}
        unexpected = set(payload) - known
        if unexpected:
            raise ProvenanceError(
                f"unexpected SourceRecord fields {sorted(unexpected)}; refusing "
                "to silently drop provenance content."
            )
        return cls(**payload)


@dataclass(frozen=True)
class Transformation:
    """One recorded operation applied to a layer.

    The parameter dict is the point of the record: ``"reproject"`` alone does not
    let a reader reconstruct what happened, whereas
    ``{"resampling": "bilinear", "src_crs": "EPSG:4326", "dst_crs": "EPSG:5187",
    "dst_res_m": [30.0, 30.0]}`` does.
    """

    operation: str
    parameters: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now_iso)
    tool: str = "wildfireguardian_data"
    tool_version: str = UNKNOWN
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.operation or not str(self.operation).strip():
            raise ProvenanceError("Transformation.operation is required")
        object.__setattr__(
            self, "timestamp", validate_temporal_string(self.timestamp, "timestamp")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "parameters": _jsonable(self.parameters),
            "timestamp": self.timestamp,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Transformation:
        known = {f for f in cls.__dataclass_fields__}
        unexpected = set(payload) - known
        if unexpected:
            raise ProvenanceError(
                f"unexpected Transformation fields {sorted(unexpected)}"
            )
        return cls(**payload)


@dataclass(frozen=True)
class ProvenanceRecord:
    """The full provenance of one layer.

    Required by every layer this package emits. ``data_class`` and
    ``temporal_class`` have no defaults on purpose (see module docstring).
    """

    layer_name: str
    data_class: DataClass
    temporal_class: TemporalProvenance
    sources: tuple[SourceRecord, ...] = ()
    transformations: tuple[Transformation, ...] = ()
    #: Temporal reference of the values: a year for ANNUAL, a timestamp for
    #: OBSERVATION_TIME, ``"UNKNOWN"`` when the source does not say, and
    #: ``"not_applicable"`` only for a genuinely STATIC synthetic construct.
    temporal_reference: str = UNKNOWN
    original_crs: str = UNKNOWN
    output_crs: str = UNKNOWN
    #: ``(x_size, y_size)`` as positive lengths in ``resolution_unit``. A pair,
    #: never a single number: naive reprojection produces non-square cells
    #: (``docs/GLOSSARY.md``). ``None`` for vector layers.
    spatial_resolution: tuple[float, float] | None = None
    resolution_unit: str = UNKNOWN
    #: Unit of the layer's *values*: ``"m"`` for elevation, ``"deg"`` for slope
    #: or aspect, ``"count"`` for population, ``"class"`` for categorical.
    value_unit: str = UNKNOWN
    vertical_datum: str = UNKNOWN
    #: How missing values are represented, as text (``"nan"``, ``"-9999"``,
    #: ``"none_declared"``). Text because the value's dtype varies and because
    #: ``"none_declared"`` is a distinct, important state (A-RAS-3).
    nodata_representation: str = UNKNOWN
    checksum: str = UNKNOWN
    checksum_algorithm: str = "sha256"
    #: Layer names this layer was derived from. A DERIVED layer with no parents
    #: is a provenance error.
    parents: tuple[str, ...] = ()
    #: Seed for any randomness used to construct the layer (A-REP-1).
    random_seed: int | None = None
    notes: str = ""
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.layer_name or not str(self.layer_name).strip():
            raise ProvenanceError("ProvenanceRecord.layer_name is required")
        object.__setattr__(self, "data_class", DataClass(self.data_class))
        object.__setattr__(
            self, "temporal_class", TemporalProvenance(self.temporal_class)
        )
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "transformations", tuple(self.transformations))
        object.__setattr__(self, "parents", tuple(self.parents))

        object.__setattr__(
            self,
            "temporal_reference",
            validate_temporal_string(self.temporal_reference, "temporal_reference"),
        )

        if self.spatial_resolution is not None:
            res = tuple(float(v) for v in self.spatial_resolution)
            if len(res) != 2:
                raise ProvenanceError(
                    f"spatial_resolution must be (x_size, y_size); got {res!r}"
                )
            if not all(v > 0 for v in res):
                raise ProvenanceError(
                    f"spatial_resolution must be positive lengths; got {res!r}. "
                    "a negative y step belongs in the affine transform, not in "
                    "the recorded resolution."
                )
            object.__setattr__(self, "spatial_resolution", res)

        if self.data_class is DataClass.DERIVED and not self.parents:
            raise ProvenanceError(
                f"layer {self.layer_name!r} is DERIVED but names no parents; a "
                "derived layer must say what it was derived from "
                "(docs/GLOSSARY.md)."
            )

    # -- provenance-preserving updates ------------------------------------- #
    def with_transformation(self, transformation: Transformation) -> ProvenanceRecord:
        """Return a copy with one more transformation appended.

        Provenance is append-only: an operation never rewrites the history of
        the layer it consumed.
        """
        return replace(
            self, transformations=self.transformations + (transformation,)
        )

    def with_updates(self, **changes: Any) -> ProvenanceRecord:
        """Return a copy with the given fields replaced (validated again)."""
        unknown = set(changes) - set(self.__dataclass_fields__)
        if unknown:
            raise ProvenanceError(f"unknown provenance fields {sorted(unknown)}")
        return replace(self, **changes)

    def derive(
        self,
        layer_name: str,
        transformation: Transformation,
        **changes: Any,
    ) -> ProvenanceRecord:
        """Build the provenance of a layer derived from this one.

        The child is :attr:`DataClass.DERIVED`, names this layer as a parent,
        inherits this layer's sources and transformation history, and appends
        ``transformation``. Inheriting the history is what makes a chain such as
        *fetch -> reproject -> clip -> slope* readable end-to-end from the final
        artifact alone.

        A synthetic parent yields a synthetic child: test data must not be able
        to launder itself into ``DERIVED`` and thereby look like real data with
        a processing history.
        """
        child_class = (
            DataClass.SYNTHETIC
            if self.data_class is DataClass.SYNTHETIC
            else DataClass.DERIVED
        )
        payload: dict[str, Any] = {
            "layer_name": layer_name,
            "data_class": child_class,
            "temporal_class": self.temporal_class,
            "temporal_reference": self.temporal_reference,
            "sources": self.sources,
            "transformations": self.transformations + (transformation,),
            "original_crs": self.original_crs,
            "output_crs": self.output_crs,
            "spatial_resolution": self.spatial_resolution,
            "resolution_unit": self.resolution_unit,
            "value_unit": self.value_unit,
            "vertical_datum": self.vertical_datum,
            "nodata_representation": self.nodata_representation,
            "checksum": UNKNOWN,
            "parents": tuple(self.parents) + (self.layer_name,)
            if self.layer_name not in self.parents
            else tuple(self.parents),
            "random_seed": self.random_seed,
            "notes": self.notes,
        }
        payload.update(changes)
        return ProvenanceRecord(**payload)

    # -- introspection ------------------------------------------------------ #
    def unknown_fields(self) -> list[str]:
        """Names of fields whose value is the literal ``UNKNOWN``.

        Reported as WARNINGs by ``wg-data validate-study-area``: incompleteness
        is a visible metric, not a silence (D-0009).
        """
        out: list[str] = []
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, str) and value == UNKNOWN:
                out.append(name)
        for index, source in enumerate(self.sources):
            for sname, svalue in source.to_dict().items():
                if svalue == UNKNOWN:
                    out.append(f"sources[{index}].{sname}")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "layer_name": self.layer_name,
            "data_class": self.data_class.value,
            "temporal_class": self.temporal_class.value,
            "temporal_reference": self.temporal_reference,
            "sources": [s.to_dict() for s in self.sources],
            "transformations": [t.to_dict() for t in self.transformations],
            "original_crs": self.original_crs,
            "output_crs": self.output_crs,
            "spatial_resolution": list(self.spatial_resolution)
            if self.spatial_resolution is not None
            else None,
            "resolution_unit": self.resolution_unit,
            "value_unit": self.value_unit,
            "vertical_datum": self.vertical_datum,
            "nodata_representation": self.nodata_representation,
            "checksum": self.checksum,
            "checksum_algorithm": self.checksum_algorithm,
            "parents": list(self.parents),
            "random_seed": self.random_seed,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProvenanceRecord:
        data = dict(payload)
        version = data.pop("schema_version", None)
        if version is not None and version != PROVENANCE_SCHEMA_VERSION:
            raise ProvenanceError(
                f"provenance schema_version {version!r} != supported "
                f"{PROVENANCE_SCHEMA_VERSION!r}; refusing to guess the layout "
                "of an unrecognised version (docs/INTERFACES.md)."
            )
        data["sources"] = tuple(
            SourceRecord.from_dict(s) for s in data.get("sources", [])
        )
        data["transformations"] = tuple(
            Transformation.from_dict(t) for t in data.get("transformations", [])
        )
        res = data.get("spatial_resolution")
        data["spatial_resolution"] = tuple(res) if res is not None else None
        data["parents"] = tuple(data.get("parents", []))
        unexpected = set(data) - set(cls.__dataclass_fields__)
        if unexpected:
            raise ProvenanceError(
                f"unexpected provenance fields {sorted(unexpected)}; refusing to "
                "silently drop provenance content."
            )
        return cls(**data)


def _jsonable(value: Any) -> Any:
    """Coerce parameter values into JSON-safe form without losing information."""
    import math

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        # JSON has no NaN/Infinity; emit the string form rather than null, which
        # would read as "absent".
        return repr(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "item") and hasattr(value, "dtype"):  # NumPy scalar
        return _jsonable(value.item())
    if hasattr(value, "tolist"):  # NumPy array
        return _jsonable(value.tolist())
    return str(value)


def require_provenance(record: Any, context: str = "") -> ProvenanceRecord:
    """Assert that ``record`` is a real provenance record."""
    if not isinstance(record, ProvenanceRecord):
        where = f" while {context}" if context else ""
        raise ProvenanceError(
            f"expected a ProvenanceRecord{where}, got {type(record).__name__}. "
            "every layer this package emits carries provenance (AGENTS.md §7)."
        )
    return record
