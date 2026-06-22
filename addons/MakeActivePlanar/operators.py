"""The Make Active Planar operator."""

import bmesh
import numpy as np
from mathutils import Vector
from bpy.types import Operator
from bpy.props import EnumProperty, FloatProperty, BoolProperty

from .geometry import fit_plane, max_plane_deviation, EPS


class MESH_OT_make_active_planar(Operator):
    """Flatten the active face by moving only the active vertex / active edge verts"""
    bl_idname = "mesh.make_active_planar"
    bl_label = "Make Active Planar"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(
        name="Move",
        items=[
            ('AUTO', "Auto", "Use the active edge's verts if an edge is active, "
                             "otherwise the active vertex"),
            ('VERT', "Active Vertex", "Move only the active vertex"),
            ('EDGE', "Active Edge", "Move both verts of the active edge"),
        ],
        default='AUTO',
    )
    fallback_free: BoolProperty(
        name="Free Move If Can't Slide",
        description="If no connected (spoke) edge can slide the vertex onto the plane, "
                    "project the vertex straight onto the plane instead",
        default=True,
    )
    planar_threshold: FloatProperty(
        name="Planar Threshold",
        description="Max vertex distance from the best-fit plane to still call the face planar",
        default=1e-4, min=0.0, soft_max=0.01, precision=6,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.edit_object is not None

    # ------------------------------------------------------------------ helpers

    def _active_face(self, bm):
        af = bm.faces.active
        if af is not None and af.select:
            return af
        sel = [f for f in bm.faces if f.select]
        return sel[0] if sel else None

    def _target_verts(self, bm, face):
        """Resolve which vert(s) of `face` to move based on the active element / mode."""
        active = bm.select_history.active
        fv = set(face.verts)
        want = self.mode

        if want in ('AUTO', 'EDGE') and isinstance(active, bmesh.types.BMEdge):
            if set(active.verts) <= fv:
                return list(active.verts)
            if want == 'EDGE':
                return []
        if want in ('AUTO', 'VERT') and isinstance(active, bmesh.types.BMVert):
            if active in fv:
                return [active]
            if want == 'VERT':
                return []

        # Explicit mode but the active element wasn't usable: fall back to selection.
        if want == 'EDGE':
            for e in face.edges:
                if e.select and all(v.select for v in e.verts):
                    return list(e.verts)
        if want == 'VERT':
            for v in face.verts:
                if v.select:
                    return [v]
        return []

    def _solve_vertex(self, v, face, centroid, normal):
        """Return (new_co: Vector, method: 'SLIDE'|'FREE')."""
        c = np.asarray(centroid, dtype=np.float64)
        n = np.asarray(normal, dtype=np.float64)
        v_co = np.asarray(v.co, dtype=np.float64)
        face_edges = set(face.edges)

        best = None  # (distance_moved, new_co)
        for e in v.link_edges:
            if e in face_edges:
                continue  # face edges lead to in-plane neighbours -> they just collapse
            w_co = np.asarray(e.other_vert(v).co, dtype=np.float64)
            d = v_co - w_co  # slide line direction (through the spoke edge)
            denom = float(d @ n)
            if abs(denom) < EPS:
                continue  # edge parallel to plane: no intersection
            s = float((c - w_co) @ n) / denom
            new = w_co + s * d
            dist = float(np.linalg.norm(new - v_co))
            if best is None or dist < best[0]:
                best = (dist, new)

        if best is not None:
            return Vector(best[1]), 'SLIDE'

        # Fallback: orthogonal projection onto the plane.
        proj = v_co - float((v_co - c) @ n) * n
        return Vector(proj), 'FREE'

    # ------------------------------------------------------------------ execute

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)

        face = self._active_face(bm)
        if face is None:
            self.report({'ERROR'}, "No active/selected face found.")
            return {'CANCELLED'}

        move_verts = self._target_verts(bm, face)
        if not move_verts:
            self.report({'ERROR'}, "No usable active vertex/edge on the active face.")
            return {'CANCELLED'}

        fixed = [v for v in face.verts if v not in move_verts]
        if len(fixed) < 3:
            self.report({'ERROR'},
                        "Need >= 3 fixed verts to define the target plane "
                        "(this face has too few verts for the chosen move).")
            return {'CANCELLED'}

        centroid, normal = fit_plane([v.co for v in fixed])
        if normal is None or normal.length < EPS:
            self.report({'ERROR'}, "Fixed vertices are degenerate; cannot define a plane.")
            return {'CANCELLED'}

        n_slid = n_free = 0
        for v in move_verts:
            new_co, method = self._solve_vertex(v, face, centroid, normal)
            if method == 'SLIDE':
                v.co = new_co
                n_slid += 1
            elif self.fallback_free:
                v.co = new_co
                n_free += 1
            # else: leave the vertex untouched

        bmesh.update_edit_mesh(obj.data)

        # Honest planarity check over the WHOLE face after the move.
        c2, n2 = fit_plane([v.co for v in face.verts])
        dev = max_plane_deviation([v.co for v in face.verts], c2, n2) if n2 else float('inf')
        is_planar = dev <= self.planar_threshold

        parts = ["Face IS planar" if is_planar else "Face is NOT planar",
                 f"(max deviation {dev:.6g})."]
        if n_free:
            parts.append(f"{n_free} vert(s) free-projected (no slide edge).")
        self.report({'INFO'} if is_planar else {'WARNING'}, " ".join(parts))
        return {'FINISHED'}
