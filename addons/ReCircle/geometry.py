"""Pure geometry helpers for the Re-circle add-on.

Nothing here touches bmesh; everything works on plain sequences of
`mathutils.Vector` (or 3-tuples) so it can be reasoned about (and tested) on
its own. The bmesh/topology walking lives in topology.py and operators.py.
"""

import math

import numpy as np
from mathutils import Vector

EPS = 1e-9


def fit_plane(points):
    """Least-squares best-fit plane through `points`.

    Returns (centroid: Vector, normal: Vector | None). Normal is None when
    fewer than 3 points are supplied (a line/point has no unique plane).
    """
    pts = np.asarray(points, dtype=np.float64)
    centroid = pts.mean(axis=0)
    if len(pts) < 3:
        return Vector(centroid), None
    # SVD of the mean-centred points: the right-singular vector with the
    # smallest singular value is the plane normal (direction of least variance).
    _, _, vh = np.linalg.svd(pts - centroid)
    return Vector(centroid), Vector(vh[2])


def plane_basis(normal):
    """Return an orthonormal (u, v) pair spanning the plane with `normal`."""
    n = Vector(normal).normalized()
    # Pick a reference axis that isn't (near) parallel to the normal.
    ref = Vector((0.0, 0.0, 1.0)) if abs(n.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    u = (ref - ref.dot(n) * n)
    if u.length < EPS:
        ref = Vector((0.0, 1.0, 0.0))
        u = ref - ref.dot(n) * n
    u.normalize()
    v = n.cross(u).normalized()
    return u, v


# ------------------------------------------------------------- circle fitting

def fit_circle(points):
    """Best-fit circle through `points`, for full loops *and* partial arcs.

    Returns (center: Vector | None, normal: Vector | None, radius: float).

    Two stages: a best-fit plane (SVD), then an algebraic (Kasa) least-squares
    circle inside that plane. Unlike "centroid + mean radius" this stays correct
    when the points only cover part of the circle — the centroid of an arc sits
    well off the real centre, which is exactly the case the arc tools need.

    Falls back to centroid + mean radius when the in-plane fit is unstable
    (near-collinear points), so callers always get something usable.
    """
    pts = [Vector(p) for p in points]
    if len(pts) < 3:
        return None, None, 0.0

    centroid, normal = fit_plane(pts)
    if normal is None or normal.length < EPS:
        return None, None, 0.0
    n = Vector(normal).normalized()
    u, v = plane_basis(n)

    xy = np.array([[(p - centroid).dot(u), (p - centroid).dot(v)] for p in pts],
                  dtype=np.float64)
    x, y = xy[:, 0], xy[:, 1]
    spread = float(np.max(np.hypot(x, y))) if len(x) else 0.0

    def _fallback():
        r = float(np.mean(np.hypot(x, y))) if len(x) else 0.0
        return Vector(centroid), n, r

    # Kasa fit: solve  A*cx + B*cy + C = x^2 + y^2  in the least-squares sense.
    A = np.column_stack([x, y, np.ones(len(x))])
    rhs = x * x + y * y
    try:
        sol, *_ = np.linalg.lstsq(A, rhs, rcond=None)
    except np.linalg.LinAlgError:
        return _fallback()

    cx, cy = sol[0] / 2.0, sol[1] / 2.0
    r_sq = sol[2] + cx * cx + cy * cy
    if not np.isfinite(r_sq) or r_sq <= 0.0:
        return _fallback()
    radius = math.sqrt(r_sq)
    # A nearly straight chain fits a huge, meaningless circle — reject it.
    if not np.isfinite(radius) or radius > 1.0e4 * max(spread, EPS):
        return _fallback()

    return Vector(centroid) + float(cx) * u + float(cy) * v, n, float(radius)


def circle_frame(center, normal):
    """(center: Vector, u, v) — the in-plane basis used to place points."""
    u, v = plane_basis(normal)
    return Vector(center), u, v


def point_angles(center, u, v, points):
    """Angles (radians, in the (u, v) frame) of `points` around `center`."""
    c = Vector(center)
    out = []
    for p in points:
        d = Vector(p) - c
        out.append(math.atan2(d.dot(v), d.dot(u)))
    return out


def point_radii(center, u, v, points):
    """In-plane distances of `points` from `center`."""
    c = Vector(center)
    out = []
    for p in points:
        d = Vector(p) - c
        out.append(math.hypot(d.dot(u), d.dot(v)))
    return out


def unwrap_angles(angles):
    """Make an ordered angle sequence continuous (no +/-pi jumps).

    Each step is wrapped into (-pi, pi] and accumulated, so an arc walked in
    order comes out monotonic and its total sweep is just last - first.
    """
    if not angles:
        return []
    out = [float(angles[0])]
    for a in angles[1:]:
        d = (float(a) - out[-1] + math.pi) % (2.0 * math.pi) - math.pi
        out.append(out[-1] + d)
    return out


def point_at_angle(center, u, v, radius, angle):
    """The point on the circle (center, u, v, radius) at `angle`."""
    return Vector(center) + radius * (math.cos(angle) * u + math.sin(angle) * v)


def _winding_sign(angles):
    """+1 if the (ordered) angle sequence advances counter-clockwise, else -1.

    Sums the signed wrapped steps between consecutive angles; a closed loop
    traversed once accumulates roughly +2pi (CCW) or -2pi (CW).
    """
    total = 0.0
    n = len(angles)
    for i in range(n):
        d = angles[(i + 1) % n] - angles[i]
        # Wrap into (-pi, pi] so a single lap sums to +/-2pi, not a big number.
        d = (d + math.pi) % (2.0 * math.pi) - math.pi
        total += d
    return 1.0 if total >= 0.0 else -1.0


def resample_ring(centroid, normal, ordered_points, count,
                  radius=0.0, offset=0.0, center=None):
    """Evenly sample a flat, regular circle that stands in for `ordered_points`.

    * `centroid`, `normal` describe the fitted plane.
    * `ordered_points` are the original loop verts *in loop order* — used to
      pick the radius (mean in-plane distance), the winding direction, and the
      phase so the new vert 0 lands near the old vert 0.
    * `radius` > 0 overrides the auto (mean) radius.
    * `offset` is an extra start-angle rotation, in radians.
    * `center` overrides `centroid` as the circle centre (e.g. a fitted one).

    Returns (positions: list[Vector], radius: float). The positions are wound
    the same way as `ordered_points`, so a bridge between old and new won't
    twist.
    """
    u, v = plane_basis(normal)
    c = Vector(center) if center is not None else Vector(centroid)

    radii = point_radii(c, u, v, ordered_points)
    angles = point_angles(c, u, v, ordered_points)

    if radius <= 0.0:
        radius = sum(radii) / len(radii) if radii else 0.0

    winding = _winding_sign(angles) if len(angles) >= 3 else 1.0
    start = (angles[0] if angles else 0.0) + winding * offset

    positions = []
    step = 2.0 * math.pi / count
    for k in range(count):
        a = start + winding * step * k
        positions.append(c + radius * (math.cos(a) * u + math.sin(a) * v))
    return positions, radius


def resample_arc(center, normal, ordered_points, count, radius=0.0):
    """Evenly space `count` points along the arc spanned by `ordered_points`.

    The first and last sample sit at the arc's own end angles, so the endpoints
    stay put (up to the radius change) and the chain still meets whatever it was
    attached to. `radius` > 0 overrides the mean in-plane radius.

    Returns (positions: list[Vector], radius: float).
    """
    c, u, v = circle_frame(center, normal)
    if count < 2 or len(ordered_points) < 2:
        return [], radius

    radii = point_radii(c, u, v, ordered_points)
    if radius <= 0.0:
        radius = sum(radii) / len(radii) if radii else 0.0

    ang = unwrap_angles(point_angles(c, u, v, ordered_points))
    a0, a1 = ang[0], ang[-1]

    positions = [
        point_at_angle(c, u, v, radius, a0 + (a1 - a0) * k / (count - 1))
        for k in range(count)
    ]
    return positions, radius


def arc_gap_angles(ordered_angles, extra_count=0):
    """Angles that close an arc into a full circle.

    `ordered_angles` are the arc's own angles (unwrapped). The remaining sweep
    is filled with points at the arc's average angular step, continuing past the
    last point and stopping short of the first — the caller wires the last new
    point back to the arc's start vertex.

    `extra_count` > 0 forces that many points into the gap instead.
    Returns a list of angles (may be empty when the arc is already closed).
    """
    if len(ordered_angles) < 2:
        return []
    a0, a1 = ordered_angles[0], ordered_angles[-1]
    sweep = a1 - a0
    if abs(sweep) < EPS:
        return []
    direction = 1.0 if sweep > 0.0 else -1.0
    step = sweep / (len(ordered_angles) - 1)
    gap = direction * 2.0 * math.pi - sweep      # what's left of the full turn

    if extra_count > 0:
        n_new = int(extra_count)
    else:
        n_new = max(int(round(abs(gap / step))) - 1, 0)
    if n_new <= 0:
        return []

    gap_step = gap / (n_new + 1)
    return [a1 + gap_step * (k + 1) for k in range(n_new)]


def bridge_chain_face_indices(pos_a, pos_b):
    """Loft two ordered, *open* polylines into a face list.

    The closed-ring version wraps around; this one stops at the ends, which is
    what an arc needs — its two end vertices stay welded to whatever the run was
    attached to. Same greedy shortest-diagonal walk, so unequal counts come out
    as a clean triangle transition; equal counts come out as quads.

    Returns faces as lists of ('a'|'b', index) tags, like `bridge_face_indices`.
    """
    a, b = len(pos_a), len(pos_b)
    if a < 2 or b < 2:
        return []

    if a == b:
        return [[('a', i), ('a', i + 1), ('b', i + 1), ('b', i)]
                for i in range(a - 1)]

    faces = []
    i = j = 0
    while i < a - 1 or j < b - 1:
        if j >= b - 1:
            advance_a = True
        elif i >= a - 1:
            advance_a = False
        else:
            diag_a = (pos_a[i + 1] - pos_b[j]).length
            diag_b = (pos_b[j + 1] - pos_a[i]).length
            advance_a = diag_a <= diag_b

        if advance_a:
            faces.append([('a', i), ('a', i + 1), ('b', j)])
            i += 1
        else:
            faces.append([('a', i), ('b', j + 1), ('b', j)])
            j += 1
    return faces


def circle_positions(center, normal, count, start_angle=0.0, radius=1.0,
                     winding=1.0):
    """`count` evenly spaced points around a full circle, from `start_angle`."""
    c, u, v = circle_frame(center, normal)
    step = winding * 2.0 * math.pi / count
    return [point_at_angle(c, u, v, radius, start_angle + step * k)
            for k in range(count)]


def align_ring(positions_ref, positions_move):
    """Roll/flip `positions_move` so it best lines up with `positions_ref`.

    Both are ordered rings of Vectors (not necessarily equal length). Returns
    the index order (list of ints into `positions_move`) that, read in
    sequence, starts nearest `positions_ref[0]` and runs the same way round.

    Used to phase-match a neighbour loop to the freshly built circle before
    bridging, so the transition faces don't spiral.
    """
    m = len(positions_move)
    if m == 0:
        return []
    ref0 = Vector(positions_ref[0])

    # Nearest neighbour vert to our start.
    best_i = min(range(m), key=lambda i: (Vector(positions_move[i]) - ref0).length_squared)

    # Decide direction: compare the forward vs backward next vert against
    # positions_ref[1] (the direction we want to travel).
    if len(positions_ref) > 1 and m > 2:
        ref1 = Vector(positions_ref[1])
        fwd = Vector(positions_move[(best_i + 1) % m])
        bwd = Vector(positions_move[(best_i - 1) % m])
        forward = (fwd - ref1).length_squared <= (bwd - ref1).length_squared
    else:
        forward = True

    if forward:
        return [(best_i + k) % m for k in range(m)]
    return [(best_i - k) % m for k in range(m)]


def bridge_face_indices(pos_a, pos_b):
    """Loft two ordered, phase-aligned closed rings into a face list.

    `pos_a` / `pos_b` are lists of Vectors (the two rings, already rolled so
    index 0 corresponds and both run the same direction). Returns a list of
    faces, each a list of ('a'|'b', index) tags.

    Equal-length rings become clean quads. Unequal rings are lofted with a
    greedy two-pointer walk that, at each step, adds the triangle with the
    shorter new diagonal — the standard way to bridge polylines of differing
    vertex counts.
    """
    a, b = len(pos_a), len(pos_b)
    if a < 2 or b < 2:
        return []

    if a == b:
        return [[('a', i), ('a', (i + 1) % a),
                 ('b', (i + 1) % b), ('b', i)] for i in range(a)]

    faces = []
    i = j = 0
    # We must lay down exactly `a` steps along ring a and `b` along ring b.
    while i < a or j < b:
        ni = (i + 1) % a
        nj = (j + 1) % b
        if j >= b:                         # only ring-a edges remain
            advance_a = True
        elif i >= a:                       # only ring-b edges remain
            advance_a = False
        else:
            # Triangle A: consume an a-edge -> diagonal (a[ni] .. b[j]).
            # Triangle B: consume a b-edge -> diagonal (b[nj] .. a[i]).
            diag_a = (pos_a[ni] - pos_b[j % b]).length
            diag_b = (pos_b[nj] - pos_a[i % a]).length
            advance_a = diag_a <= diag_b

        if advance_a:
            faces.append([('a', i % a), ('a', ni), ('b', j % b)])
            i += 1
        else:
            faces.append([('a', i % a), ('b', nj), ('b', j % b)])
            j += 1
    return faces
