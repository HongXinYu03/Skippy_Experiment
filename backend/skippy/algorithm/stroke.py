"""Sections 4.1--4.2: sampling, sequence classification and height."""
import numpy as np
from .geometry import unit
from .models import SolveError
from .progress import tracked, stage, progress_iter, current_step


def resample_2d(points, spacing, maximum):
    """  
    二维重采样
    参数：
        points: 输入的点集，二维数组，每一行表示一个点的坐标
        spacing: 重采样的间距，表示相邻点之间的最小距离
        maximum: 重采样后的最大点数，确保不会超过该值
    返回值：
        返回重采样后的点集，二维数组，每一行表示一个点的坐标
    """
    p = np.asarray(points, float)                                               # 讲输入的点转换为浮点数的numpy数组，输入的点是一个二维数组，每一行表示一个点的坐标
    p = p[np.r_[True, np.linalg.norm(np.diff(p, axis=0), axis=1) > 1e-6]]       # 过滤掉相邻点之间距离小于1e-6的点，确保至少有两个不同的点
    if len(p) < 2:
        raise SolveError("insufficient_input", "笔触至少需要两个不同的点。")
    
    lengths = np.r_[0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]   # 计算每个点到起点的累积距离
    count = max(3, min(maximum, int(np.ceil(lengths[-1]/spacing))+1))           # 计算重采样后的点数，确保至少有3个点，并且不超过maximum，同时根据spacing计算所需的点数
    s = np.linspace(0, lengths[-1], count)
    return np.column_stack([np.interp(s, lengths, p[:, i]) for i in range(2)])


def rays(camera, pixels):
    """ 
    从相机视角到世界坐标的射线
    参数：
        camera: 相机对象
        pixels: 像素坐标，二维数组，每一行表示一个像素的坐标
    返回值：
        origin: 射线的起点，三维向量
        directions: 射线的方向，三维向量
    """
    world = np.asarray(camera.world).reshape(4, 4, order="F")
    projection = np.asarray(camera.projection).reshape(4, 4, order="F")
    p = np.asarray(pixels)
    clip = np.column_stack([2*p[:, 0]/camera.width-1, 1-2*p[:, 1]/camera.height, np.full(len(p), 0.5), np.ones(len(p))])
    local = clip@np.linalg.inv(projection).T
    local /= local[:, 3:4]
    points = local@world.T
    return world[:3, 3], unit(points[:, :3]-world[:3, 3])


def project(camera, points):
    """ 
    将世界坐标投影到相机坐标
    参数：
        camera: 相机对象
        points: 世界坐标，二维数组，每一行表示一个点的坐标
    返回值：
        pixels: 相机坐标，二维数组，每一行表示一个像素的坐标
    """
    world = np.asarray(camera.world).reshape(4, 4, order="F")
    projection = np.asarray(camera.projection).reshape(4, 4, order="F")
    p = np.column_stack([points, np.ones(len(points))])@np.linalg.inv(world).T@projection.T
    ndc = p[:, :2]/p[:, 3:4]
    return np.column_stack([(ndc[:, 0]+1)*camera.width/2, (1-ndc[:, 1])*camera.height/2])


