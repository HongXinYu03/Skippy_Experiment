"""Regression checks for soft height preservation and ordinary stroke endpoints."""
import numpy as np
import pytest
from skippy.algorithm.fixtures import camera
from skippy.algorithm.geometry import Scene
from skippy.algorithm.models import Options, Shape, SolveInput
from skippy.algorithm.pipeline import solve
from skippy.algorithm.smoothing import energy_gradient, operators
from skippy.algorithm.stroke import project


def tail_turns(points, count=4):
    edges = np.diff(points, axis=0)
    directions = edges / np.maximum(np.linalg.norm(edges, axis=1)[:, None], 1e-12)
    turns = np.degrees(np.arccos(np.clip(np.sum(directions[:-1]*directions[1:], axis=1), -1, 1)))
    return np.r_[turns[:count], turns[-count:]]


@pytest.mark.parametrize("iterations", [0, 80])
@pytest.mark.parametrize("bottom_radius", [.65, .8, .95])
def test_open_u_stroke_has_no_optimization_induced_endpoint_reversal(iterations, bottom_radius):
    t = np.linspace(-2.15, 2.15, 180)
    radius = bottom_radius+.9*(abs(t)/2.15)**2
    planar = np.column_stack([radius*np.sin(t), -radius*np.cos(t), np.zeros(len(t))])
    c = camera()
    request = SolveInput(objects=[Shape(id="sphere", kind="sphere")], camera=c,
                         points=project(c, planar).tolist(), options=Options(iterations=iterations))
    result = solve(request)
    raw, smoothed = np.array(result["raw_points"]), np.array(result["control_points"])
    assert max(tail_turns(raw)) < 5
    # The former height snap introduced a ~150-degree reversal in these same cases.
    assert max(tail_turns(smoothed)) < 10
    # Also cover the first/last four spline intervals, including knot boundaries.
    assert max(tail_turns(np.array(result["points"]), count=20)) < 30
    if iterations == 0:
        np.testing.assert_allclose(smoothed[[0, -1]], raw[[0, -1]], atol=1e-12)
        assert result["debug"]["smoothing"]["iterations"] == 0
    else:
        # A newly drawn open curve is allowed to adjust both endpoints.
        assert np.all(np.linalg.norm(smoothed[[0, -1]]-raw[[0, -1]], axis=1) > 1e-4)


def test_height_error_still_affects_energy_and_descent_gradient():
    scene = Scene([Shape(id="sphere", kind="sphere")])
    points = np.array([[-.4, .1, .8], [-.2, .15, .85], [0, .2, .9], [.2, .15, .85], [.4, .1, .8]])
    origin = np.array([0., 0., 3.])
    rays = points-origin
    rays /= np.linalg.norm(rays, axis=1)[:, None]
    actual_height = scene.distance(points)
    first, second = operators(len(points), False)
    matched, matched_gradient = energy_gradient(points, scene, origin, rays, actual_height, first, second)
    mismatched, mismatched_gradient = energy_gradient(points, scene, origin, rays, actual_height+.1, first, second)
    assert mismatched > matched+1e-5
    assert np.linalg.norm(mismatched_gradient-matched_gradient) > 1e-5
