"""Triangle-mesh queries for the same distance/offset interface as primitives.

A bounding-volume hierarchy accelerates closest points and ray queries. Closed
meshes use parity for the distance sign; open meshes are two-sided surfaces.
Offset ray intervals are the union of triangle prisms and edge capsules, so
concavities and disconnected components can produce multiple depth candidates.
"""
from dataclasses import dataclass
import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from .progress import stage, current_step

EPS = 1e-10


def merge_intervals(intervals, tolerance=1e-8):
    merged = []
    for low, high in sorted(intervals):
        if high < EPS or low > high+tolerance:
            continue
        low = max(EPS, low)
        if merged and low <= merged[-1][1]+tolerance:
            merged[-1][1] = max(high, merged[-1][1])
        else:
            merged.append([low, high])
    return merged


def triangle_distances(points, triangles):
    """Distances between corresponding points and triangles, including edges."""
    a, b, c = triangles.transpose(1, 0, 2)
    ab, ac = b-a, c-a
    normal = np.cross(ab, ac)
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    signed = np.einsum('ij,ij->i', points-a, normal)
    projected = points-signed[:, None]*normal-a
    d00 = np.einsum('ij,ij->i', ab, ab)
    d01 = np.einsum('ij,ij->i', ab, ac)
    d11 = np.einsum('ij,ij->i', ac, ac)
    d20 = np.einsum('ij,ij->i', projected, ab)
    d21 = np.einsum('ij,ij->i', projected, ac)
    denominator = d00*d11-d01*d01
    u = (d11*d20-d01*d21)/denominator
    v = (d00*d21-d01*d20)/denominator
    best = np.where((u >= 0) & (v >= 0) & (u+v <= 1), signed*signed, np.inf)
    for start, end in ((a, b), (b, c), (c, a)):
        edge = end-start
        t = np.clip(np.einsum('ij,ij->i', points-start, edge)/
                    np.einsum('ij,ij->i', edge, edge), 0, 1)
        delta = points-start-t[:, None]*edge
        best = np.minimum(best, np.einsum('ij,ij->i', delta, delta))
    return np.sqrt(np.maximum(best, 0))


@dataclass
class Node:
    low: np.ndarray
    high: np.ndarray
    faces: np.ndarray | None = None
    left: object = None
    right: object = None


