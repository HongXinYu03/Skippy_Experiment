"""Transport schemas, including frontend request and scene version tracking."""
from pydantic import Field
from ..algorithm.models import RedrawInput, SolveInput


class SolveRequest(SolveInput):
    revision: int = 0
    request_id: str = Field(default="", max_length=100)

    def to_algorithm(self) -> SolveInput:
        return SolveInput.model_validate(self.model_dump(exclude={"revision", "request_id"}))


class RedrawRequest(RedrawInput):
    solve: SolveRequest

    def to_algorithm(self) -> RedrawInput:
        return RedrawInput(solve=self.solve.to_algorithm(), original=self.original,
                           start=self.start, end=self.end)
