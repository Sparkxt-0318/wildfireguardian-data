"""Run WG-BM-001..004 (terrain) from wildfireguardian-benchmarks against this repository.

Phase 2 item 32. Reproducible, and deliberately NOT part of the test suite:
it needs ``wildfireguardian-benchmarks`` checked out beside this repository,
and ``AGENTS.md`` section 7 keeps the test suite free of external dependencies.
Results and their interpretation: ``reports/BENCHMARK_CROSS_CHECK.md``.

Usage (from the repository root, with the benchmarks repo cloned):

    BENCHMARKS=/path/to/wildfireguardian-benchmarks \
        python experiments/run_benchmark_terrain.py
"""

import os
import sys
from pathlib import Path

import numpy as np
import yaml

_ROOT = Path(os.environ.get("BENCHMARKS", "/home/user/sparkxt-0318/wildfireguardian-benchmarks"))
BM = _ROOT / "benchmarks" / "terrain"
sys.path.insert(0, "src")

# ruff: noqa: E402 -- the package imports below need sys.path set first.
from wildfireguardian_data.provenance.models import (
    NOT_APPLICABLE,
    DataClass,
    ProvenanceRecord,
    SourceRecord,
    TemporalProvenance,
)
from wildfireguardian_data.raster import GridTransform, RasterKind, RasterLayer
from wildfireguardian_data.terrain.derivatives import aspect, slope


def layer(elev, cell):
    arr = np.array([[np.nan if v is None else float(v) for v in row] for row in elev])
    h, w = arr.shape
    prov = ProvenanceRecord(
        layer_name="bm_dem", data_class=DataClass.SYNTHETIC,
        temporal_class=TemporalProvenance.STATIC, temporal_reference=NOT_APPLICABLE,
        sources=(SourceRecord(name="wildfireguardian-benchmarks", url_or_identifier="WG-BM"),),
        output_crs="EPSG:5187", value_unit="m", nodata_representation="nan",
        resolution_unit="metre", surface_model="dtm",
        valid_from=NOT_APPLICABLE, valid_to=NOT_APPLICABLE,
    )
    return RasterLayer(name="bm_dem", data=arr,
        transform=GridTransform(0.0, h*cell, cell, cell), crs="EPSG:5187",
        nodata=float("nan"), kind=RasterKind.CONTINUOUS, value_unit="m", provenance=prov)

def run(case):
    inp = yaml.safe_load((BM/case/"inputs"/"terrain.yaml").read_text())
    exp = yaml.safe_load((BM/case/"expected"/"expected.yaml").read_text())["results"]
    bm  = yaml.safe_load((BM/case/"benchmark.yaml").read_text())
    tol = bm.get("tolerance", {}).get("default", 1e-9)
    dem = layer(inp["elevation"], float(inp["cell_size_m"]))
    s, a = slope(dem), aspect(dem)
    h, w = dem.shape
    interior = (slice(1, h-1), slice(1, w-1))
    si = s.data[interior]
    sm = s.valid_mask()[interior]
    got = {
        "rows": h, "cols": w,
        "interior_cells": int(si.size),
        "interior_defined": int(sm.sum()),
        "interior_undefined": int((~sm).sum()),
        "flat_interior_cells": int(np.sum(sm & (si == 0.0))),
        "max_slope_deg": float(np.nanmax(si)) if sm.any() else None,
        "min_slope_deg": float(np.nanmin(si)) if sm.any() else None,
    }
    results = []
    for key in ("rows","cols","interior_cells","interior_defined","interior_undefined",
                "flat_interior_cells","max_slope_deg","min_slope_deg"):
        want, have = exp.get(key), got[key]
        if isinstance(want,(int,float)) and isinstance(have,(int,float)):
            ok = abs(want-have) <= max(tol, 1e-9)
        else:
            ok = want == have
        results.append((key, want, have, ok))
    # probes
    for probe in inp.get("probe_cells", []):
        r, c = probe["row"], probe["col"]
        pe = exp["probes"][probe["id"]]
        gs = None if not s.valid_mask()[r,c] else float(s.data[r,c])
        ga = None if (not a.valid_mask()[r,c]) else float(a.data[r,c])
        for field, want, have in (("slope_deg", pe["slope_deg"], gs),
                                  ("aspect_deg", pe["aspect_deg"], ga)):
            if want is None or have is None:
                ok = (want is None) == (have is None)
            else:
                ok = abs(want-have) <= max(tol, 1e-6)
            results.append((f"probe.{probe['id']}.{field}", want, have, ok))
    return bm["benchmark_id"], bm["title"], results

total = 0
passed = 0
for case in sorted(p.name for p in BM.iterdir() if p.is_dir()):
    bid, title, results = run(case)
    bad = [r for r in results if not r[3]]
    total += len(results)
    passed += len(results) - len(bad)
    flag = "PASS" if not bad else f"FAIL ({len(bad)})"
    print(f"{bid}  {flag}  {title}")
    for key, want, have, ok in results:
        if not ok:
            print(f"      {key}: expected {want!r}, got {have!r}")
print(f"\nassertions: {passed}/{total} matched")
