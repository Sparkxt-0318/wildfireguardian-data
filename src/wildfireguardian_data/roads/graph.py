"""Building an undirected road graph from a line layer.

The graph is for **topology QA of the input data** -- not for routing
(``docs/SCOPE.md``). It is undirected (A-RD-1), its edge lengths are planar
lengths in the layer's projected CRS (A-RD-5), and it connects lines only where
their endpoints coincide within a tolerance (A-RD-2, D-0007).

Why a :class:`networkx.MultiGraph` and not a ``Graph``: two distinct roads
between the same pair of junctions are two ways out, and collapsing them to one
edge would make that edge look like a bridge whose loss disconnects the
junction. Parallel edges are therefore preserved, and bridge detection accounts
for multiplicity explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiLineString, Point
from shapely.strtree import STRtree

from ..crs import crs_to_string, require_projected_metre_crs
from ..errors import GraphError
from ..provenance.models import ProvenanceRecord, Transformation
from ..units import DistanceUnit
from ..vector import VectorLayer

__all__ = ["RoadGraph", "build_road_graph", "DEFAULT_SNAP_TOLERANCE_M"]

#: Endpoints within this distance are treated as the same junction (A-RD-2).
#: A modelling choice with real consequences, so it is recorded in provenance
#: and echoed in the QA report rather than buried as a constant.
DEFAULT_SNAP_TOLERANCE_M = 1.0


@dataclass
class RoadGraph:
    """An undirected multigraph of road segments, with its geometry and metadata.

    Attributes
    ----------
    graph:
        ``networkx.MultiGraph``. Node keys are integers; each node has an
        ``x``/``y`` attribute in the layer's CRS. Each edge has ``length_m``,
        ``feature_index``, and ``properties`` (the opaque source attributes).
    node_points:
        Node key -> :class:`shapely.geometry.Point`.
    snap_tolerance_m:
        The tolerance actually used.
    max_intra_cluster_distance_m:
        Largest distance between two endpoints that ended up as the *same*
        node. Reported because endpoint clustering is transitive: a chain of
        endpoints each 0.9 m from the next collapses into one node spanning
        several metres, and that needs to be visible rather than inferred.
    """

    graph: nx.MultiGraph
    node_points: dict[int, Point]
    crs: Any
    snap_tolerance_m: float
    source_layer: str
    provenance: ProvenanceRecord
    distance_unit: DistanceUnit = DistanceUnit.METRE
    max_intra_cluster_distance_m: float = 0.0
    exploded_multilinestrings: int = 0
    dropped_zero_length: int = 0
    node_crossings_added: int = 0
    shared_vertex_splits: int = 0
    shared_vertices_found: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- basic properties --------------------------------------------------- #
    @property
    def node_count(self) -> int:
        return int(self.graph.number_of_nodes())

    @property
    def edge_count(self) -> int:
        return int(self.graph.number_of_edges())

    @property
    def total_length_m(self) -> float:
        return float(
            sum(data.get("length_m", 0.0) for _, _, data in self.graph.edges(data=True))
        )

    def simple_graph(self) -> nx.Graph:
        """A simple ``Graph`` view, keeping the shortest parallel edge's length.

        Used for articulation points (which parallel edges cannot affect) and as
        the first stage of bridge detection (which they can -- see
        :func:`wildfireguardian_data.roads.qa.bridge_edges`).
        """
        simple = nx.Graph()
        simple.add_nodes_from(self.graph.nodes(data=True))
        for u, v, data in self.graph.edges(data=True):
            if u == v:
                continue  # self-loops are irrelevant to connectivity
            length = float(data.get("length_m", 0.0))
            if simple.has_edge(u, v):
                simple[u][v]["multiplicity"] += 1
                simple[u][v]["length_m"] = min(simple[u][v]["length_m"], length)
            else:
                simple.add_edge(u, v, length_m=length, multiplicity=1)
        return simple

    def edge_multiplicity(self, u: int, v: int) -> int:
        """How many parallel edges join ``u`` and ``v``."""
        if not self.graph.has_edge(u, v):
            return 0
        return len(self.graph[u][v])

    def node_xy(self, node: int) -> tuple[float, float]:
        point = self.node_points[node]
        return (float(point.x), float(point.y))

    def describe(self) -> dict[str, Any]:
        return {
            "source_layer": self.source_layer,
            "crs": crs_to_string(self.crs),
            "nodes": self.node_count,
            "edges": self.edge_count,
            "total_length_m": self.total_length_m,
            "snap_tolerance_m": self.snap_tolerance_m,
            "max_intra_cluster_distance_m": self.max_intra_cluster_distance_m,
            "exploded_multilinestrings": self.exploded_multilinestrings,
            "dropped_zero_length": self.dropped_zero_length,
            "node_crossings_added": self.node_crossings_added,
            "shared_vertex_splits": self.shared_vertex_splits,
            "shared_vertices_found": self.shared_vertices_found,
            "distance_unit": self.distance_unit.value,
        }


class _UnionFind:
    """Minimal union-find, used to cluster coincident endpoints."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:  # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self._parent[max(a, b)] = min(a, b)


