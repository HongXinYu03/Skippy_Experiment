import itertools
import numpy as np
import pytest
from fastapi.testclient import TestClient
from skippy.server.api import app
from skippy.server.schemas import SolveRequest
from skippy.algorithm.models import Shape, SolveError, RedrawInput
from skippy.algorithm.geometry import Scene
from skippy.algorithm.stroke import rays, project, prepare
from skippy.algorithm.graph import shortest_path, sigmoid
from skippy.algorithm.smoothing import energy_gradient, operators
from skippy.algorithm.fixtures import camera, example
from skippy.algorithm.pipeline import solve, redraw

def test_projection_ray_roundtrip():
    c = camera((4, 3, 7))
    points = np.array([[.4, .2, -.1], [-.4, .3, .5], [0, 0, 0]])
    origin, directions = rays(c, project(c, points))
    np.testing.assert_allclose(np.cross(directions, points-origin), 0, atol=1e-12)

def test_primitive_distances_and_all_intersections():
    for kind in ["sphere", "box", "tetrahedron"]:
        scene = Scene([Shape(id="one", kind=kind)])
        origin = scene.normalize([0, 0, 5])
        intervals = scene.intervals(origin, np.array([0., 0., -1.]))
        assert len(intervals) == 1
        for depth in intervals[0][:2]:
            assert abs(scene.distance(origin+depth*np.array([0, 0, -1]))[0]) < 2e-7
        assert scene.distance(scene.normalize([0, 0, 0]))[0] < 0

def test_offset_intersections_are_euclidean():
    for kind in ["box", "tetrahedron", "sphere"]:
        scene = Scene([Shape(id="one", kind=kind)])
        origin = scene.normalize([.15, .1, 5])
        direction = np.array([0., 0., -1.])
        intervals = scene.intervals(origin, direction, .08)
        assert intervals
        for depth in intervals[0][:2]:
            assert abs(scene.distance(origin+depth*direction)[0]-.08) < 2e-7

def test_hidden_union_roots_are_removed():
    scene = Scene([Shape(id="a", kind="sphere"), Shape(id="b", kind="sphere", position=(0, 0, -.5))])
    assert len(scene.intervals(scene.normalize([0, 0, 5]), np.array([0., 0., -1.]))) == 1

def test_height_is_constant_off_and_interpolated_on():
    request = example()
    scene = Scene(request.objects)
    p = np.array(request.points)
    _, _, on, h, sequences, _ = prepare(scene, request.camera, p)
    assert np.any(on) and np.any(~on)
    for sequence in sequences:
        values = h[sequence["start"]:sequence["end"]+1]
        if not sequence["on"]:
            assert np.ptp(values) < 1e-12
        elif len(values) > 2:
            np.testing.assert_allclose(np.diff(values, n=2), 0, atol=1e-10)

def test_dag_matches_exhaustive_paths():
    costs = [2, 3, 1, 2, 1]
    edges = {(0, 1): 4, (0, 2): 1, (1, 3): 1, (2, 3): 6, (2, 4): 2, (3, 4): 1}
    path, value = shortest_path(costs, edges, {0: costs[0]}, {4: .5})
    all_paths = []
    for n in range(5):
        for middle in itertools.combinations(range(1, 4), n):
            candidate = [0, *middle, 4]
            if all((a, b) in edges for a, b in zip(candidate[:-1], candidate[1:])):
                all_paths.append(sum(costs[i] for i in candidate)+sum(edges[a, b] for a, b in zip(candidate[:-1], candidate[1:]))+.5)
    assert value == min(all_paths)
    assert path == [0, 2, 4]

def test_sigmoid_exact_endpoints_and_midpoint():
    np.testing.assert_allclose(sigmoid(np.array([0, .5, 1])), [0, .5, 1])

def test_energy_gradient_matches_finite_difference():
    scene = Scene([Shape(id="sphere", kind="sphere")])
    p = np.array([[-.35, .1, .65], [-.1, .17, .7], [.15, .12, .72], [.35, .1, .64]])
    origin = np.array([0., 0., 3.])
    targets = (p-origin)/np.linalg.norm(p-origin, axis=1)[:, None]
    h = scene.distance(p)+.01
    first, second = operators(len(p), False)
    _, grad = energy_gradient(p, scene, origin, targets, h, first, second)
    finite = np.zeros_like(p)
    for i in range(len(p)):
        for j in range(3):
            delta = np.zeros_like(p)
            delta[i, j] = 1e-6
            plus, _ = energy_gradient(p+delta, scene, origin, targets, h, first, second)
            minus, _ = energy_gradient(p-delta, scene, origin, targets, h, first, second)
            finite[i, j] = (plus-minus)/2e-6
    np.testing.assert_allclose(grad, finite, rtol=1e-4, atol=1e-6)

