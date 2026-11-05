"""Offline replay of algorithm inputs or JSON exported by the frontend."""
import argparse
import json
from pathlib import Path
from .fixtures import example
from .models import SolveError, SolveInput
from .pipeline import solve
from .progress import console_progress


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay a Skippy case without a browser")
    parser.add_argument("input", nargs="?", help="Algorithm input or frontend export JSON")
    parser.add_argument("--example", default="sphere", choices=["sphere", "box", "tetrahedron", "two-spheres", "loop", "discontinuity"])
    parser.add_argument("--output", default="result.json")
    parser.add_argument("--export-input")
    args = parser.parse_args(argv)
    if args.input:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        # Frontend exports may include transport metadata; it is not algorithm input.
        payload.pop("revision", None)
        payload.pop("request_id", None)
        request = SolveInput.model_validate(payload)
    else:
        request = example(args.example)
    if args.export_input:
        Path(args.export_input).write_text(request.model_dump_json(indent=2), encoding="utf-8")
    try:
        with console_progress("离线曲线求解", detail=f"物体 {len(request.objects)} | 笔触点 {len(request.points)}"):
            result = solve(request)
    except SolveError as error:
        result = {"status": error.status, "message": error.message}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "metrics": result.get("metrics"), "output": args.output}, ensure_ascii=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
