"""Imported meshes must use the complete Skippy inference pipeline."""
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from scipy.spatial.transform import Rotation
from scipy.spatial import ConvexHull
from skippy.algorithm.fixtures import camera
from skippy.algorithm.geometry import Scene
from skippy.algorithm.mesh import triangle_distances
from skippy.algorithm.models import MeshData, Shape, SolveInput, Options, RedrawInput
from skippy.algorithm.pipeline import solve, redraw
from skippy.algorithm.stroke import project
from skippy.server.api import app


def cube_mesh(offset=(0, 0, 0), radius=1.):
    vertices = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                         [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]])
    faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
             [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
             [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]]
    return MeshData(vertices=(vertices*radius+offset).tolist(), faces=faces)


def test_closed_mesh_matches_box_distances_and_offset_intervals():
    mesh = Scene([Shape(id="mesh", kind="mesh", mesh=cube_mesh())])
    box = Scene([Shape(id="box", kind="box", size=(2, 2, 2))])
    samples = np.random.default_rng(7).uniform(-2, 2, (150, 3))
    np.testing.assert_allclose(mesh.distance(mesh.normalize(samples))*mesh.scale,
                               box.distance(box.normalize(samples))*box.scale, atol=1e-10)
    assert mesh.objects[0].closed
    for point in ([.13, .21, 5], [1.05, 1.05, 5], [0, 0, 0]):
        for height in (0., .08):
            direction = np.array([0., 0., -1.])
            actual = mesh.intervals(mesh.normalize(point), direction, height/mesh.scale)
            expected = box.intervals(box.normalize(point), direction, height/box.scale)
            np.testing.assert_allclose(np.array([r[:2] for r in actual])*mesh.scale,
                                       np.array([r[:2] for r in expected])*box.scale, atol=1e-7)


def test_mesh_bvh_nearest_distance_matches_exhaustive_triangles():
    # Enough disconnected cubes to force several BVH levels, including a gap.
    vertices, faces = [], []
    for x in range(6):
        data = cube_mesh((x*3, 0, 0))
        faces.extend((np.asarray(data.faces)+len(vertices)).tolist())
        vertices.extend(data.vertices)
    scene = Scene([Shape(id="mesh", kind="mesh", mesh=MeshData(vertices=vertices, faces=faces))])
    mesh = scene.objects[0]
    points = scene.normalize(np.random.default_rng(17).uniform([-3, -3, -3], [20, 3, 3], (60, 3)))
    exact = [min(triangle_distances(np.repeat([p], len(mesh.triangles), axis=0), mesh.triangles)) for p in points]
    np.testing.assert_allclose(abs(mesh.distance(points)), exact, atol=1e-10)


def test_disconnected_mesh_keeps_multiple_depth_intervals():
    first, second = cube_mesh((0, 0, 2), .5), cube_mesh((0, 0, -2), .5)
    data = MeshData(vertices=first.vertices+second.vertices,
                    faces=first.faces+(np.asarray(second.faces)+8).tolist())
    scene = Scene([Shape(id="mesh", kind="mesh", mesh=data)])
    direction = np.array([0., 0., -1.])
    origin = scene.normalize([.12, .13, 6])
    for height in (0., .02):
        intervals = scene.intervals(origin, direction, height)
        assert len(intervals) == 2
        for row in intervals:
            for depth in row[:2]:
                assert abs(scene.distance(origin+depth*direction)[0]-height) < 1e-8


def test_mesh_respects_nonuniform_scale_rotation_and_translation():
    quaternion = Rotation.from_euler('y', 37, degrees=True).as_quat()
    spec = Shape(id="mesh", kind="mesh", mesh=cube_mesh(), size=(2, .7, .4),
                 quaternion=quaternion, position=(4, -2, 1))
    mesh = Scene([spec])
    box = Scene([Shape(id="box", kind="box", size=(4, 1.4, .8),
                       quaternion=quaternion, position=spec.position)])
    points = np.random.default_rng(19).uniform([0, -4, -1], [8, 0, 3], (100, 3))
    np.testing.assert_allclose(mesh.distance(mesh.normalize(points))*mesh.scale,
                               box.distance(box.normalize(points))*box.scale, atol=1e-9)


def test_open_mesh_is_two_sided_and_has_euclidean_offsets():
    data = MeshData(vertices=[[-1, -1, 0], [1, -1, 0], [0, 1, 0]], faces=[[0, 1, 2]])
    scene = Scene([Shape(id="sheet", kind="mesh", mesh=data)])
    mesh = scene.objects[0]
    assert not mesh.closed
    np.testing.assert_allclose(scene.distance(scene.normalize([[0, 0, 1], [0, 0, -1]])), [.5, .5])
    direction = np.array([0., 0., -1.])
    for point in ([0, 0, 3], [0, -1.1, 3], [1.05, -1.03, 3]):
        origin = scene.normalize(point)
        for interval in scene.intervals(origin, direction, .1):
            for depth in interval[:2]:
                assert abs(scene.distance(origin+depth*direction)[0]-.1) < 1e-8
    origin = scene.normalize([0, 0, -3])
    assert scene.intervals(origin, -direction)


