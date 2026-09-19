"""Human- and machine-readable study-area summaries.

The summary is where a reader finds out what the bundle actually contains,
including its **gaps**: missing-cell counts, ``UNKNOWN`` provenance fields, and
the caveats that travel with the data (``docs/DECISIONS.md`` D-0009).
"""

from __future__ import annotations

from typing import Any

from ..provenance.models import UNKNOWN
from ..raster import RasterKind
from ..terrain.stats import categorical_statistics, raster_statistics
from .bundle import StudyAreaBundle

__all__ = ["summarize_bundle", "format_summary_text"]


def _record_field(record: Any, name: str, default: Any = None) -> Any:
    """Read a field from a dataclass record or from its serialised dict.

    Records are objects in a freshly built bundle and dicts in one read back
    from disk; a summary must not differ between the two.
    """
    if isinstance(record, dict):
        return record.get(name, default)
    value = getattr(record, name, default)
    return getattr(value, "value", value)


def _record_dict(record: Any) -> dict[str, Any]:
    return dict(record) if isinstance(record, dict) else record.to_dict()


def summarize_bundle(bundle: StudyAreaBundle) -> dict[str, Any]:
    """A JSON-safe summary of a bundle."""
    summary: dict[str, Any] = bundle.describe()

    provenance_summary = []
    for name, record in sorted(bundle.provenance.items()):
        unknowns = record.unknown_fields()
        provenance_summary.append(
            {
                "layer": name,
                "data_class": record.data_class.value,
                "temporal_class": record.temporal_class.value,
                "temporal_reference": record.temporal_reference,
                "sources": [source.name for source in record.sources],
                "licences": sorted(
                    {source.licence for source in record.sources if source.licence != UNKNOWN}
                ),
                "transformations": [t.operation for t in record.transformations],
                "output_crs": record.output_crs,
                "spatial_resolution": list(record.spatial_resolution)
                if record.spatial_resolution
                else None,
                "value_unit": record.value_unit,
                "unknown_field_count": len(unknowns),
                "unknown_fields": unknowns,
                "checksum": record.checksum,
            }
        )
    summary["provenance"] = provenance_summary
    summary["unknown_field_total"] = sum(
        entry["unknown_field_count"] for entry in provenance_summary
    )

    statistics: dict[str, Any] = {}
    for layer in bundle.raster_layers():
        statistics[layer.name] = (
            categorical_statistics(layer)
            if layer.kind is RasterKind.CATEGORICAL
            else raster_statistics(layer)
        )
    summary["raster_statistics"] = statistics

    if bundle.terrain is not None and bundle.terrain.statistics:
        summary["terrain_statistics"] = bundle.terrain.statistics

    if bundle.roads is not None and bundle.roads.qa is not None:
        # In a freshly built bundle the QA is a RoadNetworkQA; in one read back
        # from disk it is the dict it was written as. Both are accepted so that
        # summarising in memory and summarising from disk agree.
        qa = (
            bundle.roads.qa.to_dict()
            if hasattr(bundle.roads.qa, "to_dict")
            else dict(bundle.roads.qa)
        )
        summary["roads_qa"] = qa
        summary["roads_qa_headline"] = {
            "nodes": qa["graph"]["nodes"],
            "edges": qa["graph"]["edges"],
            "total_length_m": qa["graph"]["total_length_m"],
            "components": qa["connectivity"]["component_count"],
            "dead_ends": qa["connectivity"]["dead_end_count"],
            "isolated_segments": len(qa["connectivity"]["isolated_segments"]),
            "exit_nodes": qa["egress"]["exits"]["count"],
            "single_egress_candidates": len(qa["egress"]["single_egress_candidates"]),
            "no_egress_components": len(qa["egress"]["no_egress_components"]),
            "articulation_points": len(qa["fragility"]["articulation_points"]),
            "bridge_edges": len(qa["fragility"]["bridge_edges"]),
            "critical_links": len(qa["fragility"]["critical_links"]),
            "crossings_without_node": qa["crossings_without_node"]["count"],
        }

    if bundle.population is not None and bundle.population.villages:
        villages = bundle.population.villages
        totals = [
            _record_field(v, "population_total")
            for v in villages
            if _record_field(v, "population_total") is not None
        ]
        summary["population"] = {
            "settlements": len(villages),
            "population_total_sum": sum(totals) if totals else None,
            "settlements_without_total": sum(
                1 for v in villages if _record_field(v, "population_total") is None
            ),
            "settlements_with_strata_mismatch": sum(
                1
                for v in villages
                if _record_field(v, "strata_total_matches_total") is False
            ),
            "records": [_record_dict(v) for v in villages],
            "note": (
                "aggregate only; a missing total is None, never 0 "
                "(docs/ASSUMPTIONS.md A-POP-5)"
            ),
        }

    if bundle.facilities is not None and bundle.facilities.records:
        records = bundle.facilities.records
        by_kind: dict[str, int] = {}
        for record in records:
            kind = _record_field(record, "kind")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        summary["facilities"] = {
            "count": len(records),
            "by_kind": dict(sorted(by_kind.items())),
            "with_sourced_capacity": sum(
                1 for r in records if _record_field(r, "capacity_persons") is not None
            ),
            "suitability_assessed": sum(
                1 for r in records if _record_field(r, "suitability_assessed", False)
            ),
            "records": [_record_dict(r) for r in records],
            "note": (
                "presence is not fitness for purpose; this repository assesses no "
                "facility (docs/ASSUMPTIONS.md A-FAC-1)"
            ),
        }

    if bundle.fuels is not None and bundle.fuels.scheme is not None:
        summary["fuel_scheme"] = bundle.fuels.scheme.to_dict()

    return summary


