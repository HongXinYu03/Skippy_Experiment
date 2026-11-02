from collections import OrderedDict
from hashlib import sha256
import json
from threading import Lock
from time import perf_counter
import numpy as np
from .models import SolveInput, RedrawInput, SolveError
from .geometry import Scene, unit
from .stroke import resample_2d, prepare, candidate_vertices, project
from .graph import build_segments, solve_graph
from .smoothing import smooth, catmull_rom

_cache = OrderedDict()      # 用于缓存场景和中间结果的有序字典
_cache_lock = Lock()        # 用于保护缓存的锁

def polyline_distances(points, line):
    # 计算点集到线段的最短距离
    a, edge = line[:-1], np.diff(line, axis=0)
    differences = points[:, None, :]-a[None, :, :]
    t = np.clip(np.sum(differences*edge, axis=2)/np.maximum(np.sum(edge*edge, axis=1), 1e-12), 0, 1)
    return np.min(np.linalg.norm(differences-t[:, :, None]*edge, axis=2), axis=1)


def snapshot(request, anchors=None):
    # 准备场景并缓存中间结果以加速重复请求
    payload = request.model_dump(exclude={"locks"})
    payload["options"].pop("iterations")            # iterations are not relevant to the cached data, as they only affect smoothing after the graph is solved.
    payload["anchors"] = np.asarray(anchors).tolist() if anchors is not None else None
    key = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key], True
    start = perf_counter()
    scene = Scene(request.objects)
    if not scene.objects:
        raise SolveError("insufficient_geometry", "请先添加至少一个参与推断的几何体。")
    pixels = resample_2d(request.points, request.options.spacing, request.options.max_points)
    normalized_anchors = scene.normalize(anchors) if anchors is not None else None
    if anchors is not None:
        pixels[[0, -1]] = project(request.camera, np.asarray(anchors))
    closed = request.options.closed if request.options.closed is not None else np.linalg.norm(pixels[0]-pixels[-1]) < 8
    if closed:
        pixels[[0, -1]] = (pixels[0]+pixels[-1])/2
    origin, directions, on, heights, sequences, cuts = prepare(scene, request.camera, pixels, normalized_anchors)
    a = perf_counter()
    candidates = candidate_vertices(scene, origin, directions, on, heights, request.options.restore_pruned, normalized_anchors)
    refinements = 0
    # Resolve primitive creases numerically without accepting penetrating chords.
    # Base points remain equidistant; only problematic visible chords get extra rays.
    for _ in range(6):
        insert = []
        for i in range(len(pixels)-1):
            if i+1 in cuts or not (candidates[i] and candidates[i+1]):
                continue
            first_candidate, next_candidate = candidates[i][0], candidates[i+1][0]
            if first_candidate["object"] == next_candidate["object"] and not scene.clear([first_candidate["point"], next_candidate["point"]], request.options.collision_tolerance):
                insert.append(i)
        if not insert:
            break
        if len(pixels)+len(insert) > request.options.max_points:
            break
        indices = set(insert)
        pixels = np.array([point for i, p in enumerate(pixels) for point in ([p, (p+pixels[i+1])/2] if i in indices else [p])])
        refinements += len(insert)
        origin, directions, on, heights, sequences, cuts = prepare(scene, request.camera, pixels, normalized_anchors)
        candidates = candidate_vertices(scene, origin, directions, on, heights, request.options.restore_pruned, normalized_anchors)
    b = perf_counter()
    segments, rejected = build_segments(scene, candidates, sequences, cuts, request.options)
    c = perf_counter()
    data = dict(scene=scene, pixels=pixels, origin=origin, directions=directions, on=on, heights=heights,
                sequences=sequences, cuts=cuts, candidates=candidates, segments=segments, rejected=rejected,
                closed=closed, refinements=refinements, edge_cache={}, timings={"stroke_ms": (a-start)*1000, "candidates_ms": (b-a)*1000, "segments_ms": (c-b)*1000})
    with _cache_lock:
        _cache[key] = data
        while len(_cache) > 12:
            _cache.popitem(last=False)
    return data, False

"""
The main inference function for Skippy, which takes a SolveInput request and optional anchor points,
and returns a dictionary containing the results of the inference, including the solved path, metrics, and debug information. 
 """
