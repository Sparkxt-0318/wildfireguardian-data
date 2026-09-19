"""Study-area assembly: config, build pipeline, bundle, serialisation, summary.

``StudyAreaBundle`` and the on-disk layout are **internal and unstable**
(``docs/DECISIONS.md`` D-0001, ``docs/INTERFACES.md``).
"""

from __future__ import annotations

from .build import KNOWN_FUEL_SCHEMES, build_study_area
from .bundle import (
    BUNDLE_SCHEMA_VERSION,
    FacilitiesComponent,
    FuelsComponent,
    PopulationComponent,
    RoadsComponent,
    StudyAreaBundle,
    TerrainComponent,
)
from .config import (
    FacilitiesConfig,
    FuelsConfig,
    PopulationConfig,
    RoadsConfig,
    SourceSpec,
    StudyAreaConfig,
    TerrainConfig,
)
from .serialize import MANIFEST_NAME, read_bundle, write_bundle
from .summary import format_summary_text, summarize_bundle

__all__ = [
    "StudyAreaBundle",
    "BUNDLE_SCHEMA_VERSION",
    "TerrainComponent",
    "RoadsComponent",
    "FuelsComponent",
    "PopulationComponent",
    "FacilitiesComponent",
    "StudyAreaConfig",
    "SourceSpec",
    "TerrainConfig",
    "RoadsConfig",
    "FuelsConfig",
    "PopulationConfig",
    "FacilitiesConfig",
    "build_study_area",
    "KNOWN_FUEL_SCHEMES",
    "write_bundle",
    "read_bundle",
    "MANIFEST_NAME",
    "summarize_bundle",
    "format_summary_text",
]
