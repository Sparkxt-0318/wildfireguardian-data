"""Road-network topology QA.

Every number here is a property of the **graph as digitised**. None of it is a
statement about whether a road can be driven, whether a community can get out,
or whether anyone is safe (``docs/SCOPE.md``, ``docs/DECISIONS.md`` D-0008).
The report carries that disclaimer in-band, in the serialised output, so it
travels with the data rather than living only in this docstring.

What is computed, and why each is exact rather than approximate:

* **connected components** -- ``networkx`` on the multigraph.
* **isolated segments** -- an edge alone in its component, with no exit.
* **node degree** -- multigraph degree, so parallel edges count twice and a
  self-loop counts twice (stated in the report).
* **dead ends** -- degree-1 nodes that are not exits.
* **articulation points** -- vertex cuts; unaffected by parallel edges, so the
  simple graph gives the exact answer.
* **bridge edges** -- edge cuts. Computed on the simple graph and then filtered
  by multiplicity: an edge is a bridge in a multigraph **iff** it is a bridge in
  the simple graph *and* has exactly one parallel edge. Skipping that filter
  would report a duplicated road as critical.
* **critical links** -- edges whose removal disconnects a settlement node from
  every exit node. Only a bridge can disconnect anything, so the search is
  restricted to bridges; that is an exactness argument, not an optimisation.
* **single-egress candidates** -- components with at least one settlement node
  and exactly one exit node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import networkx as nx
from shapely.geometry import LineString, Point
from shapely.geometry import box as shapely_box
from shapely.strtree import STRtree

from ..bounds import Bounds
from ..crs import crs_to_string, require_same_crs
from ..errors import GraphError
from .graph import RoadGraph

__all__ = [
    "SAFETY_DISCLAIMER",
    "DEFAULT_BOUNDARY_TOLERANCE_M",
    "DEFAULT_SETTLEMENT_SNAP_M",
    "ExitNodeSet",
    "SettlementNodeSet",
    "RoadNetworkQA",
    "identify_exit_nodes",
    "identify_settlement_nodes",
    "articulation_points",
    "bridge_edges",
    "critical_links",
    "crossings_without_node",
    "assess_road_network",
]

#: Emitted inside every QA report. Not decoration: this report will be read by
#: people deciding where to send responders, and the graph does not know what it
#: does not know.
SAFETY_DISCLAIMER = (
    "These are topological properties of the road data as digitised. They are "
    "NOT statements about whether a road is passable, whether a vehicle of any "
    "size can use it, whether an egress is usable during a fire, or whether any "
    "community is safe or trapped. Exit nodes inferred from a study-area "
    "boundary are artifacts of the clip, not verified real-world exits."
)

#: A node this close to the study-area boundary is treated as a boundary exit.
#: Default is one 30 m DEM cell: a road digitised to a boundary rarely ends
#: exactly on it.
DEFAULT_BOUNDARY_TOLERANCE_M = 30.0

#: How far a settlement centroid may be from a road node to be matched to it.
DEFAULT_SETTLEMENT_SNAP_M = 250.0


@dataclass(frozen=True)
class ExitNodeSet:
    """Exit nodes, keeping *how* each was identified.

    The distinction matters: a source-tagged exit is a claim about the world,
    while a boundary-derived exit is an artifact of where the study area was cut
    (A-RD-4). A report built only from boundary exits is describing the clip as
    much as the network.
    """

    from_source_tags: frozenset[int] = frozenset()
    from_boundary: frozenset[int] = frozenset()
    boundary_tolerance_m: float | None = None

    @property
    def all_exits(self) -> frozenset[int]:
        return self.from_source_tags | self.from_boundary

    @property
    def clip_boundary_warning(self) -> bool:
        """Whether any exit was inferred from the boundary rather than tagged."""
        return bool(self.from_boundary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.all_exits),
            "from_source_tags": sorted(self.from_source_tags),
            "from_boundary": sorted(self.from_boundary),
            "boundary_tolerance_m": self.boundary_tolerance_m,
            "clip_boundary_warning": self.clip_boundary_warning,
            "note": (
                "boundary-derived exits exist because the network was clipped; a "
                "clip both creates exits (cut ends) and destroys them (a real "
                "second egress outside the extent). widen the study area to "
                "test whether an egress count is stable."
            ),
        }


@dataclass(frozen=True)
class SettlementNodeSet:
    """Road nodes matched to settlements, and the settlements that matched none."""

    node_by_settlement: dict[str, int] = field(default_factory=dict)
    unmatched_settlements: tuple[str, ...] = ()
    snap_distance_m: float | None = None
    matched_distance_m: dict[str, float] = field(default_factory=dict)

    @property
    def all_nodes(self) -> frozenset[int]:
        return frozenset(self.node_by_settlement.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.all_nodes),
            "node_by_settlement": dict(sorted(self.node_by_settlement.items())),
            "matched_distance_m": {
                k: round(v, 3) for k, v in sorted(self.matched_distance_m.items())
            },
            "unmatched_settlements": list(self.unmatched_settlements),
            "snap_distance_m": self.snap_distance_m,
            "note": (
                "an unmatched settlement is a finding, not a blank: either the "
                "road network is incomplete near it, or the settlement centroid "
                "is wrong, or the snap distance is too small. it is never "
                "treated as having no road access."
            ),
        }


def identify_exit_nodes(
    road_graph: RoadGraph,
    *,
    study_bounds: Bounds | None = None,
    boundary_tolerance_m: float = DEFAULT_BOUNDARY_TOLERANCE_M,
    exit_property: str | None = None,
    exit_property_values: Sequence[Any] = (True, "true", "yes", 1, "exit"),
) -> ExitNodeSet:
    """Find exit nodes, from source tags and/or proximity to the study boundary.

    Parameters
    ----------
    exit_property:
        An edge property name whose truthy value marks an edge as leaving the
        study area; both of that edge's nodes are then exits.
    study_bounds:
        When given, nodes within ``boundary_tolerance_m`` of the boundary
        **ring** (not the interior) are exits (A-RD-4).
    """
    tagged: set[int] = set()
    if exit_property is not None:
        accepted = {str(v).lower() for v in exit_property_values}
        for u, v, data in road_graph.graph.edges(data=True):
            value = (data.get("properties") or {}).get(exit_property)
            if value is not None and str(value).lower() in accepted:
                tagged.add(u)
                tagged.add(v)

    boundary: set[int] = set()
    if study_bounds is not None:
        require_same_crs(
            [road_graph.crs, study_bounds.crs], context="identifying boundary exit nodes"
        )
        ring = shapely_box(*study_bounds.as_tuple()).exterior
        for node, point in road_graph.node_points.items():
            if ring.distance(point) <= boundary_tolerance_m:
                boundary.add(node)

    return ExitNodeSet(
        from_source_tags=frozenset(tagged),
        from_boundary=frozenset(boundary - tagged),
        boundary_tolerance_m=boundary_tolerance_m if study_bounds is not None else None,
    )


def identify_settlement_nodes(
    road_graph: RoadGraph,
    settlements: Iterable[tuple[str, Point]],
    *,
    snap_distance_m: float = DEFAULT_SETTLEMENT_SNAP_M,
) -> SettlementNodeSet:
    """Match settlement points to their nearest road node within a distance.

    A settlement with no node within ``snap_distance_m`` goes into
    ``unmatched_settlements`` rather than being dropped or attached to an
    arbitrarily distant node: "this village has no digitised road access within
    250 m" is exactly the kind of finding this repository exists to surface.
    """
    items = list(settlements)
    node_ids = list(road_graph.node_points)
    if not node_ids:
        raise GraphError("road graph has no nodes; cannot match settlements")
    points = [road_graph.node_points[n] for n in node_ids]
    tree = STRtree(points)

    matched: dict[str, int] = {}
    distances: dict[str, float] = {}
    unmatched: list[str] = []
    for label, point in items:
        nearest_index = int(tree.nearest(point))
        node = node_ids[nearest_index]
        distance = float(point.distance(points[nearest_index]))
        if distance <= snap_distance_m:
            matched[label] = node
            distances[label] = distance
        else:
            unmatched.append(label)
    return SettlementNodeSet(
        node_by_settlement=matched,
        unmatched_settlements=tuple(unmatched),
        snap_distance_m=snap_distance_m,
        matched_distance_m=distances,
    )


def articulation_points(road_graph: RoadGraph) -> list[int]:
    """Nodes whose removal increases the component count.

    Computed on the simple graph: parallel edges and self-loops cannot change
    which *vertices* are cut points, so this is exact, not an approximation.
    """
    simple = road_graph.simple_graph()
    return sorted(nx.articulation_points(simple))


def bridge_edges(road_graph: RoadGraph) -> list[tuple[int, int]]:
    """Edges whose removal increases the component count.

    Graph-theoretic bridges, not structural bridges over water
    (``docs/GLOSSARY.md``). Multiplicity-aware: an edge that is a bridge in the
    simple graph but has a parallel twin is **not** a bridge, and reporting it
    as one would overstate how fragile the network is.
    """
    simple = road_graph.simple_graph()
    out: list[tuple[int, int]] = []
    for u, v in nx.bridges(simple):
        if road_graph.edge_multiplicity(u, v) == 1:
            out.append((min(u, v), max(u, v)))
    return sorted(out)


def critical_links(
    road_graph: RoadGraph,
    *,
    exit_nodes: Iterable[int],
    settlement_nodes: Iterable[int],
) -> list[dict[str, Any]]:
    """Edges whose removal cuts a settlement node off from every exit node.

    A graph cut property, reported with the settlements it would isolate. No
    travel time, no capacity, no safety interpretation -- see
    :data:`SAFETY_DISCLAIMER`.

    Only bridges are candidates: removing a non-bridge edge leaves the component
    connected by definition, so it cannot disconnect anything. Nothing is
    approximated by that restriction.
    """
    exits = set(exit_nodes)
    settlements = set(settlement_nodes)
    if not exits or not settlements:
        return []

    simple = road_graph.simple_graph()
    out: list[dict[str, Any]] = []
    for u, v in bridge_edges(road_graph):
        trial = simple.copy()
        trial.remove_edge(u, v)
        components = list(nx.connected_components(trial))
        cut_off: list[int] = []
        for component in components:
            if component & settlements and not (component & exits):
                cut_off.extend(sorted(component & settlements))
        if cut_off:
            lengths = [
                float(data.get("length_m", 0.0))
                for data in road_graph.graph[u][v].values()
            ]
            out.append(
                {
                    "edge": [u, v],
                    "length_m": min(lengths) if lengths else None,
                    "settlement_nodes_cut_off": sorted(set(cut_off)),
                    "note": (
                        "removal of this edge leaves the listed settlement nodes "
                        "with no path to any exit node in this graph"
                    ),
                }
            )
    return out


def crossings_without_node(
    road_graph: RoadGraph, *, max_samples: int = 20
) -> dict[str, Any]:
    """Count line pairs that cross without sharing a node (D-0007 diagnostic).

    A high count means either genuine grade separation (bridges, tunnels -- in
    which case the default non-noding behaviour is right) or a network digitised
    without junction nodes (in which case connectivity is under-reported). The
    diagnostic does not decide which; it hands the analyst the number and a few
    locations.
    """
    edges = list(road_graph.graph.edges(keys=True, data=True))
    geometries = [data.get("geometry") for _, _, _, data in edges]
    usable = [(i, g) for i, g in enumerate(geometries) if isinstance(g, LineString)]
    if not usable:
        return {"count": 0, "samples": [], "note": "no edge geometry available"}

    tree = STRtree([g for _, g in usable])
    index_map = [i for i, _ in usable]
    seen: set[tuple[int, int]] = set()
    samples: list[dict[str, Any]] = []
    count = 0
    for position, (edge_index, geometry) in enumerate(usable):
        for hit in tree.query(geometry):
            other_position = int(hit)
            if other_position == position:
                continue
            pair = (min(position, other_position), max(position, other_position))
            if pair in seen:
                continue
            seen.add(pair)
            other_index = index_map[other_position]
            u1, v1, _, _ = edges[edge_index]
            u2, v2, _, _ = edges[other_index]
            if {u1, v1} & {u2, v2}:
                continue  # already share a node
            other_geometry = usable[other_position][1]
            if not geometry.crosses(other_geometry):
                continue
            count += 1
            if len(samples) < max_samples:
                point = geometry.intersection(other_geometry)
                representative = (
                    point.representative_point() if not point.is_empty else None
                )
                samples.append(
                    {
                        "edges": [[u1, v1], [u2, v2]],
                        "x": float(representative.x) if representative else None,
                        "y": float(representative.y) if representative else None,
                    }
                )
    return {
        "count": count,
        "samples": samples,
        "note": (
            "lines crossing with no shared node are NOT connected in this graph "
            "(docs/DECISIONS.md D-0007). a grade-separated crossing makes that "
            "correct; a missing junction node makes it an under-connection. this "
            "diagnostic does not distinguish them."
        ),
    }


@dataclass(frozen=True)
class RoadNetworkQA:
    """The full QA report for one road graph."""

    source_layer: str
    crs: str
    node_count: int
    edge_count: int
    total_length_m: float
    snap_tolerance_m: float
    max_intra_cluster_distance_m: float
    component_count: int
    components: tuple[dict[str, Any], ...]
    node_degree_histogram: dict[int, int]
    dead_end_nodes: tuple[int, ...]
    isolated_segment_edges: tuple[dict[str, Any], ...]
    exits: ExitNodeSet
    settlements: SettlementNodeSet
    single_egress_candidates: tuple[dict[str, Any], ...]
    no_egress_components: tuple[dict[str, Any], ...]
    articulation_point_nodes: tuple[int, ...]
    bridge_edge_list: tuple[tuple[int, int], ...]
    critical_link_list: tuple[dict[str, Any], ...]
    crossing_diagnostic: dict[str, Any]
    self_loop_edges: tuple[tuple[int, int], ...]
    parallel_edge_pairs: tuple[dict[str, Any], ...]
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "safety_disclaimer": SAFETY_DISCLAIMER,
            "source_layer": self.source_layer,
            "crs": self.crs,
            "graph": {
                "nodes": self.node_count,
                "edges": self.edge_count,
                "total_length_m": self.total_length_m,
                "length_unit": "m",
                "length_note": (
                    "planar 2-D length in the layer's projected CRS; not "
                    "slope-corrected, not geodesic, not a travel distance "
                    "(docs/ASSUMPTIONS.md A-RD-5)"
                ),
                "directed": False,
                "snap_tolerance_m": self.snap_tolerance_m,
                "max_intra_cluster_distance_m": self.max_intra_cluster_distance_m,
            },
            "connectivity": {
                "component_count": self.component_count,
                "components": list(self.components),
                "node_degree_histogram": {
                    str(k): v for k, v in sorted(self.node_degree_histogram.items())
                },
                "degree_note": (
                    "multigraph degree: a parallel edge counts twice for its two "
                    "nodes, and a self-loop adds 2 to its node's degree"
                ),
                "dead_end_nodes": list(self.dead_end_nodes),
                "dead_end_count": len(self.dead_end_nodes),
                "isolated_segments": list(self.isolated_segment_edges),
                "self_loop_edges": [list(e) for e in self.self_loop_edges],
                "parallel_edge_pairs": list(self.parallel_edge_pairs),
            },
            "egress": {
                "exits": self.exits.to_dict(),
                "settlements": self.settlements.to_dict(),
                "single_egress_candidates": list(self.single_egress_candidates),
                "no_egress_components": list(self.no_egress_components),
                "candidate_note": (
                    "'candidate' is load-bearing: this is a topological count of "
                    "exits in this graph, not a claim that a community can or "
                    "cannot get out (docs/DECISIONS.md D-0008)"
                ),
            },
            "fragility": {
                "articulation_points": list(self.articulation_point_nodes),
                "bridge_edges": [list(e) for e in self.bridge_edge_list],
                "critical_links": list(self.critical_link_list),
                "bridge_note": (
                    "graph-theoretic bridges (edge cuts), not structural bridges "
                    "over water; multiplicity-aware (docs/GLOSSARY.md)"
                ),
            },
            "crossings_without_node": self.crossing_diagnostic,
            "notes": list(self.notes),
        }


def assess_road_network(
    road_graph: RoadGraph,
    *,
    study_bounds: Bounds | None = None,
    boundary_tolerance_m: float = DEFAULT_BOUNDARY_TOLERANCE_M,
    exit_property: str | None = None,
    settlements: Iterable[tuple[str, Point]] = (),
    settlement_snap_m: float = DEFAULT_SETTLEMENT_SNAP_M,
) -> RoadNetworkQA:
    """Run every QA diagnostic over a road graph and return the report."""
    graph = road_graph.graph
    exits = identify_exit_nodes(
        road_graph,
        study_bounds=study_bounds,
        boundary_tolerance_m=boundary_tolerance_m,
        exit_property=exit_property,
    )
    settlement_set = (
        identify_settlement_nodes(road_graph, settlements, snap_distance_m=settlement_snap_m)
        if settlements
        else SettlementNodeSet(snap_distance_m=settlement_snap_m)
    )
    exit_ids = exits.all_exits
    settlement_ids = settlement_set.all_nodes

    degrees = dict(graph.degree())
    histogram: dict[int, int] = {}
    for degree in degrees.values():
        histogram[int(degree)] = histogram.get(int(degree), 0) + 1

    dead_ends = tuple(
        sorted(node for node, degree in degrees.items() if degree == 1 and node not in exit_ids)
    )

    components_raw = list(nx.connected_components(graph))
    components: list[dict[str, Any]] = []
    isolated: list[dict[str, Any]] = []
    single_egress: list[dict[str, Any]] = []
    no_egress: list[dict[str, Any]] = []

    for component_id, nodes in enumerate(sorted(components_raw, key=lambda c: -len(c))):
        subgraph = graph.subgraph(nodes)
        edge_count = int(subgraph.number_of_edges())
        length = float(
            sum(d.get("length_m", 0.0) for _, _, d in subgraph.edges(data=True))
        )
        component_exits = sorted(set(nodes) & exit_ids)
        component_settlements = sorted(set(nodes) & settlement_ids)
        entry = {
            "component_id": component_id,
            "node_count": len(nodes),
            "edge_count": edge_count,
            "total_length_m": length,
            "exit_nodes": component_exits,
            "settlement_nodes": component_settlements,
        }
        components.append(entry)

        if edge_count == 1 and not component_exits:
            u, v, data = next(iter(subgraph.edges(data=True)))
            isolated.append(
                {
                    "edge": [u, v],
                    "component_id": component_id,
                    "length_m": float(data.get("length_m", 0.0)),
                    "feature_index": data.get("feature_index"),
                    "note": (
                        "a single edge alone in its component with no exit: as "
                        "digitised it connects to nothing"
                    ),
                }
            )
        if component_settlements:
            if len(component_exits) == 1:
                single_egress.append(
                    {
                        "component_id": component_id,
                        "exit_node": component_exits[0],
                        "settlement_nodes": component_settlements,
                        "settlements": sorted(
                            label
                            for label, node in settlement_set.node_by_settlement.items()
                            if node in nodes
                        ),
                        "note": (
                            "exactly one exit node in this component; whether that "
                            "exit is usable is not knowable from the road data "
                            "(docs/DECISIONS.md D-0008)"
                        ),
                    }
                )
            elif not component_exits:
                no_egress.append(
                    {
                        "component_id": component_id,
                        "settlement_nodes": component_settlements,
                        "settlements": sorted(
                            label
                            for label, node in settlement_set.node_by_settlement.items()
                            if node in nodes
                        ),
                        "note": (
                            "no exit node in this component. most often this means "
                            "the network was clipped too tightly or is incomplete, "
                            "NOT that the settlement is unreachable"
                        ),
                    }
                )

    self_loops = tuple(sorted((u, v) for u, v in nx.selfloop_edges(graph)))
    parallel: list[dict[str, Any]] = []
    for u, v in {(min(a, b), max(a, b)) for a, b in graph.edges()}:
        multiplicity = road_graph.edge_multiplicity(u, v)
        if u != v and multiplicity > 1:
            parallel.append({"edge": [u, v], "multiplicity": multiplicity})

    notes = [
        "counts describe the network as digitised, after endpoint snapping at "
        f"{road_graph.snap_tolerance_m} m",
    ]
    if road_graph.max_intra_cluster_distance_m > road_graph.snap_tolerance_m:
        notes.append(
            "endpoint clustering is transitive: some nodes merge endpoints up to "
            f"{road_graph.max_intra_cluster_distance_m:.2f} m apart, which exceeds "
            f"the {road_graph.snap_tolerance_m} m tolerance"
        )
    if road_graph.exploded_multilinestrings:
        notes.append(
            f"{road_graph.exploded_multilinestrings} MultiLineString feature(s) "
            "were exploded into parts, so edge count exceeds feature count"
        )
    if road_graph.dropped_zero_length:
        notes.append(
            f"{road_graph.dropped_zero_length} zero-length geometry/geometries were dropped"
        )
    if road_graph.node_crossings_added:
        notes.append(
            f"{road_graph.node_crossings_added} crossing(s) were noded on request "
            "(node_crossings=True), departing from the D-0007 default"
        )
    if settlement_set.unmatched_settlements:
        notes.append(
            f"{len(settlement_set.unmatched_settlements)} settlement(s) matched no "
            f"road node within {settlement_set.snap_distance_m} m"
        )

    return RoadNetworkQA(
        source_layer=road_graph.source_layer,
        crs=crs_to_string(road_graph.crs),
        node_count=road_graph.node_count,
        edge_count=road_graph.edge_count,
        total_length_m=road_graph.total_length_m,
        snap_tolerance_m=road_graph.snap_tolerance_m,
        max_intra_cluster_distance_m=road_graph.max_intra_cluster_distance_m,
        component_count=len(components_raw),
        components=tuple(components),
        node_degree_histogram=histogram,
        dead_end_nodes=dead_ends,
        isolated_segment_edges=tuple(isolated),
        exits=exits,
        settlements=settlement_set,
        single_egress_candidates=tuple(single_egress),
        no_egress_components=tuple(no_egress),
        articulation_point_nodes=tuple(articulation_points(road_graph)),
        bridge_edge_list=tuple(bridge_edges(road_graph)),
        critical_link_list=tuple(
            critical_links(
                road_graph, exit_nodes=exit_ids, settlement_nodes=settlement_ids
            )
        ),
        crossing_diagnostic=crossings_without_node(road_graph),
        self_loop_edges=self_loops,
        parallel_edge_pairs=tuple(parallel),
        notes=tuple(notes),
    )
