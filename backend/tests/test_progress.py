"""Progress is truthful, request-local, optional, and numerically transparent."""
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from itertools import count
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier

import numpy as np
import pytest
from fastapi.testclient import TestClient

from skippy.algorithm import progress
from skippy.algorithm.fixtures import example
from skippy.algorithm.models import SolveError
from skippy.algorithm.pipeline import solve
from skippy.server.api import app
from skippy.server import service


class Terminal(StringIO):
    def isatty(self):
        return True


def terminal_rows(text):
    """Interpret carriage returns as a real terminal does."""
    rows, row = [], ''
    for character in text:
        if character == '\r':
            row = ''
        elif character == '\n':
            rows.append(row.rstrip())
            row = ''
        else:
            row += character
    return rows


def test_counts_advance_only_after_completed_work_and_early_finish_is_explicit(monkeypatch):
    clock = count()
    monkeypatch.setattr(progress, "perf_counter", lambda: next(clock)*.1)
    output = Terminal()
    with progress.console_progress("求解", "counts", stream=output, interval=.05):
        with progress.stage("候选", total=3) as task:
            for i in progress.progress_iter(range(3)):
                assert task.completed == i
        with progress.stage("平滑", total=80) as task:
            task.update(5)
            task.finish("有效迭代 5/80；提前收敛", total=5)
    text = output.getvalue()
    assert "33.3% 1/3" in text
    assert "66.7% 2/3" in text
    assert "100.0% 3/3" in text
    assert "100.0% 5/5" in text and "有效迭代 5/80；提前收敛" in text
    assert "80/80" not in text
    assert "完成 求解" in text and "总耗时" in text
    assert len(terminal_rows(text)) == 1


def test_fast_updates_are_throttled_and_final_counts_are_always_printed(monkeypatch):
    monkeypatch.setattr(progress, "perf_counter", lambda: 0.)
    output = Terminal()
    with progress.console_progress(stream=output):
        with progress.stage("射线", total=1000) as task:
            for _ in range(1000):
                task.advance()
    assert len(terminal_rows(output.getvalue())) == 1
    assert "1000/1000" in output.getvalue()


def test_failure_keeps_partial_progress_and_context_is_reset():
    output = StringIO()
    with pytest.raises(SolveError):
        with progress.console_progress("求解", stream=output):
            with progress.stage("碰撞检测", total=10) as task:
                task.update(3)
                raise SolveError("no_path", "无法连接")
    assert "失败 求解" in output.getvalue()
    assert "步骤: 碰撞检测" in output.getvalue()
    assert "30.0% 3/10" in output.getvalue()
    assert "no_path: 无法连接" in output.getvalue()
    assert "完成 求解" not in output.getvalue()
    before = output.getvalue()
    with progress.stage("后续无日志计算"):
        pass
    assert output.getvalue() == before


def test_concurrent_requests_have_separate_tags_and_stage_paths():
    output = Terminal()
    barrier = Barrier(2)

    def run(name):
        with progress.console_progress("求解", f"req-{name}", stream=output) as reporter:
            with progress.stage(f"阶段{name}", total=2) as task:
                task.advance()
                barrier.wait(timeout=5)
                with progress.stage(f"子步骤{name}"):
                    pass
                task.advance()
        return reporter

    with ThreadPoolExecutor(max_workers=2) as executor:
        reporters = list(executor.map(run, ['A', 'B']))
    lines = terminal_rows(output.getvalue())
    assert len(lines) == 2
    for reporter, (name, other) in zip(reporters, [('A', 'B'), ('B', 'A')]):
        own = [line for line in lines if f"req-{name}]" in line]
        assert len(own) == 1
        assert all(f"阶段{other}" not in step.path for step in reporter.steps)
        assert any(f"阶段{name} / 子步骤{name}" in step.path for step in reporter.steps)


def test_direct_library_and_disabled_reporting_are_quiet(monkeypatch, capsys):
    request = example()
    solve(request)
    monkeypatch.setenv("SKIPPY_PROGRESS", "0")
    with progress.console_progress("关闭的报告"):
        solve(request)
    assert capsys.readouterr().err == ""


def test_progress_does_not_change_curve_or_energy_and_reports_cache_hits():
    request = example('two-spheres')
    first = solve(request)
    output = StringIO()
    with progress.console_progress(stream=output) as reporter:
        second = solve(request)
    np.testing.assert_array_equal(first['points'], second['points'])
    np.testing.assert_array_equal(first['debug']['smoothing']['energies'], second['debug']['smoothing']['energies'])
    assert first['selected_tracks'] == second['selected_tracks']
    assert "缓存命中" in output.getvalue()
    assert any("跳过对应计算" in step.detail for step in reporter.steps)
    assert all("构建候选段" not in step.name for step in reporter.steps)


