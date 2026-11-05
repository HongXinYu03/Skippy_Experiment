"""Active-contour energy, analytic gradient and safeguarded descent (§6.2)."""
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import factorized
from .geometry import unit
from .progress import tracked, stage, progress_iter, current_step

WEIGHTS = np.array([0.0039, 0.011, 0.982, 0.0019])
WEIGHTS /= WEIGHTS.sum()


def resample_3d(points, heights, directions):
    """ 
    对3D点集进行重采样
    参数：
        points: 点集，二维数组，每一行表示一个点的坐标
        heights: 高度数组，表示每个点的高度信息
        directions: 方向数组，表示每个点的方向信息
    返回值：
        q: 重采样后的点集
        h: 重采样后的高度数组
        r: 重采样后的方向数组
    """
    lengths = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    valid = np.r_[True, np.diff(lengths) > 1e-9]
    lengths = lengths[valid]
    p, h, r = points[valid], heights[valid], directions[valid]
    targets = np.linspace(0, lengths[-1], len(points))
    q = np.column_stack([np.interp(targets, lengths, p[:, axis]) for axis in range(3)])
    h = np.interp(targets, lengths, h)
    r = unit(np.column_stack([np.interp(targets, lengths, r[:, axis]) for axis in range(3)]))
    return q, h, r



def operators(n, closed):
    """ 
    内部能量最小化优化器
    参数：
        n: 点的数量
        closed: 是否闭合曲线
    返回值：
        first: 一阶差分矩阵
        second: 二阶差分矩阵
    """
    step = 1/max(1, n-1)
    if closed:
        first = sparse.lil_matrix((n, n))
        second = sparse.lil_matrix((n, n))
        for i in range(n):
            first[i, i], first[i, (i+1)%n] = -1/step, 1/step
            second[i, i] = -2/step**2
            second[i, (i-1)%n] = 1/step**2
            second[i, (i+1)%n] = 1/step**2
        return first.tocsr(), second.tocsr()
    return (sparse.diags([-np.ones(n-1), np.ones(n-1)], [0, 1], shape=(n-1, n))/step,
            sparse.diags([np.ones(n-2), -2*np.ones(n-2), np.ones(n-2)], [0, 1, 2], shape=(n-2, n))/step**2)


def energy_gradient(points, scene, origin, targets, heights, first, second):
    """ 
    计算点集的总能量（连续性、平滑、投影及高度）和梯度
    参数：
        points: 点集，二维数组，每一行表示一个点的坐标
        scene: 场景对象
        origin: 原点坐标，三维向量
        targets: 目标方向，二维数组，每一行表示一个点的目标方向
        heights: 高度数组，表示每个点的高度信息
        first: 一阶差分矩阵
        second: 二阶差分矩阵
    返回值：
        energy: 总能量
        gradient: 梯度
    """
    alpha, beta, gamma, delta = WEIGHTS
    n = len(points)
    d1, d2 = first@points, second@points
    offset = points-origin
    length = np.linalg.norm(offset, axis=1)
    direction = unit(offset)
    cosine = np.sum(direction*targets, axis=1)
    distance = scene.distance(points)
    error = distance-heights
    absolute = np.sqrt(error**2+1e-8)

    Internal = alpha*np.mean(np.sum(d1*d1, axis=1))+beta*np.mean(np.sum(d2*d2, axis=1))
    External = gamma*np.mean(1-cosine)+delta*np.mean(absolute-1e-4)
    energy = Internal+External

    gradient = 2*alpha/len(d1)*(first.T@d1)+2*beta/len(d2)*(second.T@d2)
    gradient -= gamma/n*(targets-cosine[:, None]*direction)/np.maximum(length[:, None], 1e-9)
    gradient += delta/n*(error/absolute)[:, None]*scene.gradient(points)
    
    return float(energy), gradient


