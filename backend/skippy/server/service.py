"""Adapt transport requests to algorithm inputs and attach response metadata."""
from ..algorithm.fixtures import example
from ..algorithm.models import SolveError
from ..algorithm.pipeline import redraw, solve
from ..algorithm.progress import console_progress, stage
from .schemas import RedrawRequest, SolveRequest


def _evaluate(function, request, context: SolveRequest):
    try:
        faces = sum(len(o.mesh.faces) for o in context.objects if o.mesh is not None)
        detail = (f"物体 {len(context.objects)} | 三角面 {faces} | 笔触点 {len(context.points)}"
                  f" | 平滑迭代上限 {context.options.iterations} | 场景版本 {context.revision}")
        with console_progress("局部重画" if function is redraw else "曲线求解", context.request_id, detail):
            with stage("请求数据转换"):
                algorithm_input = request.to_algorithm()
            result = function(algorithm_input)
    except SolveError as error:
        result = {"status": error.status, "message": error.message}
    return {**result, "revision": context.revision, "request_id": context.request_id}


def solve_curve(request: SolveRequest):
    return _evaluate(solve, request, request)


def redraw_curve(request: RedrawRequest):
    return _evaluate(redraw, request, request.solve)


def example_request(kind: str, width: int, height: int):
    # Preserve the existing API example envelope, including default metadata.
    return SolveRequest.model_validate(example(kind, width, height).model_dump()).model_dump()
