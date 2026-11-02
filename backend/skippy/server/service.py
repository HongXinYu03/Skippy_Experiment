"""Adapt transport requests to algorithm inputs and attach response metadata."""
from ..algorithm.fixtures import example
from ..algorithm.models import SolveError
from ..algorithm.pipeline import redraw, solve
from .schemas import RedrawRequest, SolveRequest


def _evaluate(function, algorithm_input, context: SolveRequest):
    try:
        result = function(algorithm_input)
    except SolveError as error:
        result = {"status": error.status, "message": error.message}
    return {**result, "revision": context.revision, "request_id": context.request_id}


def solve_curve(request: SolveRequest):
    return _evaluate(solve, request.to_algorithm(), request)


def redraw_curve(request: RedrawRequest):
    return _evaluate(redraw, request.to_algorithm(), request.solve)


def example_request(kind: str, width: int, height: int):
    # Preserve the existing API example envelope, including default metadata.
    return SolveRequest.model_validate(example(kind, width, height).model_dump()).model_dump()