@tracked("三维重采样与软约束平滑")
def smooth(points, scene, origin, directions, heights, iterations, tolerance, closed=False, *, fixed_endpoints=False):
    """ 
    对点集进行平滑处理，以最小化内部能量和外部约束
    参数：
        points: 点集，二维数组，每一行表示一个点的坐标
        scene: 场景对象
        origin: 原点坐标，三维向量
        directions: 目标方向，二维数组，每一行表示一个点的目标方向
        heights: 高度数组，表示每个点的高度信息
        iterations: 迭代次数
        tolerance: 容差
        closed: 是否闭合曲线
        fixed_endpoints: 仅局部重画时固定开放曲线的两个连接端点
    返回值：
        points: 平滑后的点集
        heights: 平滑后的高度信息
        info: 包含能量和迭代次数的信息
    """
    with stage("三维等距重采样") as task:
        q, h, targets = resample_3d(points, heights, directions)
        # Resampling chords can penetrate a surface; retain the feasible original if necessary.
        if not scene.clear(q, tolerance):
            q, h, targets = points.copy(), heights.copy(), directions.copy()
            task.update(detail="重采样弦段穿入，保留原可行折线")
        if closed:
            q, h, targets = q[:-1], h[:-1], targets[:-1]
    if len(q) < 4:
        current_step().finish("不足 4 个点，跳过迭代平滑", total=0)
        return points, heights, {"energies": [], "iterations": 0}
    # Height is preserved by its energy penalty, with no hard height band or snap.
    with stage("能量与预条件器初始化", total=3) as task:
        first, second = operators(len(q), closed)       # 一阶和二阶差分矩阵
        alpha, beta, _, _ = WEIGHTS
        internal = 2*alpha/first.shape[0]*(first.T@first)+2*beta/second.shape[0]*(second.T@second)
        keep_endpoints = fixed_endpoints and not closed
        active = np.arange(1, len(q)-1) if keep_endpoints else np.arange(len(q))
        endpoints = q[[0, -1]].copy() if keep_endpoints else None
        task.update(1)
        # A fixed positive preconditioner preserves a descent direction for the same energy.
        matrix = internal+sparse.eye(len(q))*.25
        inverse = factorized(matrix.tocsc()[active][:, active])
        task.update(2)
        energy, gradient = energy_gradient(q, scene, origin, targets, h, first, second)
        task.update(3, detail=f"初始能量 {energy:.8g}，固定连接端点={keep_endpoints}")
    trace = [energy]
    with stage("平滑迭代", total=iterations) as task:
        reason = "达到迭代上限" if iterations else "迭代上限为 0，仅保留重采样"
        for iteration in range(iterations):
            descent = np.zeros_like(q)
            descent[active] = inverse(gradient[active])
            magnitude = np.max(np.linalg.norm(descent, axis=1))
            if magnitude < 1e-9:
                reason = "下降方向已收敛"
                break
            descent *= min(1, .025/max(magnitude, 1e-12))
            accepted = False
            step = 1.0
            for backtrack in range(16):
                task.update(iteration, detail=f"迭代 {iteration+1}/{iterations}，回溯 {backtrack+1}/16，当前能量 {energy:.8g}")
                proposal = q-step*descent
                # Keep the drawing rays as a hard feasibility constraint. The printed
                # angular weight alone is not invariant to scene/pixel discretization.
                depth = np.sum((proposal-origin)*targets, axis=1)
                proposal = origin+depth[:, None]*targets
                if keep_endpoints:
                    proposal[[0, -1]] = endpoints
                if np.all(np.sum((proposal-origin)*targets, axis=1) > 1e-8):
                    new_energy, new_gradient = energy_gradient(proposal, scene, origin, targets, h, first, second)
                    check = np.vstack([proposal, proposal[:1]]) if closed else proposal
                    if new_energy < energy-1e-12 and scene.clear(check, tolerance):
                        q, energy, gradient = proposal, new_energy, new_gradient
                        trace.append(energy)
                        accepted = True
                        break
                step *= .5
            task.update(len(trace)-1)
            if not accepted or abs(trace[-1]-trace[-2]) < 1e-9:
                reason = "回溯未找到可行下降步" if not accepted else "能量变化低于收敛阈值"
                break
        task.finish(f"有效迭代 {len(trace)-1}/{iterations}；{reason}；最终能量 {energy:.8g}", total=len(trace)-1)
    if closed:
        q, h = np.vstack([q, q[:1]]), np.r_[h, h[0]]
    return q, h, {"energies": trace, "iterations": len(trace)-1}

@tracked("Catmull-Rom 曲线采样")
def catmull_rom(points, closed=False, subdivisions=5):
    """Uniform cubic Catmull--Rom; return samples and interpolated knot positions."""
    p = points[:-1] if closed else points
    result, parameters = [], []
    for i in progress_iter(range(len(p) if closed else len(p)-1)):
        a, b = p[i], p[(i+1)%len(p)]
        before = p[(i-1)%len(p)] if closed or i else 2*a-b
        after = p[(i+2)%len(p)] if closed or i+2 < len(p) else 2*b-a
        m0, m1 = (b-before)/2, (after-a)/2
        for t in np.arange(subdivisions)/subdivisions:
            result.append((2*t**3-3*t*t+1)*a+(t**3-2*t*t+t)*m0+(-2*t**3+3*t*t)*b+(t**3-t*t)*m1)
            parameters.append(i+t)
    result.append(p[0] if closed else p[-1])
    parameters.append(len(p) if closed else len(p)-1)
    return np.array(result), np.array(parameters)
