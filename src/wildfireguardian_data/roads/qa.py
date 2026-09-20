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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
from shapely.geometry import LineString, Point
from shapely.geometry import box as shapely_box
from shapely.strtree import STRtree

from ..bounds import Bounds
from ..crs import crs_to_string, require_same_crs
from ..errors import GraphError
from .graph import _SHARED_VERTEX_DECIMALS as _SHARED_VERTEX_ROUNDING
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
    "attribute_availability",
    "duplicate_geometry_edges",
    "grade_separation_edges",
    "network_density",
    "ROAD_ATTRIBUTES_OF_INTEREST",
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
    #: Nodes made exits because an edge's geometry crosses the study-area
    #: boundary. A fact about the data, stronger than node proximity.
    from_boundary_crossing: frozenset[int] = frozenset()
    from_boundary: frozenset[int] = frozenset()
    boundary_tolerance_m: float | None = None
    #: The edges found crossing the boundary, for inspection.
    boundary_crossing_edges: tuple[tuple[int, int], ...] = ()

    @property
    def all_exits(self) -> frozenset[int]:
        return self.from_source_tags | self.from_boundary_crossing | self.from_boundary

    @property
    def clip_boundary_warning(self) -> bool:
        """Whether any exit came from the boundary rather than from a source tag."""
        return bool(self.from_boundary or self.from_boundary_crossing)

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.all_exits),
            "from_source_tags": sorted(self.from_source_tags),
            "from_boundary_crossing": sorted(self.from_boundary_crossing),
            "from_boundary": sorted(self.from_boundary),
            "boundary_crossing_edge_count": len(self.boundary_crossing_edges),
            "boundary_crossing_edges": [list(e) for e in self.boundary_crossing_edges],
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
    #: Whether the settlement points' CRS was checked against the graph's. When
    #: false, a CRS mismatch would show up as "everything unmatched" rather than
    #: as an error, so the fact that no check ran is recorded.
    crs_checked: bool = False

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
            "crs_checked": self.crs_checked,
            "note": (
                "an unmatched settlement is a finding, not a blank: either the "
                "road network is incomplete near it, or the settlement centroid "
                "is wrong, or the snap distance is too small. it is never "
                "treated as having no road access."
            ),
        }


def _departure_nodes(
    road_graph: RoadGraph,
    u: int,
    v: int,
    *,
    study_bounds: Bounds | None,
) -> set[int]:
    """Which endpoints of a leaving edge are the points one departs *from*.

    An edge that leaves the study area gives its component egress, but marking
    **both** endpoints as exits over-counts: a single tagged through-road would
    contribute two exit nodes and a genuinely single-egress component would stop
    being reported as one. Marking neither under-counts just as badly.

    The rule: the endpoints that lie **inside** the study area are the departure
    points. If neither does -- the edge passes through, entering and leaving --
    both are returned, because from either end one can travel along it and
    leave. With no bounds to test against, both are returned.
    """
    if study_bounds is None:
        return {u, v}
    inside = {
        node
        for node in (u, v)
        if study_bounds.contains_point(*road_graph.node_xy(node))
    }
    return inside or {u, v}