def test_positive_ray_distance_matches_box_including_rays_pointing_away():
    mesh = Scene([Shape(id="mesh", kind="mesh", mesh=cube_mesh())])
    box = Scene([Shape(id="box", kind="box", size=(2, 2, 2))])
    for point, ray in [([2, 2, 4], [0, 0, -1]), ([3, 0, 0], [1, 0, 0]),
                       ([3, 2, 0], [-1, .4, 0]), ([0, 3, 4], [.2, -.1, -1])]:
        direction = np.asarray(ray, float)
        direction /= np.linalg.norm(direction)
        actual = mesh.ray_distance(mesh.normalize(point), direction)*mesh.scale
        expected = box.ray_distance(box.normalize(point), direction)*box.scale
        # The existing primitive query uses a scalar numerical minimizer.
        assert actual == pytest.approx(expected, abs=3e-5)


def test_overlapping_glb_components_are_a_union_and_open_parts_keep_solids():
    first, second = cube_mesh((0, 0, .4)), cube_mesh((0, 0, -.4))
    data = MeshData(vertices=first.vertices+second.vertices,
                    faces=first.faces+(np.asarray(second.faces)+8).tolist())
    scene = Scene([Shape(id="multi", kind="mesh", mesh=data)])
    assert scene.distance(scene.normalize([0, 0, 0]))[0] < 0
    assert scene.distance(scene.normalize([0, 0, .6]))[0] < -.1
    intervals = scene.intervals(scene.normalize([.1, .2, 5]), np.array([0., 0., -1.]))
    assert len(intervals) == 1
    np.testing.assert_allclose(np.asarray(intervals[0][:2])*scene.scale, [3.6, 6.4], atol=1e-8)
    vertices = data.vertices+[[3, 0, 0], [4, 0, 0], [3, 1, 0]]
    mixed = MeshData(vertices=vertices, faces=data.faces+[[16, 17, 18]])
    scene = Scene([Shape(id="mixed", kind="mesh", mesh=mixed)])
    assert scene.distance(scene.normalize([0, 0, 0]))[0] < 0
    assert scene.distance(scene.normalize([3.1, .1, 1]))[0] > 0


def mesh_request():
    c = camera()
    t = np.linspace(-1.7, 1.7, 80)
    points = project(c, np.column_stack([t, .12*np.sin(t*2), np.zeros(len(t))]))
    return SolveInput(objects=[Shape(id="imported", kind="mesh", mesh=cube_mesh())],
                      camera=c, points=points.tolist(), options=Options(iterations=8, max_points=80))


def test_mesh_solve_off_surface_skip_and_redraw():
    request = mesh_request()
    first = solve(request)
    assert first["status"] == "ok"
    assert any(first["debug"]["on"]) and not all(first["debug"]["on"])
    assert max(first["debug"]["heights"]) > 0
    assert first["metrics"]["projection_p95_px"] < 2
    scene = Scene(request.objects)
    assert scene.clear(scene.normalize(first["points"]))
    alternative = next(t for t in first["debug"]["tracks"] if not t["selected"])
    request.locks = {alternative["group"]: alternative["id"]}
    second = solve(request)
    assert alternative["id"] in second["selected_tracks"]
    assert np.mean(np.array(first["points"])[:, 2])-np.mean(np.array(second["points"])[:, 2]) > 1
    assert second["metrics"]["cache_hit"]
    original = first["points"]
    start, end = len(original)//3, 2*len(original)//3
    request.locks = {}
    request.points = project(request.camera, np.asarray(original[start:end+1])).tolist()
    result = redraw(RedrawInput(solve=request, original=original, start=start, end=end))
    assert result["status"] == "ok"
    np.testing.assert_allclose(result["points"][:start+1], original[:start+1], atol=1e-10)
    np.testing.assert_allclose(result["points"][-(len(original)-end):], original[end:], atol=1e-10)


def test_mesh_payload_validation_and_http_roundtrip():
    request = mesh_request().model_dump()
    request.update(request_id="mesh-roundtrip", revision=42)
    with TestClient(app) as client:
        response = client.post('/api/solve', json=request)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["request_id"] == "mesh-roundtrip"
        assert response.json()["revision"] == 42
        request["objects"][0]["mesh"]["faces"][0] = [0, 1, 999]
        assert client.post('/api/solve', json=request).status_code == 422
    with pytest.raises(ValidationError):
        Shape(id="no-geometry", kind="mesh")
    with pytest.raises(ValidationError):
        MeshData(vertices=[[0, 0, 0], [1, 0, 0], [2, 0, 0]], faces=[[0, 1, 2]])


@pytest.mark.parametrize('open_surface', [False, True])
def test_curved_and_open_surface_meshes_generate_curves(open_surface):
    if open_surface:
        data = MeshData(vertices=[[-1, -1, 0], [1, -1, 0], [0, 1, 0]], faces=[[0, 1, 2]])
        c = camera()
        points = project(c, np.column_stack([np.linspace(-.3, .3, 20), np.zeros((20, 2))]))
        request = SolveInput(objects=[Shape(id='sheet', kind='mesh', mesh=data)],
                             camera=c, points=points.tolist(), options=Options(iterations=8))
    else:
        i = np.arange(128)
        y = 1-2*(i+.5)/len(i)
        radius = np.sqrt(1-y*y)
        angle = i*np.pi*(3-np.sqrt(5))
        vertices = np.column_stack([radius*np.cos(angle), y, radius*np.sin(angle)])
        data = MeshData(vertices=vertices.tolist(), faces=ConvexHull(vertices).simplices.tolist())
        from skippy.algorithm.fixtures import example
        request = example()
        request.objects = [Shape(id='curved', kind='mesh', mesh=data)]
        request.options.iterations = 8
        request.options.max_points = 80
    result = solve(request)
    assert result['status'] == 'ok'
    assert result['metrics']['projection_p95_px'] < 2
    scene = Scene(request.objects)
    assert scene.clear(scene.normalize(result['points']))