def solve(request: SolveInput, anchors=None):
    started = perf_counter()
    data, cached = snapshot(request, anchors)       # 准备场景
    scene, options = data["scene"], request.options

    graph_start = perf_counter()
    raw, path, cost, edges = solve_graph(scene, list(data["segments"]), data["origin"], data["directions"],     # 建立图并求解最优路径
        data["pixels"], data["on"], data["cuts"], request.locks, options, data["closed"], data["edge_cache"])
    segments = sorted(data["segments"], key=lambda s: (s.start, s.end, s.id))                                   # 对路径段进行排序，按起点、终点和ID排序
    graph_end = perf_counter()

    smoothed, target_height, trace = smooth(raw, scene, data["origin"], data["directions"], data["heights"],    # 对路径进行平滑处理，计算目标高度和能量轨迹
        options.iterations, options.collision_tolerance, data["closed"])
    
    render, parameter = catmull_rom(smoothed, data["closed"])
    display_kind = "catmull_rom"

    # 检查最终曲线是否穿入几何体，如果穿入则尝试使用平滑后的曲线作为渲染结果
    if not scene.clear(render, options.collision_tolerance):
        render, parameter = smoothed, np.arange(len(smoothed))
        display_kind = "validated_polyline"
    if not scene.clear(render, options.collision_tolerance):
        raise SolveError("no_path", "最终曲线未通过穿入检查，请调整笔触或几何布局。")
    
    projected = project(request.camera, scene.world(render))    # 将渲染曲线投影到相机坐标系中
    distances = polyline_distances(projected, data["pixels"])   # 计算渲染曲线的投影与原始像素点的距离
    reverse = polyline_distances(data["pixels"], projected)     # 计算原始像素点到渲染曲线的投影距离
    height = np.interp(parameter, np.arange(len(target_height)), target_height)     # 计算渲染曲线的高度信息
    height_error = abs(scene.distance(render)-height)           # 计算渲染曲线的高度误差

    tracks = {}                                                 # 用于存储每个轨迹的相关信息，包括起点、终点、深度、是否被选中等
    for segment in segments:
        tracks.setdefault(segment.track, {"id": segment.track, "group": segment.group, "start": segment.start,
            "end": segment.end, "depth": float(np.mean(np.linalg.norm(segment.points-data["origin"], axis=1))*scene.scale),
            "selected": False, "points": scene.world(segment.points).tolist()})
    for index in path:
        tracks[segments[index].track]["selected"] = True

    all_candidates = [[{"rank": v["rank"], "depth": v["depth"]*scene.scale, "point": scene.world(v["point"]).tolist(),
                       "object": v["object"], "pruned": v["pruned"]} for v in row] for row in data["candidates"]]
    timings = dict(data["timings"], graph_ms=(graph_end-graph_start)*1000,
                   smooth_ms=(perf_counter()-graph_end)*1000, total_ms=(perf_counter()-started)*1000)

    # 返回结果字典，包含渲染曲线、原始曲线、平滑曲线、显示类型、选中的轨迹、各种度量指标和调试信息
    return dict(status="ok", closed=bool(data["closed"]),
        points=scene.world(render).tolist(), raw_points=scene.world(raw).tolist(), control_points=scene.world(smoothed).tolist(),
        display_kind=display_kind, selected_tracks=[segments[i].track for i in path],
        metrics={"projection_rms_px": float(np.sqrt(np.mean(distances**2))), "projection_p95_px": float(np.quantile(distances, .95)),
            "projection_max_px": float(np.max(distances)), "reverse_projection_max_px": float(np.max(reverse)),
            "height_p95_normalized": float(np.quantile(height_error, .95)), "height_mean_world": float(np.mean(height_error)*scene.scale),
            "minimum_clearance_world": float(np.min(scene.distance(render))*scene.scale), "path_cost": cost,
            "nodes": len(segments), "edges": len(edges), "sample_count": len(raw), "cache_hit": cached, **timings},
        debug={"pixels": data["pixels"].tolist(), "on": data["on"].tolist(), "heights": (data["heights"]*scene.scale).tolist(),
            "sequences": data["sequences"], "discontinuities": data["cuts"], "candidates": all_candidates,
            "tracks": list(tracks.values()), "rejected_connections": data["rejected"], "smoothing": trace, "crease_refinements": data["refinements"],
            "graph": {"nodes": [{"id": s.id, "track": s.track, "start": s.start, "end": s.end, "cost": s.cost, "selected": i in path} for i, s in enumerate(segments)],
                      "edges": [{"from": segments[a].id, "to": segments[b].id, "cost": weight} for (a, b), weight in edges.items()]}})

""" 
    Redraw function for Skippy, which allows users to redraw a portion of the path while keeping the endpoints fixed.
    It takes a RedrawInput request and returns a dictionary containing the results of the redraw operation, including the updated path, metrics, and debug information.
 """
def redraw(request: RedrawInput):
    """Fixed endpoints enter candidate generation and remain fixed in smoothing."""
    original = np.asarray(request.original)
    if not 0 <= request.start < request.end < len(original):
        raise SolveError("invalid_range", "局部重画区间无效。")
    a, b = original[request.start], original[request.end]
    input_request = request.solve.model_copy(deep=True)
    input_request.options.closed = False
    endpoints = project(input_request.camera, np.array([a, b]))
    input_request.points[0], input_request.points[-1] = tuple(endpoints[0]), tuple(endpoints[1])
    result = solve(input_request, anchors=np.array([a, b]))
    replacement = np.asarray(result["points"])
    scene = Scene(request.solve.objects)
    if not scene.clear(scene.normalize(replacement), request.solve.options.collision_tolerance):
        raise SolveError("no_path", "重画结果无法在保持端点连接时避开几何，请从更靠近原曲线端点的位置重画。")
    # Enforce the same visible projection budget after fixed-endpoint correction.
    distance = polyline_distances(project(request.solve.camera, replacement), np.asarray(request.solve.points))
    if np.quantile(distance, .95) > 8:
        raise SolveError("no_path", "重画偏离输入过大，请让新笔触的起止点靠近所选原曲线区间。")
    combined = np.concatenate([original[:request.start], replacement, original[request.end+1:]])
    result["points"] = combined.tolist()
    result["control_points"] = combined.tolist()
    result["display_kind"] = "fixed_endpoint_redraw"
    result["metrics"]["redraw_projection_p95_px"] = float(np.quantile(distance, .95))
    result["redraw_range"] = [request.start, request.start+len(replacement)-1]
    result["metric_scope"] = "replacement_segment"
    return result
