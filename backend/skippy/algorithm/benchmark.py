import json
import platform
from pathlib import Path
import numpy as np
from .fixtures import example
from .pipeline import solve
from .models import SolveError

results = []
for kind in ["sphere", "box", "tetrahedron", "two-spheres", "loop", "discontinuity", "stress"]:
    request = example(kind)
    try:
        first = solve(request)
        runs = [solve(request) for _ in range(5)]
        results.append({"case": kind, "status": "ok", "cold_ms": first["metrics"]["total_ms"],
                        "warm_p95_ms": float(np.quantile([r["metrics"]["total_ms"] for r in runs], .95)),
                        "metrics": runs[-1]["metrics"]})
    except SolveError as error:
        results.append({"case": kind, "status": error.status, "message": error.message})
report = {"python": platform.python_version(), "cpu": platform.processor(), "runs_per_case": 5, "cases": results}
Path("benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True, indent=2))
