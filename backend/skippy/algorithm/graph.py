"""Segment graph: on polylines are nodes; off interpolants are edges."""
from dataclasses import dataclass
import numpy as np
from .geometry import unit
from .models import SolveError

""" 
计算曲线的曲率
参数：
    points: 点集，二维数组，每一行表示一个点的坐标
返回值：
    curvature: 曲率数组，表示每个点的曲率
"""
def curvature(points):
    if len(points) < 3:
        return np.zeros(0)
    a = np.diff(points, axis=0)
    la = np.linalg.norm(a, axis=1)
    chord = np.linalg.norm(points[2:]-points[:-2], axis=1)
    return 2*np.linalg.norm(np.cross(a[:-1], a[1:]), axis=1)/np.maximum(la[:-1]*la[1:]*chord, 1e-12)

""" 
计算曲线的变差
参数：
    points: 点集，二维数组，每一行表示一个点的坐标
返回值：
    variation: 变差，表示曲线的不平整程度
"""
def variation(points):
    k = curvature(points)
    return float(np.sum(abs(np.diff(k)))) if len(k) > 1 else 0.0

def sigmoid(t):
    """Paper equation (1), not a logistic approximation."""
    return np.where(t <= .5, .5-.5*np.sqrt(np.maximum(0, 1-(2*t)**2)), .5+.5*np.sqrt(np.maximum(0, 1-(2*t-2)**2)))

def corners(points):
    if len(points) < 4:
        return np.zeros(0, dtype=int)
    tangents = unit(np.diff(points, axis=0))
    smoothed = []
    for i, tangent in enumerate(tangents):
        a, b = max(0, i-4), min(len(tangents), i+5)
        distance = np.arange(a, b)-i
        weights = np.exp(-distance**2/8)*np.maximum(tangents[a:b]@tangent, 0)
        smoothed.append(unit(np.sum(tangents[a:b]*weights[:, None], axis=0)))
    angle = np.arccos(np.clip(np.sum(np.array(smoothed)[:-1]*np.array(smoothed)[1:], axis=1), -1, 1))
    return np.flatnonzero(angle > np.pi/4)+1

@dataclass
class Segment:
    id: str
    track: str
    group: int
    start: int
    end: int
    points: np.ndarray
    ranks: list[int]
    cost: float

""" 
构建段
参数：
    scene: 场景对象
    candidates: 候选顶点列表
    sequences: 序列列表
    cuts: 切割点列表
    options: 选项
返回值：
    segments: 段列表
    rejected: 被拒绝的段信息，包括深度、梯度和碰撞的数量
"""
def build_segments(scene, candidates, sequences, cuts, options):
    segments = []
    rejected = {"depth": 0, "gradient": 0, "collision": 0}
    for sequence in sequences:
        if not sequence["on"]:
            continue
        a, b, group = sequence["start"], sequence["end"], sequence["group"]
        links = {}
        incoming = set()
        for i in range(a, b):
            for j, v in enumerate(candidates[i]):
                if not v["active"]:
                    continue
                valid = []
                for k, w in enumerate(candidates[i+1]):
                    if not w["active"]:
                        continue
                    if abs(v["depth"]-w["depth"]) > options.depth_threshold:
                        rejected["depth"] += 1
                    elif v["normal"]@w["normal"] < options.gradient_cosine:
                        rejected["gradient"] += 1
                    elif not scene.clear([v["point"], w["point"]], options.collision_tolerance):
                        rejected["collision"] += 1
                    else:
                        valid.append((i+1, k))
                        incoming.add((i+1, k))
                links[(i, j)] = valid
        starts = [(i, j) for i in range(a, b+1) for j, v in enumerate(candidates[i]) if v["active"] and (i, j) not in incoming]
        paths = []
        stack = [[s] for s in starts]
        while stack:
            path = stack.pop()
            following = links.get(path[-1], [])
            if following:
                stack.extend([path+[v] for v in following])
            else:
                paths.append(path)
            if len(paths)+len(stack)+len(segments) > options.max_segments:
                raise SolveError("budget_exceeded", "候选段数量超过计算预算，请缩短笔触、简化遮挡或降低采样密度。")
        for k, path in enumerate(paths):
            p = np.array([candidates[i][j]["point"] for i, j in path])
            rank = [candidates[i][j]["rank"] for i, j in path]
            track = f"{group}:{k}"
            # Trim around a depth boundary so a bridge can span several rays.
            left = [0]
            right = [0]
            window = min(options.bridge_samples, max(0, len(path)-2))
            if a in cuts and window:
                left += list(range(1, window+1))
            if b+1 in cuts and window:
                right += list(range(1, window+1))
            for l in left:
                for r in right:
                    # Trimming both ends must leave at least one candidate vertex.
                    if l+r >= len(path):
                        continue
                    q = p[l:len(p)-r if r else None]
                    selected_ranks = rank[l:len(p)-r if r else None]
                    segments.append(Segment(f"{track}/{l}/{r}", track, group, path[l][0], path[len(path)-r-1][0], q, selected_ranks, variation(q)))
                    if len(segments) > options.max_segments:
                        raise SolveError("budget_exceeded", "段图超过预算，请减少深度桥接窗口或输入复杂度。")
    return segments, rejected

def interpolate(origin, directions, pixels, a, b, p, q, mix_cache=None):
    indices = np.arange(a, b+1)
    key = ("mix", a, b)
    if mix_cache is not None and key in mix_cache:
        t = mix_cache[key]
    else:
        t = (indices-a)/max(1, b-a)
        detected = corners(pixels[a:b+1])
        if len(detected) == 1:
            t = sigmoid(t)
        if mix_cache is not None:
            mix_cache[key] = t
    depth = (1-t)*np.linalg.norm(p-origin)+t*np.linalg.norm(q-origin)
    return origin+directions[indices]*depth[:, None]

