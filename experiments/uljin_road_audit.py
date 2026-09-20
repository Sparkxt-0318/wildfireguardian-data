"""Audit the Uljin real-bundle road graph: why 31 segments and 22 components?

Answers the thirteen questions in `reports/ULJIN_ROAD_AUDIT.md` from the
committed bundle and the cached OSM extract, and writes the report's tables plus
a component map and CSV. Needs no network.

Run:  python experiments/uljin_road_audit.py
"""

from __future__ import annotations

import collections
import csv
import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from wildfireguardian_data.provenance import (
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
)
from wildfireguardian_data.roads import assess_road_network, build_road_graph
from wildfireguardian_data.study_area import read_bundle
from wildfireguardian_data.vector import read_geojson

BUNDLE = Path("data/study_areas/uljin_real_v1")
OSM_XML = Path("data/raw/uljin_real_v1_roads_osm.xml")
REPORTS = Path("reports")

#: The filter the bundle was built with.
REQUESTED_CLASSES = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "service", "track", "road",
    "motorway_link", "trunk_link", "primary_link", "secondary_link",
    "tertiary_link",
}


def load_roads_layer():
    """Read the committed road layer, so the audit inspects what was shipped."""
    bundle = read_bundle(BUNDLE)
    return bundle.roads.layer, bundle.bounds


def osm_facts() -> dict:
    """Facts recoverable from the cached extract alone."""
    if not OSM_XML.exists():
        return {"available": False}
    root = ElementTree.parse(OSM_XML).getroot()
    ways = root.findall("way")
    tagged = []
    classes = collections.Counter()
    for way in ways:
        tags = {t.get("k"): t.get("v") for t in way.findall("tag")}
        tagged.append((way, tags))
        if "highway" in tags:
            classes[tags["highway"]] += 1

    kept = [(w, t) for w, t in tagged if t.get("highway") in REQUESTED_CLASSES]
    # Shared OSM node IDs between kept ways, and whether they are interior.
    refs = {w.get("id"): [nd.get("ref") for nd in w.findall("nd")] for w, _ in kept}
    owner = collections.defaultdict(list)
    for way_id, node_ids in refs.items():
        for position, node_id in enumerate(node_ids):
            owner[node_id].append((way_id, position, len(node_ids)))
    shared = {n: v for n, v in owner.items() if len({w for w, _, _ in v}) > 1}
    endpoint_only = sum(
        1 for v in shared.values() if all(p == 0 or p == n - 1 for _, p, n in v)
    )

    attributes = {}
    for key in (
        "bridge", "tunnel", "layer", "oneway", "lanes", "surface", "maxspeed",
        "access", "width", "service", "tracktype",
    ):
        present = collections.Counter(t[key] for _, t in kept if key in t)
        attributes[key] = dict(present)

    return {
        "available": True,
        "ways_total": len(ways),
        "highway_classes_present": dict(classes),
        "ways_matching_filter": len(kept),
        "ways_without_highway_tag": len(ways) - sum(classes.values()),
        "classes_present_but_excluded": {
            k: v for k, v in classes.items() if k not in REQUESTED_CLASSES
        },
        "shared_osm_nodes": len(shared),
        "shared_endpoint_only": endpoint_only,
        "shared_involving_interior": len(shared) - endpoint_only,
        "ways_with_interior_vertices": sum(1 for r in refs.values() if len(r) > 2),
        "max_vertices_in_a_way": max((len(r) for r in refs.values()), default=0),
        "attributes": attributes,
    }


def graph_facts(layer, bounds, *, node_shared_vertices: bool) -> dict:
    graph = build_road_graph(layer, node_shared_vertices=node_shared_vertices)
    qa = assess_road_network(graph, study_bounds=bounds, settlements_crs=bounds.crs)
    payload = qa.to_dict()
    sizes = sorted(
        (c["edge_count"] for c in payload["connectivity"]["components"]), reverse=True
    )
    total_edges = payload["graph"]["edges"]
    return {
        "nodes": payload["graph"]["nodes"],
        "edges": total_edges,
        "total_length_km": round(payload["graph"]["total_length_m"] / 1000.0, 3),
        "components": payload["connectivity"]["component_count"],
        "component_edge_counts": sizes,
        "single_edge_components": sum(1 for s in sizes if s == 1),
        "largest_component_edge_fraction": round(sizes[0] / total_edges, 4) if total_edges else None,
        "largest_component_length_fraction": round(
            max(c["total_length_m"] for c in payload["connectivity"]["components"])
            / payload["graph"]["total_length_m"],
            4,
        ),
        "dead_ends": payload["connectivity"]["dead_end_count"],
        "isolated_segments": len(payload["connectivity"]["isolated_segments"]),
        "self_loops": len(payload["connectivity"]["self_loop_edges"]),
        "parallel_edge_pairs": len(payload["connectivity"]["parallel_edge_pairs"]),
        "crossings_without_node": payload["crossings_without_node"]["count"],
        "crossing_samples": payload["crossings_without_node"]["samples"],
        "exit_nodes": payload["egress"]["exits"]["count"],
        "exits_from_boundary_crossing": len(
            payload["egress"]["exits"]["from_boundary_crossing"]
        ),
        "exits_from_node_proximity": len(payload["egress"]["exits"]["from_boundary"]),
        "boundary_crossing_edges": payload["egress"]["exits"]["boundary_crossing_edge_count"],
        "shared_vertex_splits": graph.shared_vertex_splits,
        "shared_vertices_found": graph.shared_vertices_found,
        "_graph": graph,
        "_qa": payload,
    }


