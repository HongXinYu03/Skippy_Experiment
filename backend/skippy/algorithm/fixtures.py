import numpy as np
from .models import Camera, Shape, SolveInput, Options
from .geometry import unit
from .stroke import project

# Fixtures for testing and debugging the Skippy inference algorithm.
def camera(position=(0, 0, 6), width=1000, height=700):
    p = np.array(position, float)
    z = unit(p)
    x = unit(np.cross([0, 1, 0], z))
    y = np.cross(z, x)
    world = np.eye(4)
    world[:3, :3] = np.column_stack([x, y, z])
    world[:3, 3] = p
    near, far = .1, 100
    f = 1/np.tan(np.deg2rad(45)/2)
    projection = np.array([[f/(width/height), 0, 0, 0], [0, f, 0, 0], [0, 0, (far+near)/(near-far), 2*far*near/(near-far)], [0, 0, -1, 0]])
    return Camera(world=world.flatten(order="F").tolist(), projection=projection.flatten(order="F").tolist(), width=width, height=height)

def example(kind="sphere", width=1000, height=700):
    c = camera(width=width, height=height)
    if kind == "stress":
        c = camera((0, 0, 14), width, height)
        t = np.linspace(-5.1, 5.1, 400)
        p = np.column_stack([t, .95+.12*np.sin(t*2), np.zeros(len(t))])
        objects = [Shape(id=f"sphere-{i}-{j}", kind="sphere", position=(i*2, j, 0), size=(.6, .6, .6)) for i in range(-2, 3) for j in (-1, 1)]
    elif kind == "loop":
        t = np.linspace(0, 2*np.pi, 160)
        p = np.column_stack([.45*np.cos(t), .45*np.sin(t), np.zeros(len(t))])
        objects = [Shape(id="sphere", kind="sphere")]
    elif kind == "box":
        t = np.linspace(-1.4, 1.4, 120)
        p = np.column_stack([t, .15*np.sin(t*2), np.zeros(len(t))])
        objects = [Shape(id="box", kind="box", size=(1.7, 1.7, 1.7))]
    elif kind == "two-spheres":
        t = np.linspace(-2.6, 2.6, 180)
        p = np.column_stack([t, .15*np.cos(t*2), np.zeros(len(t))])
        objects = [Shape(id="a", kind="sphere", position=(-1.2, 0, 0), size=(.8, .8, .8)), Shape(id="b", kind="sphere", position=(1.2, 0, -.4), size=(.8, .8, .8))]
    elif kind == "tetrahedron":
        t = np.linspace(.08, .3, 60)
        p = np.column_stack([t, np.full(len(t), .12), np.zeros(len(t))])
        objects = [Shape(id="tetra", kind="tetrahedron")]
    elif kind == "discontinuity":
        t = np.linspace(-1.7, 1.7, 140)
        p = np.column_stack([t, .3*np.exp(-t*t*2), np.zeros(len(t))])
        objects = [Shape(id="near", kind="box", position=(-.55, 0, .5), size=(1.4, 1.8, 1.2)), Shape(id="far", kind="box", position=(.65, 0, -1), size=(1.5, 1.7, 1.2))]
    else:
        t = np.linspace(-1.65, 1.65, 120)
        p = np.column_stack([t, .22*np.sin(t*2), np.zeros(len(t))])
        objects = [Shape(id="sphere", kind="sphere")]
    return SolveInput(objects=objects, camera=c, points=project(c, p).tolist(), options=Options(iterations=24, spacing=2 if kind == "stress" else 8, max_points=200 if kind == "stress" else 160))