@pytest.mark.parametrize("kind", ["sphere", "box", "tetrahedron", "two-spheres", "loop", "discontinuity"])
def test_examples_are_feasible_and_preserve_projection(kind):
    request = example(kind)
    result = solve(request)
    assert result["status"] == "ok"
    assert result["metrics"]["projection_p95_px"] < 5
    assert result["metrics"]["height_p95_normalized"] < .01
    scene = Scene(request.objects)
    assert scene.clear(scene.normalize(result["points"]), request.options.collision_tolerance)
    trace = result["debug"]["smoothing"]["energies"]
    assert all(b <= a+1e-10 for a, b in zip(trace[:-1], trace[1:]))
    if kind == "loop":
        np.testing.assert_allclose(result["points"][0], result["points"][-1], atol=1e-10)

def test_skip_changes_real_depth_and_reuses_candidates():
    request = example()
    first = solve(request)
    alternatives = [t for t in first["debug"]["tracks"] if not t["selected"]]
    assert alternatives
    request.locks = {alternatives[0]["group"]: alternatives[0]["id"]}
    second = solve(request)
    assert alternatives[0]["id"] in second["selected_tracks"]
    assert second["metrics"]["cache_hit"]
    assert abs(np.mean(np.array(first["points"])[:, 2])-np.mean(np.array(second["points"])[:, 2])) > .2

def test_empty_and_all_off_are_explicit_failures():
    request = example()
    request.objects = []
    with pytest.raises(SolveError, match="几何体"):
        solve(request)
    request = example()
    request.points = [(10, 10), (50, 20)]
    with pytest.raises(SolveError):
        solve(request)

def test_http_validation_and_revision_echo():
    client = TestClient(app)
    assert client.get("/api/health").json()["implementation"] == "python"
    request = SolveRequest.model_validate(example().model_dump())
    request.revision, request.request_id = 17, "test-17"
    response = client.post("/api/solve", json=request.model_dump())
    assert response.status_code == 200
    assert response.json()["revision"] == 17
    assert response.json()["request_id"] == "test-17"
    payload = request.model_dump()
    payload["camera"]["world"] = [0]*16
    assert client.post("/api/solve", json=payload).status_code == 422

def test_depth_split_does_not_reset_height_to_zero():
    from skippy.algorithm.pipeline import snapshot
    data, _ = snapshot(example("discontinuity"))
    assert data["cuts"]
    for cut in data["cuts"]:
        assert data["heights"][cut] > 0
        assert abs(data["heights"][cut]-data["heights"][cut-1]) < .01

def test_redraw_from_second_view_fixes_endpoints_and_preserves_other_parts():
    request = example()
    first = solve(request)
    original = np.array(first["points"])
    start, end = len(original)//4, 3*len(original)//4
    new_camera = camera((.8, .2, 6))
    replacement = original[start:end+1].copy()
    replacement[:, 1] += .01*np.sin(np.linspace(0, np.pi, len(replacement)))
    request.camera = new_camera
    request.points = project(new_camera, replacement).tolist()
    second = redraw(RedrawInput(solve=request, original=original.tolist(), start=start, end=end))
    actual = np.array(second["points"])
    np.testing.assert_allclose(actual[:start+1], original[:start+1], atol=1e-10)
    np.testing.assert_allclose(actual[-(len(original)-end):], original[end:], atol=1e-10)
    assert second["metric_scope"] == "replacement_segment"
    scene = Scene(request.objects)
    assert scene.clear(scene.normalize(actual), request.options.collision_tolerance)

def test_excluding_a_guide_keeps_existing_result_independent():
    request = example()
    request.objects[0].guide = True
    original = solve(request)
    copied_points = np.array(original["points"])
    request.objects = []
    with pytest.raises(SolveError):
        solve(request)
    np.testing.assert_array_equal(original["points"], copied_points)

def test_single_corner_selects_paper_sigmoid_interpolation():
    from skippy.algorithm.graph import corners, interpolate
    from skippy.algorithm.geometry import unit
    pixels = np.array([[0, 0], [1, 1], [2, 2], [3, 1], [4, 0]], float)
    assert len(corners(pixels)) == 1
    direction = unit(np.column_stack([pixels*.05, np.ones(len(pixels))]))
    p = interpolate(np.zeros(3), direction, pixels, 0, 4, direction[0]*2, direction[-1]*5)
    expected = 2+3*(.5-.5*np.sqrt(.75))
    assert np.linalg.norm(p[1]) == pytest.approx(expected)

def test_candidate_budget_is_explicit_and_not_a_silent_beam_search():
    request = example("discontinuity")
    request.options.max_segments = 20
    with pytest.raises(SolveError) as error:
        solve(request)
    assert error.value.status == "budget_exceeded"

def test_websocket_echoes_input_version():
    client = TestClient(app)
    request = SolveRequest.model_validate(example().model_dump())
    request.request_id, request.revision = "stream-test", 42
    with client.websocket_connect("/api/stream") as websocket:
        websocket.send_json(request.model_dump())
        result = websocket.receive_json()
    assert result["status"] == "ok"
    assert result["request_id"] == "stream-test" and result["revision"] == 42