def write_component_csv(facts: dict, path: Path) -> None:
    components = facts["_qa"]["connectivity"]["components"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "component_id", "node_count", "edge_count", "total_length_m",
                "exit_node_count", "is_single_edge",
            ],
        )
        writer.writeheader()
        for component in components:
            writer.writerow(
                {
                    "component_id": component["component_id"],
                    "node_count": component["node_count"],
                    "edge_count": component["edge_count"],
                    "total_length_m": round(component["total_length_m"], 1),
                    "exit_node_count": len(component["exit_nodes"]),
                    "is_single_edge": component["edge_count"] == 1,
                }
            )


def write_component_map(facts: dict, bounds, path: Path, title: str) -> None:
    """One SVG per graph, every component in its own colour.

    Deliberately plain SVG with no plotting dependency: the point is that a
    reviewer can open it and count the fragments.
    """
    import networkx as nx

    graph = facts["_graph"]
    components = list(nx.connected_components(graph.graph))
    components.sort(key=len, reverse=True)
    colour_of = {}
    palette = [
        "#2b6cb0", "#c53030", "#2f855a", "#b7791f", "#6b46c1", "#0987a0",
        "#97266d", "#4a5568", "#dd6b20", "#319795",
    ]
    for index, nodes in enumerate(components):
        for node in nodes:
            colour_of[node] = palette[index % len(palette)]

    width = height = 900
    pad = 40
    span_x = bounds.width or 1.0
    span_y = bounds.height or 1.0

    def project(x: float, y: float) -> tuple[float, float]:
        px = pad + (x - bounds.min_x) / span_x * (width - 2 * pad)
        py = height - pad - (y - bounds.min_y) / span_y * (height - 2 * pad)
        return px, py

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{pad}" y="{pad - 14}" font-family="sans-serif" font-size="16" '
        f'fill="#1a202c">{title}</text>',
    ]
    # study-area boundary
    x0, y0 = project(bounds.min_x, bounds.max_y)
    x1, y1 = project(bounds.max_x, bounds.min_y)
    parts.append(
        f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" '
        'fill="none" stroke="#cbd5e0" stroke-dasharray="6 4"/>'
    )
    for u, v, data in graph.graph.edges(data=True):
        geometry = data.get("geometry")
        if geometry is None:
            continue
        points = " ".join(f"{px:.1f},{py:.1f}" for px, py in (project(x, y) for x, y in geometry.coords))
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{colour_of.get(u, "#000")}" '
            'stroke-width="2.2" stroke-linecap="round"/>'
        )
    for node, point in graph.node_points.items():
        px, py = project(point.x, point.y)
        parts.append(
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.6" fill="{colour_of.get(node, "#000")}" '
            'fill-opacity="0.85"/>'
        )
    parts.append(
        f'<text x="{pad}" y="{height - 14}" font-family="sans-serif" font-size="13" '
        f'fill="#4a5568">{len(components)} connected component(s), '
        f'{graph.edge_count} edges, {graph.node_count} nodes - one colour per component. '
        'Dashed box: study area.</text>'
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    layer, bounds = load_roads_layer()
    osm = osm_facts()
    endpoint_only = graph_facts(layer, bounds, node_shared_vertices=False)
    shared_vertex = graph_facts(layer, bounds, node_shared_vertices=True)

    write_component_csv(endpoint_only, REPORTS / "uljin_road_components_endpoint_only.csv")
    write_component_csv(shared_vertex, REPORTS / "uljin_road_components_shared_vertex.csv")
    write_component_map(
        endpoint_only,
        bounds,
        REPORTS / "uljin_road_components_endpoint_only.svg",
        "Uljin roads - endpoint-only connection (Phase 1 behaviour)",
    )
    write_component_map(
        shared_vertex,
        bounds,
        REPORTS / "uljin_road_components_shared_vertex.svg",
        "Uljin roads - shared-vertex noding (D-0020)",
    )

    summary = {
        "osm_extract": osm,
        "endpoint_only": {k: v for k, v in endpoint_only.items() if not k.startswith("_")},
        "shared_vertex": {k: v for k, v in shared_vertex.items() if not k.startswith("_")},
    }
    (REPORTS / "uljin_road_audit_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