def _explode_lines(layer: VectorLayer) -> tuple[list[tuple[int, LineString]], int, int]:
    """Flatten a layer into ``(feature_index, LineString)`` pairs.

    ``MultiLineString`` features are exploded into their parts, which is counted
    and recorded: a multi-part feature is several road segments, and treating it
    as one would mis-state both the edge count and the topology. Non-line
    geometries raise, and zero-length lines are dropped with a count (they
    cannot contribute a junction relationship).
    """
    out: list[tuple[int, LineString]] = []
    exploded = 0
    dropped = 0
    for index, feature in enumerate(layer.features):
        geometry = feature.geometry
        if isinstance(geometry, LineString):
            parts: Sequence[LineString] = [geometry]
        elif isinstance(geometry, MultiLineString):
            parts = list(geometry.geoms)
            exploded += 1
        else:
            raise GraphError(
                f"feature {index} of layer {layer.name!r} is a "
                f"{geometry.geom_type}, not a LineString or MultiLineString. a "
                "road network must be lines; points and polygons are rejected "
                "rather than skipped so the input error is visible."
            )
        for part in parts:
            if part.length == 0.0 or len(part.coords) < 2:
                dropped += 1
                continue
            out.append((index, part))
    return out, exploded, dropped


#: Coordinates are compared after rounding to this many decimal places (in CRS
#: units, so nanometres for a metre CRS). This is float-noise tolerance, not a
#: snapping distance: two vertices are "shared" only when the source put them at
#: the *same* place, and reprojection of one source node yields one output
#: coordinate deterministically.
_SHARED_VERTEX_DECIMALS = 9


