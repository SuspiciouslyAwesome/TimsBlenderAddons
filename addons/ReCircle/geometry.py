"""Pure geometry helpers for the Re-circle add-on.

Nothing here touches bmesh; everything works on plain sequences of
`mathutils.Vector` (or 3-tuples) so it can be reasoned about (and tested) on
its own. The bmesh/topology walking lives in operators.py.
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
                  radius=0.0, offset=0.0):
    """Evenly sample a flat, regular circle that stands in for `ordered_points`.

    * `centroid`, `normal` describe the fitted plane.
    * `ordered_points` are the original loop verts *in loop order* — used to
      pick the radius (mean in-plane distance), the winding direction, and the
      phase so the new vert 0 lands near the old vert 0.
    * `radius` > 0 overrides the auto (mean) radius.
    * `offset` is an extra start-angle rotation, in radians.

    Returns (positions: list[Vector], radius: float). The positions are wound
    the same way as `ordered_points`, so a bridge between old and new won't
    twist.
    """
    u, v = plane_basis(normal)
    c = Vector(centroid)

    radii = []
    angles = []
    for p in ordered_points:
        d = Vector(p) - c
        du, dv = d.dot(u), d.dot(v)
        radii.append(math.hypot(du, dv))
        angles.append(math.atan2(dv, du))

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
