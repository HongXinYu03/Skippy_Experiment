"""Exact exterior primitive distances and all ray/offset-body intersections."""
from dataclasses import dataclass
import numpy as np
from scipy.optimize import brentq, minimize_scalar
from scipy.spatial.transform import Rotation
from .models import Shape
from .mesh import MeshPrimitive
from .progress import tracked, progress_iter, current_step

EPS = 1e-10
TETRA = np.array([[1, 1, 1], [-1, -1, 1], [-1, 1, -1], [1, -1, -1]], float) / np.sqrt(3)
FACES = [(0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2)]

def unit(v):
    """Normalize a vector or array of vectors."""
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), EPS)

def triangle_distance(points, a, b, c):
    """Closest distance including face projection and all three edges."""
    ab, ac = b-a, c-a
    normal = unit(np.cross(ab, ac))
    signed = (points-a) @ normal
    projected = points - signed[:, None] * normal
    v = projected-a
    d00, d01, d11 = ab@ab, ab@ac, ac@ac
    denom = d00*d11-d01*d01
    u = (d11*(v@ab)-d01*(v@ac))/denom
    w = (d00*(v@ac)-d01*(v@ab))/denom
    best = np.where((u >= 0) & (w >= 0) & (u+w <= 1), abs(signed), np.inf)
    for x, y in ((a, b), (b, c), (c, a)):
        edge = y-x
        t = np.clip((points-x)@edge/(edge@edge), 0, 1)
        best = np.minimum(best, np.linalg.norm(points-x-t[:, None]*edge, axis=1))
    return best

@dataclass
class Primitive:
    spec: Shape
    center: np.ndarray
    scale: float

    def __post_init__(self):
        self.position = (np.asarray(self.spec.position)-self.center)/self.scale
        self.size = np.asarray(self.spec.size)/self.scale
        self.rotation = Rotation.from_quat(self.spec.quaternion).as_matrix()
        self.radius = float(self.size[0])
        self.bound = self.radius if self.spec.kind != "box" else float(np.linalg.norm(self.size)/2)
        self.vertices = TETRA*self.radius
        self.normals = []
        for face in FACES:
            a, b, c = self.vertices[list(face)]
            normal = unit(np.cross(b-a, c-a))
            if normal@a < 0:
                normal = -normal
            self.normals.append((a, normal))

    def distance(self, points):
        p = (np.atleast_2d(points)-self.position)@self.rotation
        if self.spec.kind == "sphere":
            return np.linalg.norm(p, axis=1)-self.radius
        if self.spec.kind == "box":
            q = abs(p)-self.size/2
            return np.linalg.norm(np.maximum(q, 0), axis=1)+np.minimum(q.max(axis=1), 0)
        closest = np.full(len(p), np.inf)
        for face in FACES:
            closest = np.minimum(closest, triangle_distance(p, *self.vertices[list(face)]))
        inside = np.ones(len(p), bool)
        for a, normal in self.normals:
            inside &= ((p-a)@normal <= EPS)
        return np.where(inside, -closest, closest)

    def sphere_interval(self, origin, direction, radius):
        offset = origin-self.position
        b = offset@direction
        discriminant = b*b-offset@offset+radius*radius
        if discriminant < -EPS:
            return None
        delta = np.sqrt(max(0, discriminant))
        return -b-delta, -b+delta

    def interval(self, origin, direction, height=0.0):
        bound = self.sphere_interval(origin, direction, self.bound+height+1e-7)
        if bound is None or bound[1] <= EPS:
            return None
        low, high = max(EPS, bound[0]), bound[1]
        if self.spec.kind == "sphere":
            return low, high
        o = (origin-self.position)@self.rotation
        r = direction@self.rotation
        if height <= 1e-9:
            if self.spec.kind == "box":
                half = self.size/2
                for axis in range(3):
                    if abs(r[axis]) < EPS:
                        if abs(o[axis]) > half[axis]+EPS:
                            return None
                    else:
                        a, b = sorted(((-half[axis]-o[axis])/r[axis], (half[axis]-o[axis])/r[axis]))
                        low, high = max(low, a), min(high, b)
            else:
                for a, normal in self.normals:
                    numerator = (a-o)@normal
                    denominator = r@normal
                    if abs(denominator) < EPS:
                        if numerator < -EPS:
                            return None
                    elif denominator > 0:
                        high = min(high, numerator/denominator)
                    else:
                        low = max(low, numerator/denominator)
            return (low, high) if high >= low and high > EPS else None
        f = lambda t: float(self.distance(origin+t*direction)[0]-height)
        minimum = minimize_scalar(f, bounds=(low, high), method="bounded", options={"xatol": 1e-9})
        if minimum.fun > 1e-7:
            return None
        middle = minimum.x
        if abs(minimum.fun) < 1e-9:
            return middle, middle
        left = brentq(f, low, middle, xtol=1e-10) if f(low) > 0 else low
        right = brentq(f, middle, high, xtol=1e-10) if f(high) > 0 else high
        return left, right

    def ray_distance(self, origin, direction):
        """Distance to a positive ray; convexity permits a bounded scalar solve."""
        if self.spec.kind == "sphere":
            t = max(0.0, (self.position-origin)@direction)
            return max(0.0, float(self.distance(origin+t*direction)[0]))
        t = max(0.0, (self.position-origin)@direction)
        f = lambda depth: float(self.distance(origin+depth*direction)[0])
        result = minimize_scalar(f, bounds=(max(0, t-self.bound), t+self.bound), method="bounded")
        return max(0.0, result.fun)

    def intervals(self, origin, direction, height=0.0):
        interval = self.interval(origin, direction, height)
        return [interval] if interval is not None else []

