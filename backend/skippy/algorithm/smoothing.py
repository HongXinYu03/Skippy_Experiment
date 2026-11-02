"""Active-contour energy, analytic gradient and safeguarded descent (§6.2)."""
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import factorized
from .geometry import unit

WEIGHTS = np.array([0.0039, 0.011, 0.982, 0.0019])
WEIGHTS /= WEIGHTS.sum()

def resample_3d(points, heights, directions):
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
    energy = (alpha*np.mean(np.sum(d1*d1, axis=1))+beta*np.mean(np.sum(d2*d2, axis=1))
              +gamma*np.mean(1-cosine)+delta*np.mean(absolute-1e-4))
    gradient = 2*alpha/len(d1)*(first.T@d1)+2*beta/len(d2)*(second.T@d2)
    gradient -= gamma/n*(targets-cosine[:, None]*direction)/np.maximum(length[:, None], 1e-9)
    gradient += delta/n*(error/absolute)[:, None]*scene.gradient(points)
    return float(energy), gradient

def smooth(points, scene, origin, directions, heights, iterations, tolerance, closed=False):
    q, h, targets = resample_3d(points, heights, directions)
    # Resampling chords can penetrate a surface; retain the feasible original if necessary.
    if not scene.clear(q, tolerance):
        q, h, targets = points.copy(), heights.copy(), directions.copy()
    if closed:
        q, h, targets = q[:-1], h[:-1], targets[:-1]
    if len(q) < 4:
        return points, heights, {"energies": [], "iterations": 0}
    original_depth = np.sum((q-origin)*targets, axis=1)
    key = (targets.tobytes(), h.tobytes(), np.round(original_depth, 9).tobytes(), closed)
    bounds = scene.height_cache.get(key)
    if bounds is None:
        bounds = np.column_stack([original_depth, original_depth])
        for index, (r, height) in enumerate(zip(targets, h)):
            if not closed and index in (0, len(q)-1):
                continue
            outer = scene.intervals(origin, r, float(height)+.003)
            inner = scene.intervals(origin, r, max(0.0, float(height)-.003))
            feasible = []
            for low, high, _ in outer:
                cursor = low
                for a, b, _ in inner:
                    if b <= cursor or a >= high:
                        continue
                    if a > cursor:
                        feasible.append((cursor, min(a, high)))
                    cursor = max(cursor, b)
                if cursor < high:
                    feasible.append((cursor, high))
            if feasible:
                depth = original_depth[index]
                bounds[index] = min(feasible, key=lambda ab: abs(np.clip(depth, *ab)-depth))
        if len(scene.height_cache) >= 8:
            scene.height_cache.pop(next(iter(scene.height_cache)))
        scene.height_cache[key] = bounds
    def height_constraint(values):
        depth = np.sum((values-origin)*targets, axis=1)
        return origin+np.clip(depth, bounds[:, 0], bounds[:, 1])[:, None]*targets
    constrained = height_constraint(q)
    if scene.clear(np.vstack([constrained, constrained[:1]]) if closed else constrained, tolerance):
        q = constrained
    first, second = operators(len(q), closed)
    alpha, beta, _, _ = WEIGHTS
    internal = 2*alpha/first.shape[0]*(first.T@first)+2*beta/second.shape[0]*(second.T@second)
    active = np.arange(len(q)) if closed else np.arange(1, len(q)-1)
    # A fixed positive preconditioner preserves a descent direction for the same energy.
    matrix = internal+sparse.eye(len(q))*.25
    inverse = factorized(matrix.tocsc()[active][:, active])
    energy, gradient = energy_gradient(q, scene, origin, targets, h, first, second)
    trace = [energy]
    for _ in range(iterations):
        descent = np.zeros_like(q)
        descent[active] = inverse(gradient[active])
        magnitude = np.max(np.linalg.norm(descent, axis=1))
        if magnitude < 1e-9:
            break
        descent *= min(1, .025/max(magnitude, 1e-12))
        accepted = False
        step = 1.0
        for _ in range(16):
            proposal = q-step*descent
            # Keep the drawing rays as a hard feasibility constraint. The printed
            # angular weight alone is not invariant to scene/pixel discretization.
            depth = np.sum((proposal-origin)*targets, axis=1)
            proposal = origin+depth[:, None]*targets
            proposal = height_constraint(proposal)
            if np.all(np.sum((proposal-origin)*targets, axis=1) > 1e-8):
                new_energy, new_gradient = energy_gradient(proposal, scene, origin, targets, h, first, second)
                check = np.vstack([proposal, proposal[:1]]) if closed else proposal
                if new_energy < energy-1e-12 and scene.clear(check, tolerance):
                    q, energy, gradient = proposal, new_energy, new_gradient
                    trace.append(energy)
                    accepted = True
                    break
            step *= .5
        if not accepted or abs(trace[-1]-trace[-2]) < 1e-9:
            break
    if closed:
        q, h = np.vstack([q, q[:1]]), np.r_[h, h[0]]
    return q, h, {"energies": trace, "iterations": len(trace)-1}

def catmull_rom(points, closed=False, subdivisions=5):
    """Uniform cubic Catmull--Rom; return samples and interpolated knot positions."""
    p = points[:-1] if closed else points
    result, parameters = [], []
    for i in range(len(p) if closed else len(p)-1):
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