def identify_exit_nodes(
    road_graph: RoadGraph,
    *,
    study_bounds: Bounds | None = None,
    boundary_tolerance_m: float = DEFAULT_BOUNDARY_TOLERANCE_M,
    exit_property: str | None = None,
    exit_property_values: Sequence[Any] = (True, "true", "yes", 1, "exit"),
    detect_boundary_crossings: bool = True,
) -> ExitNodeSet:
    """Find exit nodes: source-tagged, boundary-crossing, or boundary-adjacent.

    Three separately recorded ways a node can be an exit, because they are
    different kinds of claim (A-RD-4):

    ``from_source_tags``
        An edge property marks the edge as leaving the area. A statement about
        the world, from the source.
    ``from_boundary_crossing``
        An edge's **geometry crosses the study-area boundary**. Also a fact
        about the data, and the one an earlier version missed entirely: with
        ``clip_mode="intersects"`` whole features are kept, so a road leaving
        the area has no *node* near the boundary and node-proximity alone found
        almost none of them. On the Uljin bundle that under-reported egress as 1
        exit where 8 roads actually leave the extent.
    ``from_boundary``
        A node lies within ``boundary_tolerance_m`` of the boundary ring. The
        weakest of the three, and an artifact of where the study area was cut.

    In every case the exit node is the *departure* point -- see
    :func:`_departure_nodes` -- so one leaving road contributes one exit, not
    two.
    """
    tagged: set[int] = set()
    if exit_property is not None:
        accepted = {str(value).lower() for value in exit_property_values}
        for u, v, data in road_graph.graph.edges(data=True):
            value = (data.get("properties") or {}).get(exit_property)
            if value is not None and str(value).lower() in accepted:
                tagged |= _departure_nodes(road_graph, u, v, study_bounds=study_bounds)

    crossing: set[int] = set()
    boundary: set[int] = set()
    crossing_edges: list[list[int]] = []
    if study_bounds is not None:
        require_same_crs(
            [road_graph.crs, study_bounds.crs], context="identifying boundary exit nodes"
        )
        ring = shapely_box(*study_bounds.as_tuple()).exterior

        if detect_boundary_crossings:
            for u, v, data in road_graph.graph.edges(data=True):
                geometry = data.get("geometry")
                # `crosses`, not `intersects`: an edge that merely *touches* the
                # ring at its own endpoint has not left the study area, and that
                # endpoint is already covered by the node-proximity rule below.
                # Only an edge with geometry on both sides of the boundary is a
                # crossing.
                if geometry is None or not geometry.crosses(ring):
                    continue
                crossing |= _departure_nodes(road_graph, u, v, study_bounds=study_bounds)
                crossing_edges.append([min(u, v), max(u, v)])

        for node, point in road_graph.node_points.items():
            if ring.distance(point) <= boundary_tolerance_m:
                boundary.add(node)

    return ExitNodeSet(
        from_source_tags=frozenset(tagged),
        from_boundary_crossing=frozenset(crossing - tagged),
        from_boundary=frozenset(boundary - tagged - crossing),
        boundary_tolerance_m=boundary_tolerance_m if study_bounds is not None else None,
        boundary_crossing_edges=tuple(sorted({tuple(e) for e in crossing_edges})),
    )


