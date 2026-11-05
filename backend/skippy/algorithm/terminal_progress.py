"""One shared live terminal line, with one permanent summary per request."""
from collections import OrderedDict
import logging
import os
import re
import shutil
from threading import RLock
from time import perf_counter, strftime

_lock = RLock()
_lines = {}
_logging_redirect = None


def _safe_write(stream, text):
    try:
        stream.write(text)
        stream.flush()
    except UnicodeEncodeError:
        try:
            encoding = getattr(stream, "encoding", None) or "ascii"
            stream.write(text.encode(encoding, "backslashreplace").decode(encoding))
            stream.flush()
        except (OSError, UnicodeError, ValueError):
            pass
    except (OSError, ValueError):
        pass


def _fraction(task):
    if task is None:
        return 0.
    if task.status == "完成":
        return 1.
    return min(1., max(0., task.completed/task.total)) if task.total else 0.


def _redirect_logging():
    global _logging_redirect
    if _logging_redirect is not None:
        return
    from tqdm.contrib.logging import logging_redirect_tqdm
    # Redirect handlers only where they already exist, preserving propagation.
    loggers = [logging.getLogger(name) for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access")]
    loggers = [logger for logger in loggers if logger.handlers]
    _logging_redirect = logging_redirect_tqdm(loggers=loggers)
    _logging_redirect.__enter__()


def _restore_logging():
    global _logging_redirect
    if not _lines and _logging_redirect is not None:
        _logging_redirect.__exit__(None, None, None)
        _logging_redirect = None


class _LiveLine:
    def __init__(self, stream):
        from tqdm import tqdm
        from tqdm.utils import disp_len, disp_trim
        self.tqdm, self.disp_len, self.disp_trim = tqdm, disp_len, disp_trim
        self.stream = stream
        self.active = OrderedDict()
        self.bar = None

    def draw(self):
        if not self.active:
            return
        output = next(reversed(self.active.values()))
        reporter, task = output.reporter, output.task
        columns = max(35, shutil.get_terminal_size((100, 20)).columns-1)
        status = task.status if task is not None else "准备"
        name = task.name if task is not None else reporter.operation
        elapsed = task.elapsed if task is not None else perf_counter()-reporter.started
        fraction = _fraction(task)
        percent = f"{fraction:5.1%}" if task is not None and (task.total is not None or task.finished) else "  --%"
        counts = f" {task.completed}/{task.total}" if task is not None and task.total is not None else ""
        postfix = f"{percent}{counts} {elapsed:.1f}s"
        detail = task.detail if task is not None else ""
        # Keep the entire bar shorter than the terminal width, including CJK.
        description = f"[#{reporter.number:04d}] {status} {name}"
        room = max(8, columns-self.disp_len(postfix)-18)
        description = self.disp_trim(description, room)
        remaining = columns-self.disp_len(description)-self.disp_len(postfix)-18
        if detail and remaining > 5:
            postfix += " | "+self.disp_trim(detail, remaining-3)
        if self.bar is None:
            self.bar = self.tqdm(total=100, file=self.stream, leave=False, position=0,
                                 ncols=columns, ascii=True, mininterval=reporter.interval,
                                 bar_format="{desc} |{bar:12}| {postfix[0]}",
                                 desc=description, postfix=[postfix], initial=100*fraction)
        else:
            self.bar.ncols = columns
            self.bar.n = 100*fraction
            self.bar.set_description_str(description, refresh=False)
            self.bar.postfix = [postfix]
            self.bar.refresh()

    def close_bar(self):
        if self.bar is not None:
            self.bar.close()
            self.bar = None

    def finish(self, output, text):
        self.close_bar()
        self.active.pop(output.reporter.number, None)
        # tqdm.write leaves one completed line and safely clears other bars.
        from tqdm.utils import disp_trim
        columns = max(35, shutil.get_terminal_size((100, 20)).columns-1)
        self.tqdm.write(disp_trim(text, columns), file=self.stream)
        self.draw()


class ProgressOutput:
    def __init__(self, reporter, interactive=None):
        self.reporter = reporter
        self.task = None
        self.line = None
        if not reporter.enabled:
            return
        mode = os.getenv("SKIPPY_PROGRESS_MODE", "auto").strip().lower()
        if interactive is None:
            try:
                interactive = mode == "inline" or (mode != "plain" and reporter.stream.isatty())
            except (AttributeError, OSError, ValueError):
                interactive = False
        if interactive:
            with _lock:
                try:
                    key = id(reporter.stream)
                    self.line = _lines.get(key)
                    if self.line is None:
                        self.line = _LiveLine(reporter.stream)
                        _lines[key] = self.line
                        _redirect_logging()
                    self.line.active[reporter.number] = self
                    self.line.draw()
                except (ImportError, OSError, UnicodeError, ValueError):
                    self._detach()

    def _detach(self):
        if self.line is None:
            return
        self.line.active.pop(self.reporter.number, None)
        if not self.line.active:
            try:
                self.line.close_bar()
            except (OSError, UnicodeError, ValueError):
                pass
            _lines.pop(id(self.reporter.stream), None)
        self.line = None
        _restore_logging()

    def render(self, task):
        self.task = task
        if self.line is None:
            return
        with _lock:
            try:
                # Older concurrent requests keep their state without taking
                # the live line away from the most recently started request.
                if next(reversed(self.line.active.values())) is self:
                    self.line.draw()
            except (OSError, UnicodeError, ValueError):
                self._detach()

    def finish(self, error=None):
        reporter = self.reporter
        if not reporter.enabled:
            return
        status = "完成" if error is None else "失败"
        task = reporter.failure_task if error is not None else None
        fraction = _fraction(task) if error is not None else 1.
        filled = int(12*fraction)
        bar = "#"*filled+"-"*(12-filled)
        counts = f" {task.completed}/{task.total}" if task is not None and task.total is not None else ""
        text = (f"{strftime('%H:%M:%S')} [{reporter.tag}] {status} {reporter.operation}"
                f" |{bar}| {fraction:5.1%}{counts} | 总耗时 {perf_counter()-reporter.started:.2f}s")
        if error is not None:
            message = " ".join(str(error).split())
            text += f" | {getattr(error, 'status', type(error).__name__)}: {message}"
            if task is not None:
                text += f" | 步骤: {task.name}"
        else:
            if any("缓存命中" in step.detail for step in reporter.steps):
                text += " | 缓存命中"
            for step in reporter.steps:
                if step.name == "平滑迭代":
                    match = re.search(r"有效迭代 \d+/\d+", step.detail)
                    if match:
                        text += f" | {match.group()}"
        with _lock:
            line = self.line or _lines.get(id(reporter.stream))
            if line is not None:
                try:
                    line.finish(self, text)
                except (OSError, UnicodeError, ValueError):
                    _safe_write(reporter.stream, text+"\n")
                finally:
                    self._detach()
            else:
                # Files, pipes and non-interactive IDE panes get one summary,
                # never a stream of carriage returns or repeated state lines.
                _safe_write(reporter.stream, text+"\n")