class MeshPrimitive:
    def __init__(self, spec, center, scale, triangles=None, split=True):
        self.spec = spec
        if triangles is None:
            rotation = Rotation.from_quat(spec.quaternion).as_matrix()
            vertices = np.asarray(spec.mesh.vertices)*np.asarray(spec.size)
            vertices = (vertices@rotation.T+np.asarray(spec.position)-center)/scale
            triangles = vertices[np.asarray(spec.mesh.faces)]
        self.triangles = triangles
        # glTF frequently duplicates vertices at UV seams or hard normals.
        vertices = triangles.reshape(-1, 3)
        welded, inverse = np.unique(np.round(vertices, 12), axis=0, return_inverse=True)
        faces = inverse.reshape(-1, 3)
        edges = np.sort(np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]],
                                       faces[:, [2, 0]])), axis=1)
        _, edge_ids, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
        self.closed = bool(np.all(counts == 2))
        self.low = self.triangles.min(axis=(0, 1))
        self.high = self.triangles.max(axis=(0, 1))
        self.position = (self.low+self.high)/2
        self.bound = float(np.linalg.norm(self.high-self.low)/2)
        self.parts = []
        if split:
            # Treat closed disconnected components as a union, including when
            # a GLB contains overlapping solids or mixes solids and open sheets.
            order = np.argsort(edge_ids)
            ids = np.tile(np.arange(len(faces)), 3)[order]
            shared = np.diff(edge_ids[order]) == 0
            adjacency = coo_matrix((np.ones(np.count_nonzero(shared)),
                                    (ids[:-1][shared], ids[1:][shared])),
                                   shape=(len(faces), len(faces)))
            count, labels = connected_components(adjacency, directed=False)
            face_closed = (counts[edge_ids] == 2).reshape(3, -1).all(axis=0)
            component_closed = np.ones(count, bool)
            np.logical_and.at(component_closed, labels, face_closed)
            face_order = np.argsort(labels)
            boundaries = np.r_[0, np.cumsum(np.bincount(labels))]
            groups = [face_order[boundaries[group]:boundaries[group+1]]
                      for group in np.flatnonzero(component_closed)]
            open_faces = np.flatnonzero(~component_closed[labels])
            if len(open_faces):
                groups.append(open_faces)
            if len(groups) > 1:
                with stage("多部件网格索引", total=len(faces)) as task:
                    for index, indices in enumerate(groups, 1):
                        task.update(detail=f"部件 {index}/{len(groups)}")
                        self.parts.append(MeshPrimitive(spec, center, scale, triangles[indices], split=False))
                self.closed = all(part.closed for part in self.parts)
                return
        self.vertex_tree = cKDTree(welded)
        self._lows = self.triangles.min(axis=1)
        self._highs = self.triangles.max(axis=1)
        self._centers = (self._lows+self._highs)/2
        if split:
            with stage("构建网格 BVH", total=len(faces)) as task:
                self.root = self._build(np.arange(len(faces)), task)
        else:
            self.root = self._build(np.arange(len(faces)), current_step())

    def _build(self, indices, task):
        node = Node(self._lows[indices].min(axis=0), self._highs[indices].max(axis=0))
        if len(indices) <= 8:
            node.faces = indices
            task.advance(len(indices))
        else:
            axis = int(np.ptp(self._centers[indices], axis=0).argmax())
            middle = len(indices)//2
            order = np.argpartition(self._centers[indices, axis], middle)
            node.left = self._build(indices[order[:middle]], task)
            node.right = self._build(indices[order[middle:]], task)
        return node

    def _unsigned_distance(self, points):
        best = self.vertex_tree.query(points)[0]
        stack = [(self.root, np.arange(len(points)))]
        while stack:
            node, indices = stack.pop()
            delta = np.maximum(np.maximum(node.low-points[indices], points[indices]-node.high), 0)
            indices = indices[np.einsum('ij,ij->i', delta, delta) <= best[indices]**2+1e-20]
            if not len(indices):
                continue
            if node.faces is None:
                stack.append((node.left, indices))
                stack.append((node.right, indices))
            else:
                # Bound temporary arrays even during dense curve collision checks.
                for start in range(0, len(indices), 2048):
                    batch = indices[start:start+2048]
                    triangles = np.tile(self.triangles[node.faces], (len(batch), 1, 1))
                    p = np.repeat(points[batch], len(node.faces), axis=0)
                    distances = triangle_distances(p, triangles).reshape(len(batch), -1)
                    best[batch] = np.minimum(best[batch], distances.min(axis=1))
        return best

    @staticmethod
    def _ray_box(origins, direction, low, high):
        near = np.full(len(origins), -np.inf)
        far = np.full(len(origins), np.inf)
        for axis in range(3):
            if abs(direction[axis]) < EPS:
                far[(origins[:, axis] < low[axis]) | (origins[:, axis] > high[axis])] = -np.inf
            else:
                a = (low[axis]-origins[:, axis])/direction[axis]
                b = (high[axis]-origins[:, axis])/direction[axis]
                near = np.maximum(near, np.minimum(a, b))
                far = np.minimum(far, np.maximum(a, b))
        return (far >= np.maximum(near, EPS)), near, far

    def _ray_hits(self, origins, direction):
        """All unique positive intersections, for one or many parallel rays."""
        stack = [(self.root, np.arange(len(origins)))]
        ray_ids, depths = [], []
        while stack:
            node, indices = stack.pop()
            mask, _, _ = self._ray_box(origins[indices], direction, node.low-1e-12, node.high+1e-12)
            indices = indices[mask]
            if not len(indices):
                continue
            if node.faces is None:
                stack.append((node.left, indices))
                stack.append((node.right, indices))
                continue
            triangles = self.triangles[node.faces]
            edge1, edge2 = triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]
            cross = np.cross(direction, edge2)
            determinant = np.einsum('ij,ij->i', edge1, cross)
            valid = abs(determinant) > 1e-14
            inverse = np.divide(1., determinant, out=np.zeros_like(determinant), where=valid)
            for start in range(0, len(indices), 2048):
                batch = indices[start:start+2048]
                offset = origins[batch, None, :]-triangles[None, :, 0, :]
                u = np.einsum('nfi,fi->nf', offset, cross)*inverse
                q = np.cross(offset, edge1)
                v = np.einsum('nfi,i->nf', q, direction)*inverse
                t = np.einsum('nfi,fi->nf', q, edge2)*inverse
                hit = valid & (u >= -1e-10) & (v >= -1e-10) & (u+v <= 1+1e-10) & (t > EPS)
                rows, cols = np.nonzero(hit)
                ray_ids.extend(batch[rows].tolist())
                depths.extend(t[rows, cols].tolist())
        if not depths:
            return np.array([], int), np.array([])
        ids, t = np.asarray(ray_ids), np.asarray(depths)
        order = np.lexsort((t, ids))
        ids, t = ids[order], t[order]
        unique = np.r_[True, (ids[1:] != ids[:-1]) | (np.diff(t) > 1e-8)]
        return ids[unique], t[unique]

    def distance(self, points):
        points = np.atleast_2d(points)
        if self.parts:
            return np.min([part.distance(points) for part in self.parts], axis=0)
        distances = self._unsigned_distance(points)
        if self.closed:
            distances[self._inside(points)] *= -1
        return distances

    def _inside(self, points):
        direction = np.array([1., .371390676, .529103])
        direction /= np.linalg.norm(direction)
        ids, _ = self._ray_hits(points, direction)
        return np.bincount(ids, minlength=len(points)) % 2 == 1

    def _surface_intervals(self, origin, direction):
        if self.parts:
            return merge_intervals([interval for part in self.parts
                                    for interval in part._surface_intervals(origin, direction)])
        if not self.closed:
            _, depths = self._ray_hits(np.atleast_2d(origin), direction)
            return [[t, t] for t in depths]
        # Starting beyond the bounds and sweeping forward avoids a separate
        # inside query when the camera itself lies inside this object.
        shift = max(0., float((origin-self.position)@direction)+self.bound+1.)
        start = origin-shift*direction
        _, all_depths = self._ray_hits(np.atleast_2d(start), direction)
        middle = (all_depths[:-1]+all_depths[1:])/2
        inside = self._inside(start+middle[:, None]*direction) if len(middle) else []
        intervals = []
        for low, high, solid in zip(all_depths[:-1], all_depths[1:], inside):
            if solid and high-shift > EPS:
                intervals.append([max(EPS, low-shift), high-shift])
        # Preserve isolated surface contacts, including tangent rays.
        intervals.extend([t-shift, t-shift] for t in all_depths if t-shift > EPS)
        return merge_intervals(intervals)

    @staticmethod
    def _clip(low, high, offset, slope, minimum, maximum):
        """Clip many ray intervals by slabs, including parallel rays."""
        parallel = abs(slope) < 1e-14
        denominator = np.where(parallel, 1., slope)
        a, b = (minimum-offset)/denominator, (maximum-offset)/denominator
        low = np.where(parallel, low, np.maximum(low, np.minimum(a, b)))
        high = np.where(parallel, high, np.minimum(high, np.maximum(a, b)))
        outside = parallel & ((offset < minimum-1e-12) | (offset > maximum+1e-12))
        return low, np.where(outside, -np.inf, high)

    def _triangle_offsets(self, origin, direction, triangles, radius):
        """Vectorized Euclidean offsets: face prisms plus edge capsules."""
        a, b, c = triangles.transpose(1, 0, 2)
        normal = np.cross(b-a, c-a)
        normal /= np.linalg.norm(normal, axis=1)[:, None]
        low, high = self._clip(np.full(len(a), -np.inf), np.full(len(a), np.inf),
                               np.einsum('ij,ij->i', origin-a, normal), normal@direction,
                               -radius, radius)
        for start, end in ((a, b), (b, c), (c, a)):
            inward = np.cross(normal, end-start)
            low, high = self._clip(low, high, np.einsum('ij,ij->i', origin-start, inward),
                                   inward@direction, 0., np.inf)
        intervals = [np.column_stack([low, high])[high >= low]]

        starts, ends = np.concatenate([a, b, c]), np.concatenate([b, c, a])
        edge, offset = ends-starts, origin-starts
        length = np.linalg.norm(edge, axis=1)
        axis = edge/length[:, None]
        parallel = axis@direction
        along = np.einsum('ij,ij->i', offset, axis)
        perpendicular = direction-parallel[:, None]*axis
        radial = offset-along[:, None]*axis
        aa = np.einsum('ij,ij->i', perpendicular, perpendicular)
        bb = np.einsum('ij,ij->i', radial, perpendicular)
        cc = np.einsum('ij,ij->i', radial, radial)-radius*radius
        discriminant = bb*bb-aa*cc
        axial = aa < 1e-14
        delta = np.sqrt(np.maximum(0., discriminant))
        denominator = np.where(axial, 1., aa)
        low = np.where(axial, -np.inf, (-bb-delta)/denominator)
        high = np.where(axial, np.inf, (-bb+delta)/denominator)
        low, high = self._clip(low, high, along, parallel, 0., length)
        valid = (high >= low) & np.where(axial, cc <= 1e-12, discriminant >= -1e-12)
        intervals.append(np.column_stack([low, high])[valid])

        # The three endpoint spheres complete all edge capsules.
        projection = offset@direction
        discriminant = projection*projection-np.einsum('ij,ij->i', offset, offset)+radius*radius
        delta = np.sqrt(np.maximum(0., discriminant))
        intervals.append(np.column_stack([-projection-delta, -projection+delta])[discriminant >= -1e-12])
        return np.concatenate(intervals).tolist()

    def intervals(self, origin, direction, height=0.):
        if self.parts:
            return merge_intervals([interval for part in self.parts
                                    for interval in part.intervals(origin, direction, height)])
        intervals = self._surface_intervals(origin, direction)
        if height <= 1e-9:
            return intervals
        stack = [self.root]
        faces = []
        while stack:
            node = stack.pop()
            hit, _, _ = self._ray_box(np.atleast_2d(origin), direction, node.low-height, node.high+height)
            if not hit[0]:
                continue
            if node.faces is None:
                stack.extend((node.left, node.right))
            else:
                faces.extend(node.faces.tolist())
        if faces:
            intervals.extend(self._triangle_offsets(origin, direction, self.triangles[faces], height))
        return merge_intervals(intervals)

    def ray_distance(self, origin, direction):
        """Exact minimum distance from a positive ray to all mesh triangles."""
        if self.parts:
            return min(part.ray_distance(origin, direction) for part in self.parts)
        if self._surface_intervals(origin, direction):
            return 0.
        # A minimum is at the origin, a ray/face intersection, or an edge.
        # When the ray parallels a face, its minimum is also attained on an edge.
        best = float(self._unsigned_distance(np.atleast_2d(origin))[0])
        for start in range(0, len(self.triangles), 4096):
            triangles = self.triangles[start:start+4096]
            for a, b in ((0, 1), (1, 2), (2, 0)):
                p, edge = triangles[:, a]-origin, triangles[:, b]-triangles[:, a]
                along_p, along_e = p@direction, edge@direction
                radial_p = p-along_p[:, None]*direction
                radial_e = edge-along_e[:, None]*direction
                denominator = np.einsum('ij,ij->i', radial_e, radial_e)
                u = np.clip(-np.einsum('ij,ij->i', radial_p, radial_e)/np.maximum(denominator, 1e-30), 0, 1)
                # Also check t=0 boundaries, where an edge crosses the ray's
                # origin plane, and the closest point to the origin itself.
                boundary = np.clip(np.divide(-along_p, along_e, out=np.zeros_like(along_p),
                                              where=abs(along_e) > 1e-14), 0, 1)
                for parameter in (u, boundary):
                    offset = p+parameter[:, None]*edge
                    t = np.maximum(0., offset@direction)
                    distance = np.linalg.norm(offset-t[:, None]*direction, axis=1).min()
                    best = min(best, float(distance))
        return best