@tracked("笔触分类与高度计算")
def prepare(scene, camera, pixels, anchors=None):
    """ 
    准备笔触数据,检查几何体投影并计算高度信息
    参数：
        scene: 场景对象
        camera: 相机对象
        pixels: 像素坐标，二维数组，每一行表示一个像素的坐标
        anchors: 锚点坐标，二维数组，每一行表示一个锚点的坐标
    返回值：
        无
    """
    origin_world, directions = rays(camera, pixels)
    origin = scene.normalize(origin_world)
    with stage("射线求交与 on/off 分类", total=len(directions)) as task:
        hits = [scene.intervals(origin, r) for r in progress_iter(directions)]
        task.update(detail=f"命中 {sum(bool(h) for h in hits)} / {len(directions)} 条射线")
    on = np.array([bool(h) for h in hits])
    if anchors is not None:
        on[[0, -1]] = True
    if not np.any(on):
        raise SolveError("insufficient_geometry", "笔触没有经过几何体投影，请经过物体轮廓或添加临时引导物。")
    depths = np.array([h[0][0] if h else np.nan for h in hits])
    if anchors is not None:
        depths[[0, -1]] = np.linalg.norm(np.asarray(anchors)-origin, axis=1)
    discontinuities = []
    with stage("深度不连续检测", total=len(pixels)-1) as task:
        for i in progress_iter(range(len(pixels)-1)):
            if not (on[i] and on[i+1]):
                continue
            screen_delta = np.linalg.norm(pixels[i+1]-pixels[i])/max(camera.width, camera.height)
            jump = abs(depths[i+1]-depths[i])
            if jump < max(3*screen_delta, 0.025):
                continue
            _, middle_ray = rays(camera, [(pixels[i]+pixels[i+1])/2])
            middle = scene.intervals(origin, middle_ray[0])
            if not middle or abs(middle[0][0]-(depths[i]+depths[i+1])/2) > 0.4*jump:
                discontinuities.append(i+1)
        task.update(detail=f"检测到 {len(discontinuities)} 处深度不连续")
    boundaries = sorted(set(int(i) for i in [0, len(pixels), *discontinuities, *list(np.flatnonzero(on[1:] != on[:-1])+1)]))
    sequences = [{"start": a, "end": b-1, "on": bool(on[a])} for a, b in zip(boundaries[:-1], boundaries[1:])]
    heights = np.zeros(len(pixels))
    with stage("off 段离物距离", total=int(np.count_nonzero(~on))):
        for sequence in sequences:
            if not sequence["on"]:
                a, b = sequence["start"], sequence["end"]
                distances = []
                for r in directions[a:b+1]:
                    distances.append(scene.ray_distance(origin, r))
                    current_step().advance()
                heights[a:b+1] = max(distances)
    # A depth split is still on geometry: interpolate between the actual off
    # neighbors across the entire contiguous run, rather than resetting to zero.
    run_boundaries = [0, *[int(i) for i in np.flatnonzero(on[1:] != on[:-1])+1], len(pixels)]
    with stage("on 段高度插值", total=len(run_boundaries)-1):
        for a, end in progress_iter(list(zip(run_boundaries[:-1], run_boundaries[1:]))):
            if on[a]:
                b = end-1
                left = heights[a-1] if a else 0.0
                right = heights[b+1] if b+1 < len(pixels) else 0.0
                if anchors is not None:
                    if a == 0:
                        left = max(0.0, scene.distance(anchors[0])[0])
                    if b == len(pixels)-1:
                        right = max(0.0, scene.distance(anchors[1])[0])
                heights[a:b+1] = np.linspace(left, right, b-a+1)
    group = 0
    for sequence in sequences:
        if sequence["on"]:
            sequence["group"] = group
            group += 1
    return origin, directions, on, heights, sequences, discontinuities




@tracked("候选顶点与梯度计算")
def candidate_vertices(scene, origin, directions, on, heights, restore_pruned, anchors=None):
    """ 
    计算候选顶点
    参数：
        scene: 场景对象
        origin: 射线的起点，三维向量
        directions: 射线的方向，三维向量
        on: 布尔数组，表示每个射线是否与几何体相交
        heights: 高度数组，表示每个射线的高度信息
        restore_pruned: 布尔值，表示是否恢复被修剪的顶点
        anchors: 锚点坐标，二维数组，每一行表示一个锚点的坐标
    返回值：
        candidates: 候选顶点列表，每个元素是一个字典，包含顶点的相关信息，包括样本索引、深度、位置、法线、对象ID
    """
    candidates = []
    for i, r in enumerate(progress_iter(directions)):
        row = []
        if on[i]:
            intervals = scene.intervals(origin, r, float(heights[i]))
            for k, (a, b, object_id) in enumerate(intervals):
                for side, depth in (("front", a), ("back", b)):
                    if row and abs(row[-1]["depth"]-depth) < 1e-7:
                        continue
                    point = origin+depth*r
                    pruned = side == "back" and k < len(intervals)-1
                    row.append({"sample": i, "rank": len(row), "depth": float(depth), "point": point,
                                "normal": unit(scene.gradient(point)[0]), "object": object_id,
                                "pruned": pruned, "active": restore_pruned or not pruned})
        if anchors is not None and i in (0, len(directions)-1):
            point = anchors[0 if i == 0 else 1]
            row = [{"sample": i, "rank": 0, "depth": float(np.linalg.norm(point-origin)), "point": point,
                    "normal": unit(scene.gradient(point)[0]), "object": "fixed_anchor", "pruned": False, "active": True}]
        candidates.append(row)
    current_step().update(detail=f"共 {sum(map(len, candidates))} 个深度候选")
    return candidates
