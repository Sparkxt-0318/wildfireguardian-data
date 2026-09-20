"""Run WG-BM-005..008 (graph connectivity) from wildfireguardian-benchmarks against this repository.

Phase 2 item 32. Reproducible, and deliberately NOT part of the test suite:
it needs ``wildfireguardian-benchmarks`` checked out beside this repository,
and ``AGENTS.md`` section 7 keeps the test suite free of external dependencies.
Results and their interpretation: ``reports/BENCHMARK_CROSS_CHECK.md``.

Usage (from the repository root, with the benchmarks repo cloned):

    BENCHMARKS=/path/to/wildfireguardian-benchmarks \
        python experiments/run_benchmark_graph.py
"""

import os
import sys
from pathlib import Path

import networkx as nx
import yaml
from shapely.geometry import LineString

_ROOT = Path(os.environ.get("BENCHMARKS", "/home/user/sparkxt-0318/wildfireguardian-benchmarks"))
BM = _ROOT / "benchmarks" / "routing"
sys.path.insert(0, "src")

# ruff: noqa: E402 -- the package imports below need sys.path set first.
from wildfireguardian_data.provenance.models import (
    NOT_APPLICABLE,
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
)
from wildfireguardian_data.roads.graph import build_road_graph
from wildfireguardian_data.roads.qa import bridge_edges
from wildfireguardian_data.vector import Feature, VectorLayer

CRS = "EPSG:5187"

def road_layer(graph_spec):
    xy = {n["id"]: (float(n["x"]), float(n["y"])) for n in graph_spec["nodes"]}
    feats = []
    for e in graph_spec["edges"]:
        feats.append(Feature(LineString([xy[e["from"]], xy[e["to"]]]),
                             {"road_id": e["id"], "highway": "unclassified"}))
    prov = ProvenanceRecord(
        layer_name="bm_roads", data_class=DataClass.SYNTHETIC,
        temporal_class=TemporalProvenance.STATIC, temporal_reference=NOT_APPLICABLE,
        sources=(SourceRecord(name="wildfireguardian-benchmarks", url_or_identifier="WG-BM"),),
        output_crs=CRS, value_unit=NOT_APPLICABLE, nodata_representation=NOT_APPLICABLE,
        resolution_unit=NOT_APPLICABLE, vertical_datum=NOT_APPLICABLE,
        surface_model=NOT_APPLICABLE, valid_from=NOT_APPLICABLE, valid_to=NOT_APPLICABLE,
    )
    return VectorLayer(name="bm_roads", features=tuple(feats), crs=CRS, provenance=prov), xy

def run(case):
    d = BM / case
    bm = yaml.safe_load((d/"benchmark.yaml").read_text())
    exp = yaml.safe_load((d/"expected"/"expected.yaml").read_text())["results"]
    gspec = yaml.safe_load((d/"inputs"/"network.yaml").read_text())
    layer, xy = road_layer(gspec)
    rg = build_road_graph(layer)
    G = rg.simple_graph()
    # map our node ids back to benchmark node names by coordinate
    name_by_xy = {(round(x,6), round(y,6)): n for n, (x, y) in xy.items()}
    ours_name = {}
    for node in G.nodes:
        x, y = rg.node_xy(node)
        ours_name[node] = name_by_xy.get((round(x,6), round(y,6)), f"?{node}")

    got = {
        "node_count": rg.node_count,
        "edge_count": rg.edge_count,
        "components": nx.number_connected_components(G),
    }
    if "articulation_points" in exp:
        got["articulation_points"] = sorted(
            ours_name[n] for n in nx.articulation_points(G))
    if "bridges" in exp:
        got["bridges"] = sorted(
            tuple(sorted((ours_name[a], ours_name[b]))) for a, b in bridge_edges(rg))
    checks = []
    for key in ("node_count", "edge_count", "components", "articulation_points"):
        if key in exp and key in got:
            want = sorted(exp[key]) if isinstance(exp[key], list) else exp[key]
            checks.append((key, want, got[key], want == got[key]))
    if "bridges" in exp:
        # Expected bridges are EDGE IDS; ours are node pairs. Compare counts only.
        checks.append(("bridge_count", len(exp["bridges"]), len(got["bridges"]),
                       len(exp["bridges"]) == len(got["bridges"])))
    # reachability of destinations, where the graph is undirected
    dests = exp.get("destinations", {})
    src = yaml.safe_load((d/"inputs"/"query.yaml").read_text()).get("source") \
        if (d/"inputs"/"query.yaml").exists() else None
    if src and dests:
        rev = {v: k for k, v in ours_name.items()}
        for dest, info in dests.items():
            if src not in rev or dest not in rev:
                continue
            reach = nx.has_path(G, rev[src], rev[dest])
            checks.append((f"reachable.{dest}", info["reachable"], reach,
                           info["reachable"] == reach))
    return bm["benchmark_id"], bm["title"], checks

total = 0
passed = 0
for case in sorted(p.name for p in BM.iterdir() if p.is_dir() and p.name.split("-")[2].split("_")[0] in {"005","006","007","008"}):
    try:
        bid, title, checks = run(case)
    except Exception as exc:
        print(f"{case}: COULD NOT RUN: {type(exc).__name__}: {exc}")
        continue
    bad = [c for c in checks if not c[3]]
    total += len(checks)
    passed += len(checks) - len(bad)
    print(f"{bid}  {'PASS' if not bad else f'FAIL ({len(bad)})'}  {title}")
    for key, want, have, ok in checks:
        mark = " " if ok else "X"
        print(f"   {mark} {key}: expected {want!r}, got {have!r}")
print(f"\ncheckable assertions: {passed}/{total} matched")
