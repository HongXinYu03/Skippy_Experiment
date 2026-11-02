import json
from .fixtures import example
from .pipeline import snapshot
from .graph import outer, interpolate
import numpy as np

for kind in ["tetrahedron", "discontinuity"]:
    data, _ = snapshot(example(kind))
    print(json.dumps({"kind": kind, "sequences": data["sequences"], "cuts": data["cuts"],
        "refinements": data["refinements"], "rejected": data["rejected"],
        "samples": len(data["pixels"]), "candidates": [len(c) for c in data["candidates"]],
        "segments": [{"id": s.id, "start": s.start, "end": s.end, "ranks": s.ranks} for s in data["segments"]]}, ensure_ascii=True))
    if kind == "discontinuity":
        for s in data["segments"]:
            if s.start == np.flatnonzero(data["on"])[0] and all(r == 0 for r in s.ranks):
                leading = outer(data["origin"], data["directions"], s, True)
                print("START", s.id, data["scene"].clear(leading))
                for t in data["segments"]:
                    if t.group != 1:
                        continue
                    p = interpolate(data["origin"], data["directions"], data["pixels"], s.end, t.start, s.points[-1], t.points[0])
                    ending = outer(data["origin"], data["directions"], t, False)
                    print("EDGE", s.id, t.id, "clear", data["scene"].clear(p), "end", data["scene"].clear(ending), "min", np.min(data["scene"].distance(p)))