def test_http_requests_report_stages_and_preserve_error_status(capsys, monkeypatch):
    from contextlib import contextmanager
    reporters = []

    @contextmanager
    def observe(*args, **kwargs):
        with progress.console_progress(*args, **kwargs) as reporter:
            reporters.append(reporter)
            yield reporter

    monkeypatch.setattr(service, 'console_progress', observe)
    request = example().model_dump()
    request['options']['spacing'] = 11.237  # A fresh snapshot for this request.
    request.update(request_id='progress-http', revision=31)
    with TestClient(app) as client:
        result = client.post('/api/solve', json=request).json()
        assert result['status'] == 'ok'
        assert result['request_id'] == 'progress-http' and result['revision'] == 31
        output = capsys.readouterr().err
        phases = ' '.join(step.name for step in reporters[-1].steps)
        for name in ['请求数据转换', '场景几何准备', '二维笔触重采样', '射线求交',
                     'off 段离物距离', '候选顶点', '棱边与穿入细化', '构建候选段',
                     'DAG 最短路径', '平滑迭代', '最终曲线穿入验证', '投影与高度误差统计']:
            assert name in phases
        assert "完成 曲线求解" in output and "总耗时" in output
        assert len(output.splitlines()) == 1
        request['objects'] = []
        result = client.post('/api/solve', json=request).json()
        assert result['status'] == 'insufficient_geometry'
        output = capsys.readouterr().err
        assert "失败 曲线求解" in output and 'insufficient_geometry' in output
        assert "完成 曲线求解" not in output
        assert len(output.splitlines()) == 1


def test_cli_keeps_stdout_json_and_sends_progress_to_stderr(tmp_path):
    result_path = tmp_path / 'result.json'
    env = {**os.environ, 'SKIPPY_PROGRESS': '1', 'PYTHONIOENCODING': 'utf-8'}
    completed = subprocess.run([sys.executable, '-m', 'skippy', '--example', 'sphere',
                                '--output', str(result_path)], env=env,
                               cwd=Path(__file__).resolve().parents[1],
                               capture_output=True, encoding='utf-8', timeout=30)
    assert completed.returncode == 0
    assert json.loads(completed.stdout)['status'] == 'ok'
    assert json.loads(result_path.read_text(encoding='utf-8'))['status'] == 'ok'
    assert "完成 离线曲线求解" in completed.stderr
    assert "有效迭代" in completed.stderr
    assert len(completed.stderr.splitlines()) == 1


def test_zero_iteration_budget_is_reported_without_invented_iterations():
    request = example()
    request.options.iterations = 0
    output = StringIO()
    with progress.console_progress(stream=output) as reporter:
        result = solve(request)
    assert result['debug']['smoothing']['iterations'] == 0
    assert "有效迭代 0/0" in output.getvalue()
    assert any("迭代上限为 0" in step.detail for step in reporter.steps)


def test_broken_console_does_not_break_the_solver():
    class BrokenConsole:
        def write(self, text):
            raise OSError('Console has disconnected')

    with progress.console_progress(stream=BrokenConsole()):
        result = solve(example())
    assert result['status'] == 'ok'


def test_redirected_output_has_one_summary_and_no_carriage_return_spam(monkeypatch):
    clock = count()
    monkeypatch.setattr(progress, 'perf_counter', lambda: next(clock)*.1)
    output = StringIO()
    with progress.console_progress(stream=output):
        for name in ['射线', '候选', '平滑']:
            with progress.stage(name, total=100) as task:
                for _ in range(100):
                    task.advance()
    assert output.getvalue().count('\n') == 1
    assert '\r' not in output.getvalue()
    assert '100.0%' in output.getvalue()


def test_uvicorn_logs_leave_the_progress_line_intact_and_handlers_are_restored(monkeypatch):
    output = Terminal()
    monkeypatch.setattr(sys, 'stderr', output)
    logger = logging.getLogger('uvicorn')
    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter('INFO: %(message)s'))
    logger.handlers, logger.level, logger.propagate = [handler], logging.INFO, False
    try:
        with progress.console_progress(stream=output):
            with progress.stage('候选', total=3) as task:
                task.advance()
                logger.info('request accepted')
                task.advance(2)
        assert logger.handlers == [handler]
        rows = terminal_rows(output.getvalue())
        assert len(rows) == 2
        assert rows[0] == 'INFO: request accepted'
        assert '100.0%' in rows[1] and '完成 求解' in rows[1]
    finally:
        logger.handlers, logger.level, logger.propagate = old_handlers, old_level, old_propagate


def test_long_chinese_labels_do_not_wrap_to_new_lines(monkeypatch):
    from tqdm.utils import disp_len
    from skippy.algorithm import terminal_progress
    monkeypatch.setattr(terminal_progress.shutil, 'get_terminal_size', lambda *_: os.terminal_size((60, 20)))
    output = Terminal()
    with progress.console_progress(stream=output):
        with progress.stage('这是一个很长的中文阶段名称用于检查终端换行问题', total=100) as task:
            task.update(50, detail='回溯次数和当前能量也不能使进度信息超出终端宽度')
    for frame in output.getvalue().replace('\n', '\r').split('\r'):
        assert disp_len(frame) <= 59
    assert len(terminal_rows(output.getvalue())) == 1
