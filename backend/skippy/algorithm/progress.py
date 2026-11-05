"""Request-scoped console progress, independent of HTTP and numerical results.

API/CLI entry points opt in with ``console_progress``. Direct library calls are
quiet. Interactive terminals share one tqdm line; each request leaves only one
summary. Redirected output contains final summaries without intermediate states.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from itertools import count
import os
import sys
from time import perf_counter
from .terminal_progress import ProgressOutput

_reporter = ContextVar("skippy_progress", default=None)
_step = ContextVar("skippy_progress_step", default=None)
_sequence = count(1)


def _text(value):
    return " ".join("".join(c for c in str(value) if c.isprintable() or c.isspace()).split())


class ConsoleProgress:
    def __init__(self, operation, request_id="", detail="", *, stream=None,
                 enabled=None, interval=None, interactive=None):
        self.enabled = (os.getenv("SKIPPY_PROGRESS", "1").strip().lower() not in
                        {"0", "false", "off", "no"}) if enabled is None else enabled
        if interval is None:
            try:
                interval = float(os.getenv("SKIPPY_PROGRESS_INTERVAL", "0.5"))
            except ValueError:
                interval = .5
        self.interval = min(60., max(.05, interval))
        self.stream = stream if stream is not None else sys.stderr
        self.operation = _text(operation)
        identifier = _text(request_id)[:12]
        self.number = next(_sequence)
        self.tag = f"Skippy #{self.number:04d}" + (f" {identifier}" if identifier else "")
        self.started = perf_counter()
        self.failure_step = ""
        self.failure_task = None
        self.steps = []
        self.detail = _text(detail)
        self.output = ProgressOutput(self, interactive=interactive)


class ProgressStep:
    def __init__(self, reporter, name, total=None):
        self.reporter = reporter
        self.name = _text(name)
        parent = _step.get()
        self.parent = parent
        self.children = 0
        if parent is not None:
            parent.children += 1
        self.path = f"{parent.path} / {self.name}" if parent is not None else self.name
        self.total = total
        self.completed = 0
        self.detail = ""
        self.started = perf_counter()
        self.last_update = self.started
        self.finished = False
        self.status = "开始"
        self.elapsed = 0.

    def _write(self, status):
        self.status = status
        self.elapsed = perf_counter()-self.started
        if self.reporter is None or not self.reporter.enabled:
            return
        self.reporter.output.render(self)

    def update(self, completed=None, *, total=None, detail=None):
        if self.finished:
            return
        if completed is not None:
            self.completed = completed
        if total is not None:
            self.total = total
        if detail is not None:
            self.detail = _text(detail)
        if self.reporter is None or not self.reporter.enabled:
            return
        now = perf_counter()
        if now-self.last_update >= self.reporter.interval:
            self.last_update = now
            self._write("进行")

    def advance(self, amount=1, *, detail=None):
        self.update(self.completed+amount, detail=detail)

    def finish(self, detail=None, *, total=None):
        if self.finished:
            return
        if total is not None:
            self.total = total
        if self.total is not None:
            self.completed = self.total
        if detail is not None:
            self.detail = _text(detail)
        self.finished = True
        self._write("完成")

    def fail(self, error):
        if self.finished:
            return
        self.finished = True
        self.detail = f"{getattr(error, 'status', type(error).__name__)}: {_text(error)}"
        if self.reporter is not None:
            self.reporter.failure_step = self.reporter.failure_step or self.path
            self.reporter.failure_task = self.reporter.failure_task or self
        self._write("失败")


@contextmanager
def stage(name, total=None):
    task = ProgressStep(_reporter.get(), name, total)
    if task.reporter is not None:
        task.reporter.steps.append(task)
    token = _step.set(task)
    task._write("开始")
    try:
        yield task
    except BaseException as error:
        task.fail(error)
        raise
    else:
        task.finish()
    finally:
        _step.reset(token)


def current_step():
    """The innermost stage; a silent step when called outside a stage."""
    return _step.get() or ProgressStep(None, "")


def progress_iter(iterable, total=None):
    """Advance only after an item has actually finished, including continues."""
    task = current_step()
    if total is None:
        total = len(iterable)
    task.update(0, total=total)
    for index, item in enumerate(iterable, 1):
        yield item
        task.update(index)


def tracked(name):
    """Instrument a whole function while preserving its public signature."""
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            with stage(name):
                return function(*args, **kwargs)
        return run
    return decorate


@contextmanager
def console_progress(operation="求解", request_id="", detail="", **kwargs):
    reporter = ConsoleProgress(operation, request_id, detail, **kwargs)
    token = _reporter.set(reporter)
    step_token = _step.set(None)
    try:
        yield reporter
    except BaseException as error:
        reporter.output.finish(error)
        raise
    else:
        reporter.output.finish()
    finally:
        _step.reset(step_token)
        _reporter.reset(token)