def _split_at_shared_vertices(
    lines: list[tuple[int, LineString]]
) -> tuple[list[tuple[int, LineString]], int, int]:
    """Split lines at vertices that two different lines genuinely share.

    This is **not** intersection noding (D-0007), and the distinction is the
    whole point. A shared vertex means the source placed one coordinate in two
    lines' coordinate lists -- an explicit assertion that they meet. A geometric
    crossing with no shared vertex means the lines pass over each other, which
    in OpenStreetMap is exactly how a bridge or tunnel is represented. So this
    recovers the junctions the source states while still refusing to fabricate
    the ones it does not.

    Why it is needed: OSM (and most road GIS) encodes a junction as a shared
    *node*, which is very often an **interior** vertex of a way rather than an
    endpoint -- a side road meeting the middle of a through road. Connecting
    only at endpoints therefore discards most of the topology the source
    actually carries. In the Uljin extract, 35 of 41 shared OSM nodes between
    road ways involved an interior vertex (``reports/ULJIN_ROAD_AUDIT.md``).

    Returns ``(lines, splits_made, shared_vertex_count)``.
    """
    # Which parts touch each rounded coordinate.
    owners: dict[tuple[float, float], set[int]] = {}
    coords_per_part: list[list[tuple[float, float]]] = []
    for part_index, (_, line) in enumerate(lines):
        rounded = [
            (round(x, _SHARED_VERTEX_DECIMALS), round(y, _SHARED_VERTEX_DECIMALS))
            for x, y in line.coords
        ]
        coords_per_part.append(rounded)
        for coordinate in rounded:
            owners.setdefault(coordinate, set()).add(part_index)

    shared = {c for c, parts in owners.items() if len(parts) > 1}
    if not shared:
        return lines, 0, 0

    out: list[tuple[int, LineString]] = []
    splits = 0
    for part_index, (feature_index, line) in enumerate(lines):
        rounded = coords_per_part[part_index]
        # Interior vertices only: the endpoints already become nodes through
        # endpoint clustering, so splitting there would just duplicate work.
        cut_at = [
            i for i in range(1, len(rounded) - 1) if rounded[i] in shared
        ]
        if not cut_at:
            out.append((feature_index, line))
            continue
        coordinates = list(line.coords)
        boundaries = [0, *cut_at, len(coordinates) - 1]
        pieces = 0
        for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
            segment = coordinates[start : stop + 1]
            if len(segment) < 2:
                continue
            candidate = LineString(segment)
            if candidate.length == 0.0:
                continue
            out.append((feature_index, candidate))
            pieces += 1
        splits += max(0, pieces - 1)
    return out, splits, len(shared)


def _cluster_endpoints(
    points: list[Point], tolerance: float
) -> tuple[list[int], dict[int, Point], float]:
    """Cluster endpoints within ``tolerance``; return labels, centroids, max span.

    Clustering is transitive (union-find), so ``max span`` -- the largest
    distance between two endpoints sharing a node -- can exceed ``tolerance``
    and is returned so the caller can report it (see
    :attr:`RoadGraph.max_intra_cluster_distance_m`).
    """
    union = _UnionFind(len(points))
    if tolerance > 0 and points:
        tree = STRtree(points)
        for index, point in enumerate(points):
            for other in tree.query(point.buffer(tolerance)):
                other_index = int(other)
                if other_index != index and point.distance(points[other_index]) <= tolerance:
                    union.union(index, other_index)
    elif points:
        # tolerance == 0: exact coordinate identity only.
        exact: dict[tuple[float, float], int] = {}
        for index, point in enumerate(points):
            key = (float(point.x), float(point.y))
            if key in exact:
                union.union(exact[key], index)
            else:
                exact[key] = index

    groups: dict[int, list[int]] = {}
    for index in range(len(points)):
        groups.setdefault(union.find(index), []).append(index)

    labels = [0] * len(points)
    centroids: dict[int, Point] = {}
    max_span = 0.0
    for node_id, (_, members) in enumerate(sorted(groups.items())):
        xs = [points[m].x for m in members]
        ys = [points[m].y for m in members]
        centroids[node_id] = Point(sum(xs) / len(xs), sum(ys) / len(ys))
        for member in members:
            labels[member] = node_id
        if len(members) > 1:
            for i, left in enumerate(members):
                for right in members[i + 1 :]:
                    span = points[left].distance(points[right])
                    max_span = max(max_span, span)
    return labels, centroids, max_span


