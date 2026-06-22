"""Plane-fitting helpers for the Make Active Planar add-on."""

import numpy as np
from mathutils import Vector

EPS = 1e-9


def fit_plane(points):
    """Least-squares best-fit plane.

    Returns (centroid: Vector, normal: Vector | None). Normal is None when
    fewer than 3 points are supplied.
    """
    pts = np.asarray(points, dtype=np.float64)
    centroid = pts.mean(axis=0)
    if len(pts) < 3:
        return Vector(centroid), None
    # SVD of the mean-centred points: the right-singular vector with the
    # smallest singular value is the plane normal (direction of least variance).
    _, _, vh = np.linalg.svd(pts - centroid)
    return Vector(centroid), Vector(vh[2])


def max_plane_deviation(points, centroid, normal):
    """Largest absolute signed distance of any point from the plane."""
    c = np.asarray(centroid, dtype=np.float64)
    n = np.asarray(normal, dtype=np.float64)
    pts = np.asarray(points, dtype=np.float64)
    return float(np.abs((pts - c) @ n).max())
