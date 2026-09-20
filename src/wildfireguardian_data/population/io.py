"""Population layer ingestion, with a load-time privacy guard.

The guard (``docs/DECISIONS.md`` D-0011) rejects attribute names that look
person-level or medical **before** the data enters the package. It is a
tripwire, not a security boundary: renaming a column defeats it
(``docs/FAILURE_MODES.md`` F-POP-1). Its job is to make the prohibited path fail
loudly for whoever tries it -- including a future agent following an instruction
that seemed reasonable in isolation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from shapely.geometry import Point

from ..crs import crs_to_string
from ..errors import IngestError, PrivacyGuardError
from ..provenance.checksum import sha256_file
from ..provenance.models import (
    NOT_APPLICABLE,
    UNKNOWN,
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
    Transformation,
)
from ..vector import VectorLayer, read_geojson
from .models import AgeStrataSet, AgeStratum, SettlementCentroidKind, VillagePopulation

__all__ = [
    "PRIVACY_FORBIDDEN_FIELD_PATTERNS",
    "DEFAULT_MIN_AGGREGATE_COUNT",
    "check_privacy",
    "load_population_layer",
    "villages_from_layer",
]

#: Attribute-name patterns that indicate person-level or medical content.
#: Matched case-insensitively against the *whole* normalised field name and
#: against its parts, in both English and romanised Korean where a term is
#: routinely used in Korean administrative data.
#:
#: Aggregate demographic fields (``pop_total``, ``age_65_plus``,
#: ``households_count``) are deliberately **not** here: they are the legitimate
#: content of this module (A-POP-2). It is the individual and the diagnosis that
#: are refused, not the demography.
PRIVACY_FORBIDDEN_FIELD_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"resident.?registration", "resident registration number (주민등록번호)"),
    (r"\bjumin\b", "resident registration number (romanised)"),
    (r"\brrn\b", "resident registration number"),
    (r"national.?id", "national identity number"),
    (r"(^|_)(person|patient|resident|occupant)_(name|id|no|number)($|_)", "person-level identifier"),
    (r"(^|_)(first|last|full|given|family)_?name($|_)", "personal name"),
    (r"\bdiagnos", "medical diagnosis"),
    (r"\bdisease\b", "medical condition"),
    (r"\bicd_?1?0?\b", "medical diagnosis code"),
    (r"\bmedicat", "medication"),
    (r"prescri", "prescription"),
    (r"\bdisabilit(y|ies)_(grade|level|type|code)\b", "individual disability classification"),
    (r"care.?grade", "long-term care grade (장기요양등급)"),
    (r"\bltci\b", "long-term care insurance grade"),
    (r"(^|_)mobility_(status|grade|level|score)($|_)", "individual mobility assessment"),
    (r"\bbedridden\b", "individual care status"),
    (r"\bwheelchair_user\b", "individual care status"),
    (r"(^|_)(phone|mobile|tel|telephone|email)($|_)", "personal contact detail"),
    (r"(^|_)(address|addr|street_address|dwelling_id|house_?hold_?id)($|_)", "dwelling-level identifier"),
    (r"\bnhis\b", "national health insurance record"),
    (r"\bmedical_?record", "medical record"),
)

#: Aggregate counts below this are flagged as a WARNING, not suppressed
#: (D-0011): a genuinely 3-person Korean hamlet is a real study object, and
#: deleting it would falsify the landscape.
DEFAULT_MIN_AGGREGATE_COUNT = 5


def _normalise(field_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(field_name).strip().lower())


def check_privacy(
    field_names: Iterable[str], *, context: str = "population layer"
) -> None:
    """Raise :class:`PrivacyGuardError` if any field name looks person-level.

    Checks names only. It cannot inspect values, and it cannot detect a
    prohibited field renamed to ``notes`` -- which is why the prohibition is
    also stated in ``AGENTS.md`` and ``docs/ASSUMPTIONS.md``, and why this is
    documented as a tripwire.
    """
    offences: list[str] = []
    for name in field_names:
        normalised = _normalise(name)
        for pattern, description in PRIVACY_FORBIDDEN_FIELD_PATTERNS:
            if re.search(pattern, normalised):
                offences.append(f"{name!r} looks like {description}")
                break
    if offences:
        raise PrivacyGuardError(
            f"refusing to load {context}: "
            + "; ".join(offences)
            + ". this repository holds aggregate settlement-level population "
            "only, and no medical or person-level information "
            "(docs/ASSUMPTIONS.md A-POP-1/2, docs/DECISIONS.md D-0011). "
            "aggregate age strata such as 'age_65_plus' are permitted; "
            "individual records are not. if the field is genuinely aggregate "
            "and the name is misleading, rename it at the source -- do not "
            "bypass this guard."
        )


def load_population_layer(
    path: str | Path,
    *,
    name: str,
    source: SourceRecord,
    data_class: DataClass,
    temporal_class: TemporalProvenance,
    crs: Any = None,
    temporal_reference: str = UNKNOWN,
    aggregation_level: str = UNKNOWN,
    notes: str = "",
) -> VectorLayer:
    """Load an aggregate population layer from GeoJSON, privacy-checked.

    ``aggregation_level`` (for example ``"village"``, ``"ri"``, ``"eup_myeon"``)
    is recorded in provenance. It is ``"UNKNOWN"`` when the caller does not know
    -- which validation reports, because an unknown aggregation level means a
    downstream consumer cannot tell whether a count is disclosive.
    """
    source_path = Path(path)
    provenance = ProvenanceRecord(
        layer_name=name,
        data_class=DataClass(data_class),
        temporal_class=TemporalProvenance(temporal_class),
        temporal_reference=temporal_reference,
        sources=(source,),
        transformations=(
            Transformation(
                operation="load_population_layer",
                parameters={
                    "path": str(source_path),
                    "declared_crs": crs_to_string(crs),
                    "aggregation_level": aggregation_level,
                    "privacy_guard": "field-name tripwire applied at load time",
                },
                notes=(
                    "aggregate settlement-level data only; person-level and "
                    "medical fields are rejected (docs/DECISIONS.md D-0011)"
                ),
            ),
        ),
        original_crs=crs_to_string(crs) if crs is not None else UNKNOWN,
        output_crs=crs_to_string(crs) if crs is not None else UNKNOWN,
        value_unit="count",
        # A vector layer has no cell size, no vertical datum and no nodata
        # convention: those facts do not exist rather than being unknown, and
        # recording UNKNOWN for them would dilute the incompleteness metric
        # D-0009 exists to keep meaningful. The OSM fetcher already did this;
        # the local-file loaders did not.
        resolution_unit=NOT_APPLICABLE,
        vertical_datum=NOT_APPLICABLE,
        nodata_representation=NOT_APPLICABLE,
        surface_model=NOT_APPLICABLE,
        checksum=sha256_file(source_path) if source_path.exists() else UNKNOWN,
        notes=(f"aggregation_level={aggregation_level}" + (f" | {notes}" if notes else "")),
    )
    layer = read_geojson(
        source_path,
        name=name,
        provenance=provenance,
        crs=crs,
        feature_kind="settlement",
    )
    check_privacy(layer.property_keys, context=f"population layer {name!r} from {source_path}")
    return VectorLayer(
        name=layer.name,
        features=layer.features,
        crs=layer.crs,
        provenance=provenance.with_updates(
            original_crs=crs_to_string(layer.crs), output_crs=crs_to_string(layer.crs)
        ),
        feature_kind=layer.feature_kind,
    )


def villages_from_layer(
    layer: VectorLayer,
    *,
    id_property: str = "settlement_id",
    name_property: str = "name",
    total_property: str = "population_total",
    age_stratum_properties: Sequence[tuple[str, int, int | None]] = (),
    count_basis_property: str = "count_basis",
    reference_date_property: str = "reference_date",
) -> tuple[VillagePopulation, ...]:
    """Build :class:`VillagePopulation` records from a vector layer.

    A missing ``total_property`` becomes ``None``, not ``0`` (A-POP-5). A
    missing ``id_property`` is an error: an unidentified settlement cannot be
    referred to, and inventing an index for it would create an identifier that
    changes whenever the file is reordered.
    """
    check_privacy(layer.property_keys, context=f"population layer {layer.name!r}")
    villages: list[VillagePopulation] = []
    for index, feature in enumerate(layer.features):
        properties = feature.properties
        raw_id = properties.get(id_property)
        if raw_id is None or not str(raw_id).strip():
            raise IngestError(
                f"feature {index} of layer {layer.name!r} has no {id_property!r}. "
                "settlements need stable identifiers; a positional index would "
                "change if the file were reordered."
            )
        strata = []
        for property_name, lower, upper in age_stratum_properties:
            value = properties.get(property_name)
            strata.append(
                AgeStratum(
                    lower=lower,
                    upper=upper,
                    count=None if value is None else int(value),
                )
            )
        total = properties.get(total_property)
        villages.append(
            VillagePopulation(
                settlement_id=str(raw_id),
                name=str(properties.get(name_property, UNKNOWN)),
                geometry=feature.geometry,
                centroid=(
                    feature.geometry
                    if isinstance(feature.geometry, Point)
                    else None
                ),
                centroid_kind=(
                    SettlementCentroidKind.SOURCE_PROVIDED
                    if isinstance(feature.geometry, Point)
                    else SettlementCentroidKind.UNKNOWN
                ),
                population_total=None if total is None else int(total),
                age_strata=AgeStrataSet(tuple(strata)),
                count_basis=str(properties.get(count_basis_property, UNKNOWN)),
                reference_date=str(properties.get(reference_date_property, UNKNOWN)),
                attributes={
                    k: v
                    for k, v in properties.items()
                    if k
                    not in {
                        id_property,
                        name_property,
                        total_property,
                        count_basis_property,
                        reference_date_property,
                    }
                    and k not in {p for p, _, _ in age_stratum_properties}
                },
            )
        )
    return tuple(villages)