def build_road_graph(
    layer: VectorLayer,
    *,
    snap_tolerance_m: float = DEFAULT_SNAP_TOLERANCE_M,
    node_shared_vertices: bool = True,
    node_crossings: bool = False,
    name: str | None = None,
) -> RoadGraph:
    """Build a :class:`RoadGraph` from a line layer.

    Parameters
    ----------
    snap_tolerance_m:
        Endpoints within this distance become one node (A-RD-2). ``0`` means
        exact coordinate identity.
    node_shared_vertices:
        ``True`` by default: lines are split where they **share a vertex**, so a
        side road meeting the middle of a through road becomes a junction. This
        recovers topology the source explicitly encodes -- OSM represents a
        junction as a shared node, usually an interior vertex of a way -- and is
        distinct from ``node_crossings`` (D-0020). Set ``False`` to reproduce the
        endpoint-only behaviour, which under-connects any real road layer.
    node_crossings:
        ``False`` by default: lines that cross **without sharing a vertex** are
        **not** joined (D-0007), because such a crossing is how OSM represents a
        bridge or tunnel, and fabricating a junction there would silently
        convert a single-egress community into a two-egress one. Setting this
        ``True`` splits crossing lines at their intersections; the choice is
        recorded in provenance and reported in the QA output.

    Raises
    ------
    GraphError
        If the layer is empty, holds non-line geometry, or has no CRS.
    CRSError
        If the CRS is not projected in metres -- planar lengths in degrees are
        not lengths (D-0010).
    """
    require_projected_metre_crs(
        layer.crs, context=f"building a road graph from layer {layer.name!r}"
    )
    if snap_tolerance_m < 0:
        raise GraphError(f"snap_tolerance_m must be >= 0; got {snap_tolerance_m}")
    if layer.is_empty:
        raise GraphError(
            f"layer {layer.name!r} has no features; an empty road graph is "
            "reported as an error rather than returned, because every QA metric "
            "over it would be a vacuous zero."
        )

    lines, exploded, dropped = _explode_lines(layer)
    if not lines:
        raise GraphError(
            f"layer {layer.name!r} yielded no usable line geometry "
            f"({dropped} zero-length feature(s) dropped)."
        )

    shared_vertex_splits = 0
    shared_vertex_count = 0
    if node_shared_vertices:
        lines, shared_vertex_splits, shared_vertex_count = _split_at_shared_vertices(lines)

    crossings_added = 0
    split_failures: list[dict[str, Any]] = []
    if node_crossings:
        lines, crossings_added, split_failures = _split_at_crossings(lines)

    endpoints: list[Point] = []
    for _, line in lines:
        coords = list(line.coords)
        endpoints.append(Point(coords[0]))
        endpoints.append(Point(coords[-1]))

    labels, centroids, max_span = _cluster_endpoints(endpoints, snap_tolerance_m)

    graph = nx.MultiGraph()
    for node_id, point in centroids.items():
        graph.add_node(node_id, x=float(point.x), y=float(point.y))

    for position, (feature_index, line) in enumerate(lines):
        start = labels[2 * position]
        end = labels[2 * position + 1]
        properties = dict(layer.features[feature_index].properties)
        graph.add_edge(
            start,
            end,
            length_m=float(line.length),
            feature_index=feature_index,
            geometry=line,
            properties=properties,
        )

    transformation = Transformation(
        operation="build_road_graph",
        parameters={
            "snap_tolerance_m": snap_tolerance_m,
            "node_shared_vertices": node_shared_vertices,
            "shared_vertices_found": shared_vertex_count,
            "shared_vertex_splits": shared_vertex_splits,
            "node_crossings": node_crossings,
            "crossings_noded": crossings_added,
            "crossing_split_failures": split_failures,
            "features_in": len(layer.features),
            "lines_used": len(lines),
            "exploded_multilinestrings": exploded,
            "dropped_zero_length": dropped,
            "nodes": int(graph.number_of_nodes()),
            "edges": int(graph.number_of_edges()),
            "max_intra_cluster_distance_m": max_span,
            "crs": crs_to_string(layer.crs),
            "directed": False,
        },
        notes=(
            "undirected topology QA graph; planar edge lengths in CRS metres, "
            "not travel distances (docs/ASSUMPTIONS.md A-RD-1, A-RD-5). "
            + (
                f"split at {shared_vertex_splits} shared vertex/vertices, "
                "recovering junctions the source encodes as shared nodes "
                "(docs/DECISIONS.md D-0020). "
                if node_shared_vertices
                else "endpoint-only connection: junctions the source encodes at "
                "interior vertices are NOT recovered. "
            )
            + (
                "crossing lines were noded on request"
                if node_crossings
                else "lines crossing WITHOUT a shared vertex are NOT connected, "
                "which is how OSM represents a bridge or tunnel "
                "(docs/DECISIONS.md D-0007)"
            )
        ),
    )
    provenance = layer.provenance.derive(
        name or f"{layer.name}_graph", transformation, value_unit="m"
    )
    return RoadGraph(
        graph=graph,
        node_points=centroids,
        crs=layer.crs,
        snap_tolerance_m=float(snap_tolerance_m),
        source_layer=layer.name,
        provenance=provenance,
        max_intra_cluster_distance_m=float(max_span),
        exploded_multilinestrings=exploded,
        dropped_zero_length=dropped,
        node_crossings_added=crossings_added,
        shared_vertex_splits=shared_vertex_splits,
        shared_vertices_found=shared_vertex_count,
    )