def format_summary_text(summary: dict[str, Any]) -> str:
    """Render a summary as plain text for a terminal."""
    lines: list[str] = []
    lines.append(f"study area : {summary['study_area_id']}")
    lines.append(f"CRS        : {summary['crs']}")
    bounds = summary["bounds"]
    lines.append(
        f"bounds     : ({bounds['min_x']:.1f}, {bounds['min_y']:.1f}) - "
        f"({bounds['max_x']:.1f}, {bounds['max_y']:.1f})  "
        f"[{summary['extent_m'][0]:.0f} x {summary['extent_m'][1]:.0f} m]"
    )
    lines.append(f"components : {', '.join(summary['components_present'])}")
    lines.append("")
    lines.append("layers:")
    for layer in summary["layers"]:
        if "shape" in layer:
            lines.append(
                f"  {layer['name']:<14} raster {layer['shape'][0]}x{layer['shape'][1]} "
                f"@ {layer['resolution'][0]:g}m  unit={layer['value_unit']}  "
                f"missing={layer['missing_cells']}/{layer['valid_cells'] + layer['missing_cells']}  "
                f"[{layer['data_class']}/{layer['temporal_class']}]"
            )
        else:
            lines.append(
                f"  {layer['name']:<14} vector {layer['features']} features "
                f"{layer['geom_types']}  [{layer['data_class']}/{layer['temporal_class']}]"
            )

    terrain = summary.get("terrain_statistics")
    if terrain:
        elevation = terrain.get("elevation", {})
        if elevation.get("min") is not None:
            lines.append("")
            lines.append("terrain:")
            lines.append(
                f"  elevation  : {elevation['min']:.1f} - {elevation['max']:.1f} m "
                f"(mean {elevation['mean']:.1f}, relief {terrain.get('relief_m', 0):.1f})"
            )
        slope = terrain.get("slope")
        if slope and slope.get("mean") is not None:
            lines.append(
                f"  slope      : mean {slope['mean']:.2f} deg, max {slope['max']:.2f} deg "
                f"({slope['cells_missing']} cells not computable)"
            )
        aspect = terrain.get("aspect")
        if aspect:
            circular = aspect["circular_mean"]
            mean = circular.get("mean_deg")
            lines.append(
                "  aspect     : circular mean "
                + (f"{mean:.1f} deg" if mean is not None else "n/a")
                + f" (resultant {circular['resultant_length']:.3f}; "
                f"{circular['count_ignored']} cells ignored)"
            )

    headline = summary.get("roads_qa_headline")
    if headline:
        lines.append("")
        lines.append("roads (topology QA only - no safety claim):")
        lines.append(
            f"  {headline['nodes']} nodes, {headline['edges']} edges, "
            f"{headline['total_length_m'] / 1000:.2f} km, "
            f"{headline['components']} component(s)"
        )
        lines.append(
            f"  dead ends {headline['dead_ends']}, isolated segments "
            f"{headline['isolated_segments']}, exit nodes {headline['exit_nodes']}"
        )
        lines.append(
            f"  single-egress candidates {headline['single_egress_candidates']}, "
            f"no-egress components {headline['no_egress_components']}"
        )
        lines.append(
            f"  articulation points {headline['articulation_points']}, bridges "
            f"{headline['bridge_edges']}, critical links {headline['critical_links']}, "
            f"unnoded crossings {headline['crossings_without_node']}"
        )

    population = summary.get("population")
    if population:
        lines.append("")
        lines.append("population (aggregate):")
        lines.append(
            f"  {population['settlements']} settlement(s), total "
            f"{population['population_total_sum']}, "
            f"{population['settlements_without_total']} without a total, "
            f"{population['settlements_with_strata_mismatch']} with strata mismatch"
        )

    facilities = summary.get("facilities")
    if facilities:
        lines.append("")
        lines.append("facilities (source-declared roles, unassessed):")
        lines.append(
            "  "
            + ", ".join(f"{kind}={count}" for kind, count in facilities["by_kind"].items())
        )
        lines.append(
            f"  sourced capacity {facilities['with_sourced_capacity']}/"
            f"{facilities['count']}, suitability assessed "
            f"{facilities['suitability_assessed']}/{facilities['count']}"
        )

    lines.append("")
    lines.append(f"provenance : {summary['unknown_field_total']} UNKNOWN field(s) total")
    for entry in summary["provenance"]:
        licences = ", ".join(entry["licences"]) if entry["licences"] else "UNKNOWN"
        lines.append(
            f"  {entry['layer']:<14} {entry['data_class']}/{entry['temporal_class']}"
            f"  unknowns={entry['unknown_field_count']}  licence={licences}"
        )
        lines.append(f"    transformations: {' -> '.join(entry['transformations'])}")

    lines.append("")
    for caveat in summary["caveats"]:
        lines.append(f"! {caveat}")
    return "\n".join(lines)
