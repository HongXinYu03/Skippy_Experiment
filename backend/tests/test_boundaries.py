"""Regression checks for the boundary between standalone inference and transport."""
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient
from skippy.algorithm import solve
from skippy.algorithm.fixtures import example
from skippy.algorithm.stroke import project
from skippy.server.api import app


@pytest.mark.parametrize("empty_scene", [False, True])
def test_offline_replay_never_loads_server_and_accepts_frontend_export(tmp_path, empty_scene):
    payload = example().model_dump()
    payload.update(request_id="frontend-export", revision=9)
    if empty_scene:
        payload["objects"] = []
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    exported = tmp_path / "algorithm-input.json"
    original_json = json.dumps(payload)
    source.write_text(original_json, encoding="utf-8")
    expected_exit = 1 if empty_scene else 0
    # Use a fresh process so HTTP imports in this pytest process cannot hide coupling.
    script = """
import runpy
import sys
sys.argv = ['skippy', *sys.argv[1:]]
try:
    runpy.run_module('skippy', run_name='__main__')
except SystemExit as exit_status:
    actual_exit = exit_status.code
else:
    raise AssertionError('CLI did not return an exit status')
assert actual_exit == EXPECTED_EXIT, actual_exit
assert not any(name == 'fastapi' or name.startswith('fastapi.') or
               name == 'skippy.server' or name.startswith('skippy.server.')
               for name in sys.modules)
""".replace("EXPECTED_EXIT", str(expected_exit))
    completed = subprocess.run(
        [sys.executable, "-c", script, str(source), "--output", str(output),
         "--export-input", str(exported)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == ("insufficient_geometry" if empty_scene else "ok")
    assert "request_id" not in result and "revision" not in result
    assert "request_id" not in json.loads(exported.read_text(encoding="utf-8"))
    assert source.read_text(encoding="utf-8") == original_json


def test_examples_preserve_transport_defaults_and_viewport():
    with TestClient(app) as client:
        response = client.get("/api/examples/sphere?width=800&height=600")
    assert response.status_code == 200
    payload = response.json()
    assert (payload["request_id"], payload["revision"]) == ("", 0)
    assert (payload["camera"]["width"], payload["camera"]["height"]) == (800, 600)


def test_algorithm_failure_is_adapted_with_frontend_metadata():
    payload = example().model_dump()
    payload.update(objects=[], request_id="empty-scene", revision=12)
    with TestClient(app) as client:
        response = client.post("/api/solve", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "insufficient_geometry"
    assert (result["request_id"], result["revision"]) == ("empty-scene", 12)


@pytest.mark.parametrize("invalid_range", [False, True])
def test_redraw_adapter_preserves_geometry_and_transport_metadata(invalid_range):
    request = example()
    original = np.array(solve(request)["points"])
    start, end = len(original)//4, 3*len(original)//4
    request.points = project(request.camera, original[start:end+1]).tolist()
    payload = {
        "solve": {**request.model_dump(), "request_id": "redraw-test", "revision": 27},
        "original": original.tolist(), "start": start,
        "end": len(original)+1 if invalid_range else end,
    }
    with TestClient(app) as client:
        response = client.post("/api/redraw", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert (result["request_id"], result["revision"]) == ("redraw-test", 27)
    if invalid_range:
        assert result["status"] == "invalid_range"
    else:
        assert result["status"] == "ok"
        actual = np.array(result["points"])
        np.testing.assert_allclose(actual[:start+1], original[:start+1], atol=1e-10)
        np.testing.assert_allclose(actual[-(len(original)-end):], original[end:], atol=1e-10)