def outer(origin, directions, segment, leading):
    indices = np.arange(0, segment.start+1) if leading else np.arange(segment.end, len(directions))
    depth = np.linalg.norm(segment.points-origin, axis=1)
    if leading:
        delta = np.mean(np.diff(depth[:4])) if len(depth) > 1 else 0
        values = depth[0]+(indices-segment.start)*delta
    else:
        delta = np.mean(np.diff(depth[-4:])) if len(depth) > 1 else 0
        values = depth[-1]+(indices-segment.end)*delta
    if np.any(values <= 1e-8):
        return None
    return origin+directions[indices]*values[:, None]

def shortest_path(node_cost, edges, starts, ends):
    """Topological relaxation. IDs are already ordered by input progress."""
    distance = dict(starts)
    previous = {}
    outgoing = {}
    for (a, b), weight in edges.items():
        outgoing.setdefault(a, []).append((b, weight))
    for node in range(len(node_cost)):
        if node not in distance:
            continue
        for successor, edge_cost in outgoing.get(node, []):
            candidate = distance[node]+edge_cost+node_cost[successor]
            if candidate < distance.get(successor, float("inf")):
                distance[successor] = candidate
                previous[successor] = node
    possible = [(distance[n]+cost, n) for n, cost in ends.items() if n in distance]
    if not possible:
        return None, None
    cost, current = min(possible)
    path = [current]
    while current in previous:
        current = previous[current]
        path.append(current)
    return path[::-1], float(cost)

def solve_graph(scene, segments, origin, directions, pixels, on, cuts, locks, options, closed, edge_cache=None):
    if edge_cache is None:
        edge_cache = {}
    segments.sort(key=lambda s: (s.start, s.end, s.id))
    for group, track in locks.items():
        if not any(s.group == group and s.track == track for s in segments):
            raise SolveError("no_path", "所选深度候选已失效，请清除深度锁定后重新求解。")
    available = [not (s.group in locks and locks[s.group] != s.track) for s in segments]
    edges, polylines = {}, {}
    starts, ends = {}, {}
    first_on = int(np.flatnonzero(on)[0])
    last_on = int(np.flatnonzero(on)[-1])
    for i, s in enumerate(segments):
        if not available[i]:
            continue
        if s.start == first_on and (s.group in locks or all(r == 0 for r in s.ranks)):
            p = outer(origin, directions, s, True)
            if p is not None and scene.clear(p, options.collision_tolerance):
                starts[i] = s.cost+variation(np.concatenate([p[:-1], s.points[:3]]))
                polylines[(-1, i)] = p
        if s.end == last_on:
            p = outer(origin, directions, s, False)
            if p is not None and scene.clear(p, options.collision_tolerance):
                ends[i] = variation(np.concatenate([s.points[-3:], p[1:]]))
                polylines[(i, -1)] = p
        for j in range(i+1, len(segments)):
            t = segments[j]
            if not available[j] or s.end >= t.start or s.group >= t.group:
                continue
            skipped = np.arange(s.end+1, t.start)
            if np.any(on[skipped]):
                # Bridges are permitted only within a short neighborhood of a detected discontinuity.
                if not any(s.end >= cut-options.bridge_samples-1 and t.start <= cut+options.bridge_samples for cut in cuts):
                    continue
            key = (s.id, t.id)
            if key not in edge_cache:
                p = interpolate(origin, directions, pixels, s.end, t.start, s.points[-1], t.points[0], edge_cache)
                if scene.clear(p, options.collision_tolerance):
                    neighborhood = np.concatenate([s.points[-3:-1], p, t.points[1:3]])
                    edge_cache[key] = (variation(neighborhood), p)
                else:
                    edge_cache[key] = None
            cached = edge_cache[key]
            if cached is not None:
                edges[(i, j)], polylines[(i, j)] = cached
    path, cost = shortest_path([s.cost for s in segments], edges, starts, ends)
    if path is None:
        raise SolveError("no_path", "没有可连续连接的曲线。请调整视角、缩短笔触，或尝试启用被剪枝候选。")
    if closed:
        best = None
        for start, weight in starts.items():
            closure_ends = {}
            begin = polylines[(-1, start)]
            for end, final_weight in ends.items():
                finish = polylines[(end, -1)]
                if np.linalg.norm(begin[0]-finish[-1]) < 0.025:
                    closure_ends[end] = final_weight+variation(np.concatenate([finish[-3:], begin[:3]]))
            candidate, candidate_cost = shortest_path([s.cost for s in segments], edges, {start: weight}, closure_ends)
            if candidate is not None and (best is None or candidate_cost < best[1]):
                best = (candidate, candidate_cost)
        if best is None:
            raise SolveError("no_path", "二维闭合笔触没有可闭合的深度组合，请取消闭合或调整笔触。")
        path, cost = best
    pieces = [polylines[(-1, path[0])][:-1]]
    previous = None
    for index in path:
        if previous is not None:
            pieces.append(polylines[(previous, index)][1:-1])
        pieces.append(segments[index].points)
        previous = index
    pieces.append(polylines[(path[-1], -1)][1:])
    points = np.concatenate(pieces)
    if len(points) != len(pixels):
        raise SolveError("numerical_failure", "段图路径未覆盖完整笔触。")
    if closed:
        points[[0, -1]] = (points[0]+points[-1])/2
    return points, path, cost, edges