def _split_at_crossings(
    lines: list[tuple[int, LineString]]
) -> tuple[list[tuple[int, LineString]], int, list[dict[str, Any]]]:
    """Split lines at mutual intersections (opt-in; see D-0007).

    Only point intersections that are interior to a line are used as split
    locations. Shared endpoints already produce a node through clustering, and
    overlapping collinear segments are left alone: splitting those would
    multiply geometry without adding topology.
    """
    from shapely.ops import split as shapely_split

    geometries = [line for _, line in lines]
    tree = STRtree(geometries)
    split_points: dict[int, list[Point]] = {}
    for index, line in enumerate(geometries):
        for other in tree.query(line):
            other_index = int(other)
            if other_index <= index:
                continue
            intersection = line.intersection(geometries[other_index])
            if intersection.is_empty:
                continue
            candidates = (
                list(intersection.geoms)
                if hasattr(intersection, "geoms")
                else [intersection]
            )
            for candidate in candidates:
                if not isinstance(candidate, Point):
                    continue
                for target in (index, other_index):
                    geometry = geometries[target]
                    endpoints = (
                        Point(geometry.coords[0]),
                        Point(geometry.coords[-1]),
                    )
                    if any(candidate.equals(e) for e in endpoints):
                        continue
                    split_points.setdefault(target, []).append(candidate)

    if not split_points:
        return lines, 0, []

    out: list[tuple[int, LineString]] = []
    added = 0
    split_failures: list[dict[str, Any]] = []
    for index, (feature_index, line) in enumerate(lines):
        points = split_points.get(index)
        if not points:
            out.append((feature_index, line))
            continue
        pieces = [line]
        for point in points:
            next_pieces: list[LineString] = []
            for piece in pieces:
                if piece.distance(point) > 1e-9:
                    next_pieces.append(piece)
                    continue
                try:
                    result = shapely_split(piece, point)
                except GEOSException as exc:
                    # A split that GEOS cannot perform leaves the line whole,
                    # which under-connects the network rather than fabricating a
                    # junction -- the safe direction (D-0007). But it must not be
                    # silent: the reason is surfaced so `crossings_noded` being
                    # short has an explanation.
                    split_failures.append(
                        {
                            "feature_index": feature_index,
                            "x": float(point.x),
                            "y": float(point.y),
                            "reason": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    next_pieces.append(piece)
                    continue
                parts = [g for g in result.geoms if isinstance(g, LineString) and g.length > 0]
                next_pieces.extend(parts or [piece])
            pieces = next_pieces
        added += max(0, len(pieces) - 1)
        out.extend((feature_index, piece) for piece in pieces)
    return out, added, split_failures