class Scene:
    @tracked("场景几何准备")
    def __init__(self, objects):
        objects = [o for o in objects if o.enabled]
        if not objects:
            self.center, self.scale, self.objects = np.zeros(3), 1.0, []
            return
        lows, highs = [], []
        for obj in objects:
            if obj.kind == "mesh":
                rotation = Rotation.from_quat(obj.quaternion).as_matrix()
                vertices = np.asarray(obj.mesh.vertices)*np.asarray(obj.size)
                vertices = vertices@rotation.T+np.asarray(obj.position)
                lows.append(vertices.min(axis=0))
                highs.append(vertices.max(axis=0))
            else:
                bound = np.linalg.norm(obj.size)/2 if obj.kind == "box" else obj.size[0]
                lows.append(np.asarray(obj.position)-bound)
                highs.append(np.asarray(obj.position)+bound)
        low, high = np.min(lows, axis=0), np.max(highs, axis=0)
        self.center = (low+high)/2
        self.scale = float(max(np.max(high-low), 1e-3))
        self.objects = []
        for obj in progress_iter(objects):
            detail = f"{obj.kind} {obj.id}"
            if obj.mesh is not None:
                detail += f"，{len(obj.mesh.faces)} 个三角面"
            current_step().update(detail=detail)
            self.objects.append((MeshPrimitive if obj.kind == "mesh" else Primitive)(obj, self.center, self.scale))

    def normalize(self, p):
        return (np.asarray(p)-self.center)/self.scale

    def world(self, p):
        return np.asarray(p)*self.scale+self.center

    def distance(self, points):
        p = np.atleast_2d(points)
        return np.min([o.distance(p) for o in self.objects], axis=0) if self.objects else np.full(len(p), np.inf)

    def gradient(self, points):
        p = np.atleast_2d(points)
        step = 1e-5
        return np.column_stack([(self.distance(p+np.eye(3)[i]*step)-self.distance(p-np.eye(3)[i]*step))/(2*step) for i in range(3)])

    def intervals(self, origin, direction, height=0.0):
        """Union of offset solids, removing internal and overlapping roots."""
        intervals = []
        for obj in self.objects:
            for interval in obj.intervals(origin, direction, height):
                intervals.append((*interval, obj.spec.id))
        merged = []
        for low, high, object_id in sorted(intervals):
            if merged and low <= merged[-1][1]+1e-8:
                merged[-1][1] = max(high, merged[-1][1])
            else:
                merged.append([low, high, object_id])
        return merged

    def ray_distance(self, origin, direction):
        return min(o.ray_distance(origin, direction) for o in self.objects)

    def clear(self, points, tolerance=0.0005):
        p = np.asarray(points)
        if len(p) < 2:
            return bool(np.min(self.distance(p)) >= -tolerance)
        # Max step 0.003 in normalized scene units, including all endpoints.
        a, delta = p[:-1], np.diff(p, axis=0)
        counts = np.maximum(2, np.ceil(np.linalg.norm(delta, axis=1)/.003).astype(int)+1)
        segment = np.repeat(np.arange(len(a)), counts)
        start = np.repeat(np.cumsum(counts)-counts, counts)
        t = (np.arange(np.sum(counts))-start)/(counts[segment]-1)
        checks = a[segment]+t[:, None]*delta[segment]
        return bool(np.min(self.distance(checks)) >= -tolerance)
