"""Provenance: source, dates, transformations, CRS, resolution, checksum.

See ``docs/DATA_PROVENANCE.md`` for what is recorded and why, and
``docs/DECISIONS.md`` D-0009 for the ``UNKNOWN`` rule.
"""

from __future__ import annotations

from .checksum import (
    CHECKSUM_ALGORITHM,
    sha256_array,
    sha256_bytes,
    sha256_file,
    sha256_json,
)
from .models import (
    NOT_APPLICABLE,
    PROVENANCE_SCHEMA_VERSION,
    UNKNOWN,
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
    Transformation,
    require_provenance,
    utc_now_iso,
    validate_temporal_string,
)
from .store import (
    provenance_path,
    read_all_provenance,
    read_provenance,
    write_provenance,
)

__all__ = [
    "UNKNOWN",
    "NOT_APPLICABLE",
    "PROVENANCE_SCHEMA_VERSION",
    "DataClass",
    "TemporalProvenance",
    "SourceRecord",
    "Transformation",
    "ProvenanceRecord",
    "require_provenance",
    "utc_now_iso",
    "validate_temporal_string",
    "CHECKSUM_ALGORITHM",
    "sha256_file",
    "sha256_bytes",
    "sha256_array",
    "sha256_json",
    "provenance_path",
    "write_provenance",
    "read_provenance",
    "read_all_provenance",
]
