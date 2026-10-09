"""The maths of a circular fillet between two edges meeting at a corner.

Nothing here touches bmesh — it works on plain `mathutils.Vector`s, so the
operator can decide what to do with the result.

The picture, for a corner V with neighbours A and B:

    d1 = unit(A - V), d2 = unit(B - V), theta = angle between them.

The circle of radius r tangent to both edges has its centre on the bisector, at
r / sin(theta/2) from V. It touches the edges at distance t = r / tan(theta/2)
from V (the *tangent distance*), and the arc between the touch points sweeps
pi - theta.
"""

import math
from collections import namedtuple

EPS = 1e-6

# Corners flatter / sharper than this (radians) have no usable fillet.
MIN_ANGLE = 1e-3

# d1, d2: unit vectors from the corner along its two edges.
# theta: the angle between them.
# max_radius: the largest radius whose tangent points still lie on the edges.
Corner = namedtuple("Corner", "d1 d2 theta max_radius")


def corner_of(vertex, a, b):
    """Describe the corner at `vertex` between the edges to `a` and `b`.

    Returns None when an edge has no length or the two edges are (anti)parallel
    — there is no circle to fit in either case.
    """
    v1, v2 = a - vertex, b - vertex
    len1, len2 = v1.length, v2.length
    if len1 < EPS or len2 < EPS:
        return None
    d1, d2 = v1 / len1, v2 / len2
    theta = math.acos(max(-1.0, min(1.0, d1.dot(d2))))
    if theta < MIN_ANGLE or math.pi - theta < MIN_ANGLE:
        return None
    return Corner(d1, d2, theta, min(len1, len2) * math.tan(theta / 2.0))


def segment_count(corner, num_points):
    """How many segments the arc gets.

    `num_points` is the vertex count of the *full* circle; the arc takes the
    whole number of segments that comes closest to that spacing, so both ends
    land exactly on the tangent points and the steps are all equal.
    """
    sweep = math.pi - corner.theta
    return max(1, int(round(sweep * max(num_points, 3) / (2.0 * math.pi))))


def arc_points(vertex, corner, radius, num_points):
    """Points along the fillet arc, from the tangent point on edge 1 to edge 2.

    Includes both tangent points (first and last). Evenly spaced by angle.
    """
    d1, d2, theta = corner.d1, corner.d2, corner.theta
    half = theta / 2.0
    t = radius / math.tan(half)
    t1 = vertex + d1 * t
    t2 = vertex + d2 * t
    center = vertex + (d1 + d2).normalized() * (radius / math.sin(half))

    r1 = (t1 - center) / radius
    r2 = (t2 - center) / radius
    # r2 split into the part along r1 and the part across it: w is the second
    # axis of the arc's own frame, so the sweep is a plain cos/sin.
    w = (r2 - r1 * r1.dot(r2)).normalized()

    sweep = math.pi - theta
    n = segment_count(corner, num_points)
    pts = []
    for i in range(n + 1):
        s = sweep * i / n
        pts.append(center + (r1 * math.cos(s) + w * math.sin(s)) * radius)
    pts[0], pts[-1] = t1, t2            # exact, not just within rounding
    return pts