def identify_settlement_nodes(
    road_graph: RoadGraph,
    settlements: Iterable[tuple[str, Point]],
    *,
    snap_distance_m: float = DEFAULT_SETTLEMENT_SNAP_M,
    settlements_crs: Any = None,
) -> SettlementNodeSet:
    """Match settlement points to their nearest road node within a distance.

    A settlement with no node within ``snap_distance_m`` goes into
    ``unmatched_settlements`` rather than being dropped or attached to an
    arbitrarily distant node: "this village has no digitised road access within
    250 m" is exactly the kind of finding this repository exists to surface.

    Parameters
    ----------
    settlements_crs:
        The CRS the settlement points are in. Checked against the graph's CRS
        (D-0002), because these are bare Shapely points and a metre-based
        distance comparison against lon/lat coordinates silently reports every
        settlement as unmatched rather than raising. Passing ``None`` skips the
        check and records that it was skipped -- acceptable only when the caller
        has already established that both are in one CRS, as the build pipeline
        has.
    """
    if settlements_crs is not None:
        require_same_crs(
            [road_graph.crs, settlements_crs],
            context="matching settlement points to road nodes",
        )
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
        crs_checked=settlements_crs is not None,
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

    **"Cuts off" is a change of state**, so a settlement counts only if it can
    reach some exit in the intact graph *and* cannot after the edge is removed.
    Without that comparison, a settlement that had no exit to begin with -- an
    orphan fragment, or a component the study-area clip severed -- would be
    attributed to every bridge anywhere in the graph, including bridges in
    unrelated components, and the count would inflate with exactly the road-data
    incompleteness that makes a network fragmented in the first place. Such
    settlements are reported once, as ``no_egress_components``, and never here.
    """
    exits = set(exit_nodes)
    settlements = set(settlement_nodes)
    if not exits or not settlements:
        return []

    simple = road_graph.simple_graph()
    # Settlements that have any path to any exit before we remove anything.
    # Only these can be newly cut off.
    connected_before: set[int] = set()
    for component in nx.connected_components(simple):
        if component & exits:
            connected_before |= component & settlements
    if not connected_before:
        return []

    out: list[dict[str, Any]] = []
    for u, v in bridge_edges(road_graph):
        trial = simple.copy()
        trial.remove_edge(u, v)
        components = list(nx.connected_components(trial))
        cut_off: list[int] = []
        for component in components:
            if not (component & exits):
                cut_off.extend(sorted(component & settlements & connected_before))
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
    road_graph: RoadGraph, *, max_samples: int = 20, node_tolerance_m: float = 0.5
) -> dict[str, Any]:
    """Count places where two edges cross at a point that is **not** a graph node.

    The D-0007 diagnostic: a crossing with no node there means the two lines
    pass over each other as far as the data is concerned. In OpenStreetMap that
    is exactly how a bridge or tunnel is encoded, so a non-zero count is
    expected and correct for a network with grade separation; a *large* count
    instead suggests a network digitised without junction nodes, whose
    connectivity is then under-reported. The diagnostic does not decide which.

    The test is on the **crossing point**, not on whether the two edges happen
    to share a node somewhere else. That distinction is load-bearing: a bridge
    whose far end rejoins the same road further along *does* share a node with
    it, and an earlier version skipped exactly those pairs -- so the clearest
    real cases of grade separation were the ones it missed.

    ``node_tolerance_m`` is how close a crossing point must be to a graph node
    to count as noded. It is half a metre by default: junction coordinates
    survive reprojection to well under that, and anything larger would start
    absorbing genuinely un-noded crossings.
    """
    edges = list(road_graph.graph.edges(keys=True, data=True))
    geometries = [data.get("geometry") for _, _, _, data in edges]
    usable = [(i, g) for i, g in enumerate(geometries) if isinstance(g, LineString)]
    if not usable:
        return {
            "count": 0,
            "samples": [],
            "node_tolerance_m": node_tolerance_m,
            "note": "no edge geometry available",
        }

    tree = STRtree([g for _, g in usable])
    node_tree = (
        STRtree(list(road_graph.node_points.values())) if road_graph.node_points else None
    )
    node_list = list(road_graph.node_points.values())

    def is_at_a_node(point: Point) -> bool:
        if node_tree is None:
            return False
        nearest = node_tree.nearest(point)
        if nearest is None:
            return False
        return bool(point.distance(node_list[int(nearest)]) <= node_tolerance_m)

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
            other_geometry = usable[other_position][1]
            if not geometry.crosses(other_geometry):
                continue
            intersection = geometry.intersection(other_geometry)
            candidates = (
                list(intersection.geoms) if hasattr(intersection, "geoms") else [intersection]
            )
            points = [c for c in candidates if isinstance(c, Point)]
            un_noded = [p for p in points if not is_at_a_node(p)]
            if not un_noded:
                continue
            count += len(un_noded)
            u1, v1, _, _ = edges[edge_index]
            u2, v2, _, _ = edges[usable[other_position][0]]
            for point in un_noded:
                if len(samples) >= max_samples:
                    break
                properties_a = (edges[edge_index][3].get("properties") or {})
                properties_b = (edges[usable[other_position][0]][3].get("properties") or {})
                samples.append(
                    {
                        "edges": [[u1, v1], [u2, v2]],
                        "x": float(point.x),
                        "y": float(point.y),
                        # Grade-separation tags, if the source carries them.
                        # Their presence is the reader's evidence that the
                        # crossing is real rather than a missing junction.
                        "bridge_tags": [
                            properties_a.get("bridge"),
                            properties_b.get("bridge"),
                        ],
                        "tunnel_tags": [
                            properties_a.get("tunnel"),
                            properties_b.get("tunnel"),
                        ],
                        "layer_tags": [
                            properties_a.get("layer"),
                            properties_b.get("layer"),
                        ],
                    }
                )
    return {
        "count": count,
        "samples": samples,
        "node_tolerance_m": node_tolerance_m,
        "note": (
            "crossing points that are not graph nodes. lines crossing WITHOUT a "
            "shared vertex are not connected (docs/DECISIONS.md D-0007), which "
            "is how OSM represents a bridge or tunnel -- check the bridge/tunnel/"
            "layer tags in the samples. a crossing at a shared vertex IS noded "
            "(D-0020) and does not appear here. this diagnostic does not "
            "distinguish grade separation from a missing junction node."
        ),
    }


#: Attributes a downstream routing or travel-time model typically wants. Their
#: *availability* is reported; none is ever defaulted by road class (D-0022).
ROAD_ATTRIBUTES_OF_INTEREST: tuple[str, ...] = (
    "highway",
    "oneway",
    "lanes",
    "surface",
    "smoothness",
    "tracktype",
    "maxspeed",
    "access",
    "width",
    "bridge",
    "tunnel",
    "layer",
    "service",
)


def attribute_availability(road_graph: RoadGraph) -> dict[str, Any]:
    """Per-attribute presence counts across edges, with the value distribution.

    Reported so a consumer can see what it is *not* getting. An attribute absent
    from every edge is the normal case in rural Korean OSM, and a consumer that
    needs it must supply its own default in its own declared parameter layer --
    this repository will not invent one (D-0022).
    """
    edges = list(road_graph.graph.edges(data=True))
    total = len(edges)
    out: dict[str, Any] = {}
    for attribute in ROAD_ATTRIBUTES_OF_INTEREST:
        values: dict[str, int] = {}
        present = 0
        for _, _, data in edges:
            value = (data.get("properties") or {}).get(attribute)
            if value is None:
                continue
            present += 1
            key = str(value)
            values[key] = values.get(key, 0) + 1
        out[attribute] = {
            "edges_with_value": present,
            "edges_total": total,
            "fraction_present": round(present / total, 4) if total else None,
            "values": dict(sorted(values.items(), key=lambda kv: -kv[1])[:12]),
        }
    out["_note"] = (
        "availability only. no attribute is defaulted by road class: a "
        "fabricated 'lanes' or 'surface' would be indistinguishable from a "
        "sourced one in the field a travel-time model multiplies by (D-0022)."
    )
    return out


def duplicate_geometry_edges(road_graph: RoadGraph) -> list[dict[str, Any]]:
    """Edges whose geometry duplicates another edge's, exactly or reversed.

    Distinct from parallel edges, which merely share endpoints: two genuinely
    different roads between the same junctions are two ways out and must be kept
    (F-RD-5). A *duplicated geometry* is instead a digitising artifact -- the
    same road entered twice -- which inflates length and hides fragility by
    making a real bridge look like a parallel pair.
    """
    seen: dict[tuple, list[tuple[int, int]]] = {}
    for u, v, data in road_graph.graph.edges(data=True):
        geometry = data.get("geometry")
        if geometry is None:
            continue
        coordinates = [
            (round(x, _SHARED_VERTEX_ROUNDING), round(y, _SHARED_VERTEX_ROUNDING))
            for x, y in geometry.coords
        ]
        # Canonical form: whichever direction sorts first, so the same road
        # digitised in the opposite direction still matches.
        key = tuple(min(coordinates, coordinates[::-1]))
        seen.setdefault(key, []).append((min(u, v), max(u, v)))
    return [
        {
            "edges": [list(e) for e in edges],
            "multiplicity": len(edges),
            "note": "identical geometry, possibly digitised twice",
        }
        for edges in seen.values()
        if len(edges) > 1
    ]


def grade_separation_edges(road_graph: RoadGraph) -> dict[str, Any]:
    """Edges the source tags as bridges or tunnels, and their noding state.

    A bridge or tunnel *should* cross something without sharing a node. This
    reports the tagged edges so a reader can check that the un-noded crossings
    found elsewhere are the tagged ones -- and, more usefully, notice when they
    are **not**, which means either an untagged grade separation or a missing
    junction.
    """
    bridges: list[dict[str, Any]] = []
    tunnels: list[dict[str, Any]] = []
    for u, v, data in road_graph.graph.edges(data=True):
        properties = data.get("properties") or {}
        entry = {
            "edge": [min(u, v), max(u, v)],
            "length_m": float(data.get("length_m", 0.0)),
            "layer": properties.get("layer"),
            "highway": properties.get("highway"),
        }
        if properties.get("bridge") not in (None, "no"):
            bridges.append({**entry, "bridge": properties.get("bridge")})
        if properties.get("tunnel") not in (None, "no"):
            tunnels.append({**entry, "tunnel": properties.get("tunnel")})
    return {
        "bridge_edges": bridges,
        "tunnel_edges": tunnels,
        "bridge_count": len(bridges),
        "tunnel_count": len(tunnels),
        "note": (
            "source-tagged grade separation. a tagged bridge that crosses "
            "nothing in this layer may cross a stream or railway not included "
            "here; an UNtagged crossing is either unmapped grade separation or "
            "a missing junction, which the crossings_without_node diagnostic "
            "reports without deciding which."
        ),
    }


def network_density(road_graph: RoadGraph, study_bounds: Bounds | None) -> dict[str, Any]:
    """Road length per square kilometre, overall and by quadrant.

    A crude gap detector: a quadrant with an order-of-magnitude lower density
    than its neighbours is either genuinely roadless (steep forest, water) or
    unmapped, and this cannot tell which. It is reported so the question gets
    asked rather than assumed away (F-RD-3).
    """
    if study_bounds is None:
        return {"available": False, "note": "no study bounds supplied"}
    area_km2 = (study_bounds.width * study_bounds.height) / 1e6
    if area_km2 <= 0:
        return {"available": False, "note": "degenerate study bounds"}

    mid_x, mid_y = study_bounds.center
    quadrants = {
        "north_west": (study_bounds.min_x, mid_y, mid_x, study_bounds.max_y),
        "north_east": (mid_x, mid_y, study_bounds.max_x, study_bounds.max_y),
        "south_west": (study_bounds.min_x, study_bounds.min_y, mid_x, mid_y),
        "south_east": (mid_x, study_bounds.min_y, study_bounds.max_x, mid_y),
    }
    lengths = dict.fromkeys(quadrants, 0.0)
    for _, _, data in road_graph.graph.edges(data=True):
        geometry = data.get("geometry")
        if geometry is None:
            continue
        for name, box in quadrants.items():
            clipped = geometry.intersection(shapely_box(*box))
            if not clipped.is_empty:
                lengths[name] += float(clipped.length)

    quadrant_area = area_km2 / 4.0
    densities = {
        name: round(length / 1000.0 / quadrant_area, 3) for name, length in lengths.items()
    }
    nonzero = [d for d in densities.values() if d > 0]
    return {
        "available": True,
        "study_area_km2": round(area_km2, 3),
        "km_per_km2_overall": round(road_graph.total_length_m / 1000.0 / area_km2, 3),
        "km_per_km2_by_quadrant": densities,
        "empty_quadrants": sorted(n for n, d in densities.items() if d == 0.0),
        "max_over_min_ratio": (
            round(max(nonzero) / min(nonzero), 2) if len(nonzero) > 1 else None
        ),
        "note": (
            "a low- or zero-density quadrant is either genuinely roadless "
            "(steep forest, water) or unmapped. this diagnostic cannot "
            "distinguish them, and completeness stays UNKNOWN "
            "(docs/FAILURE_MODES.md F-RD-3)."
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
    #: Share of the network in its largest component, by edges and by length.
    largest_component: dict[str, Any] = field(default_factory=dict)
    attribute_availability_report: dict[str, Any] = field(default_factory=dict)
    duplicate_geometry: tuple[dict[str, Any], ...] = ()
    grade_separation: dict[str, Any] = field(default_factory=dict)
    density: dict[str, Any] = field(default_factory=dict)
    build_diagnostics: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            # 1.1.0 added largest_component, attribute_availability,
            # duplicate_geometry, grade_separation, density, build_diagnostics,
            # and the exit categories from D-0021.
            "schema_version": "1.1.0",
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
                "duplicate_geometry": list(self.duplicate_geometry),
                "largest_component": dict(self.largest_component),
            },
            "attributes": self.attribute_availability_report,
            "grade_separation": self.grade_separation,
            "density": self.density,
            "build_diagnostics": self.build_diagnostics,
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
    settlements_crs: Any = None,
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
        identify_settlement_nodes(
            road_graph,
            settlements,
            snap_distance_m=settlement_snap_m,
            settlements_crs=settlements_crs,
        )
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

    largest = max(components, key=lambda c: c["edge_count"]) if components else {}
    total_length = road_graph.total_length_m
    largest_component = (
        {
            "component_id": largest["component_id"],
            "node_count": largest["node_count"],
            "edge_count": largest["edge_count"],
            "total_length_m": largest["total_length_m"],
            "edge_fraction": round(largest["edge_count"] / road_graph.edge_count, 4)
            if road_graph.edge_count
            else None,
            "length_fraction": round(largest["total_length_m"] / total_length, 4)
            if total_length
            else None,
            "note": (
                "a largest-component fraction well below 1 means the network is "
                "fragmented as digitised. before reading that as sparsity, rule "
                "out the pipeline: see reports/ULJIN_ROAD_AUDIT.md, where "
                "endpoint-only connection produced 22 components from a network "
                "that has 2"
            ),
        }
        if largest
        else {}
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
        largest_component=largest_component,
        attribute_availability_report=attribute_availability(road_graph),
        duplicate_geometry=tuple(duplicate_geometry_edges(road_graph)),
        grade_separation=grade_separation_edges(road_graph),
        density=network_density(road_graph, study_bounds),
        build_diagnostics={
            "snap_tolerance_m": road_graph.snap_tolerance_m,
            "max_intra_cluster_distance_m": road_graph.max_intra_cluster_distance_m,
            "shared_vertices_found": road_graph.shared_vertices_found,
            "shared_vertex_splits": road_graph.shared_vertex_splits,
            "node_crossings_added": road_graph.node_crossings_added,
            "exploded_multilinestrings": road_graph.exploded_multilinestrings,
            "dropped_zero_length": road_graph.dropped_zero_length,
            "note": (
                "how the graph was built. dropped_zero_length and "
                "shared_vertex_splits are the numbers to check first when a "
                "component count looks wrong (docs/DECISIONS.md D-0020)"
            ),
        },
        notes=tuple(notes),
    )
