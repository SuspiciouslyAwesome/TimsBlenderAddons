"""The Re-circle operator.

Resamples one or more selected closed edge loops into flat, regular circles at
a chosen vertex count, keeping the mesh connected:

  * count unchanged -> the loop verts are just redistributed evenly onto the
    best-fit circle (topology preserved exactly), and
  * count changed   -> the face strips touching the loop are removed and the
    new circle is bridged back to the neighbouring loops (clean quads where the
    counts match, a triangle transition where they differ). Single-face n-gon
    caps are rebuilt, and several selected loops are resampled to the same count
    so the strips between them come out as clean quads.

Pick the circular edge *loop* that runs around the object (Alt-click), not the
perpendicular edge *ring* of rungs.
"""

from collections import defaultdict, deque

import bmesh
from bpy.types import Operator
from bpy.props import IntProperty, FloatProperty, BoolProperty

from .geometry import (
    fit_plane, resample_ring, align_ring, bridge_face_indices, EPS,
)


# --------------------------------------------------------------- topology utils

def _edge_between(a, b):
    for e in a.link_edges:
        if e.other_vert(a) is b:
            return e
    return None


def _loop_edges(loop):
    edges = []
    n = len(loop)
    for i in range(n):
        e = _edge_between(loop[i], loop[(i + 1) % n])
        if e is not None:
            edges.append(e)
    return edges


def _order_cycle(comp, adj, start):
    """Walk a 2-regular component into an ordered vertex list, or None."""
    ordered = [start]
    prev, cur = None, start
    while True:
        nxts = [n for n in adj[cur] if n is not prev]
        if not nxts:
            return None
        nxt = nxts[0]
        if nxt is start:
            break
        ordered.append(nxt)
        prev, cur = cur, nxt
        if len(ordered) > len(comp):
            return None
    return ordered if len(ordered) == len(comp) else None


def _cycles_from_adjacency(adj):
    """Split a vertex adjacency map into ordered clean cycles.

    Returns (cycles, n_bad) where each cycle is an ordered vertex list and
    n_bad counts components that weren't simple closed loops.
    """
    cycles, n_bad, seen = [], 0, set()
    for s in list(adj):
        if s in seen:
            continue
        comp, stack = set(), [s]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            for y in adj[x]:
                if y not in comp:
                    stack.append(y)
        seen |= comp
        if any(len(adj[v]) != 2 for v in comp):
            n_bad += 1
            continue
        ordered = _order_cycle(comp, adj, s)
        if ordered is None:
            n_bad += 1
        else:
            cycles.append(ordered)
    return cycles, n_bad


def selected_cycles(bm):
    """Ordered closed loops formed by the currently selected edges."""
    adj = defaultdict(list)
    for e in bm.edges:
        if e.select:
            a, b = e.verts
            adj[a].append(b)
            adj[b].append(a)
    return _cycles_from_adjacency(adj)


def boundary_cycles(verts):
    """Ordered closed loops among `verts`, following boundary/wire edges only.

    An edge counts as boundary when it has fewer than two faces — exactly the
    edges exposed after the loop's face strips are removed.
    """
    vset = set(verts)
    adj = defaultdict(list)
    for v in vset:
        if not v.is_valid:
            continue
        for e in v.link_edges:
            if len(e.link_faces) >= 2:
                continue
            ov = e.other_vert(v)
            if ov in vset:
                adj[v].append(ov)
    cycles, _ = _cycles_from_adjacency(adj)
    return cycles


# ------------------------------------------------------------ face orientation

def _edge_dir(face, a, b):
    """+1 if `face` traverses a->b, -1 if b->a, 0 if a,b aren't a face edge."""
    vs = face.verts[:]
    n = len(vs)
    for k in range(n):
        if vs[k] is a and vs[(k + 1) % n] is b:
            return 1
        if vs[k] is b and vs[(k + 1) % n] is a:
            return -1
    return 0


def orient_new_faces(new_faces):
    """Make freshly built faces agree with each other and with existing geometry.

    Walks each connected component of `new_faces`, flipping neighbours so they
    stay consistent across shared edges. Each component is seeded from an
    adjacent pre-existing face when one exists; components with no existing
    neighbour are returned so the caller can fall back to recalc.
    """
    new_set = set(new_faces)
    visited = set()
    unseeded = []

    def seed(face):
        # Orient `face` opposite an existing neighbour across a shared edge.
        for e in face.edges:
            for g in e.link_faces:
                if g is face or g in new_set or not g.is_valid:
                    continue
                a, b = e.verts
                if _edge_dir(face, a, b) == _edge_dir(g, a, b):
                    face.normal_flip()
                return True
        return False

    for start in new_set:
        if start in visited:
            continue
        visited.add(start)
        if not seed(start):
            unseeded.append(start)
        queue = deque([start])
        while queue:
            f = queue.popleft()
            for e in f.edges:
                for g in e.link_faces:
                    if g is f or g not in new_set or g in visited:
                        continue
                    a, b = e.verts
                    if _edge_dir(f, a, b) == _edge_dir(g, a, b):
                        g.normal_flip()
                    visited.add(g)
                    queue.append(g)
    return unseeded


# --------------------------------------------------------------- the operator

class MESH_OT_recircle(Operator):
    """Resample the selected edge loop(s) into flat, regular circles"""
    bl_idname = "mesh.recircle"
    bl_label = "Re-circle"
    bl_options = {'REGISTER', 'UNDO'}

    segments: IntProperty(
        name="Vertices",
        description="Target number of vertices for each selected loop",
        default=32, min=3, soft_max=256,
    )
    radius: FloatProperty(
        name="Radius",
        description="Circle radius; 0 uses the loop's average radius",
        default=0.0, min=0.0,
    )
    offset: FloatProperty(
        name="Offset",
        description="Rotate the new vertices around the circle",
        default=0.0, subtype='ANGLE',
    )
    fill_caps: BoolProperty(
        name="Rebuild Caps",
        description="Re-create a single n-gon cap where the loop bounded one",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return (context.mode == 'EDIT_MESH'
                and obj is not None
                and obj.type == 'MESH')

    def invoke(self, context, event):
        # Default the vertex count to the first selected loop's current count so
        # the redo panel starts at "no change" and the user dials from there.
        obj = context.edit_object
        if obj is not None:
            bm = bmesh.from_edit_mesh(obj.data)
            cycles, _ = selected_cycles(bm)
            if cycles:
                self.segments = len(cycles[0])
        return self.execute(context)

    # ------------------------------------------------------------ execute

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)

        loops, n_bad = selected_cycles(bm)
        if not loops:
            msg = "No closed edge loop selected. Alt-click the circular loop " \
                  "that runs around the object."
            if n_bad:
                msg = "Selection isn't a clean closed loop " \
                      "(open chain or branching edges)."
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        target = self.segments
        need_rebuild = any(len(loop) != target for loop in loops)

        if not need_rebuild:
            self._regularize_in_place(bm, loops)
            bmesh.update_edit_mesh(obj.data)
            n = len(loops)
            extra = f" ({n_bad} skipped)" if n_bad else ""
            self.report({'INFO'},
                        f"Regularized {n} loop(s) at {target} verts{extra}.")
            return {'FINISHED'}

        ok = self._rebuild(bm, loops)
        if not ok:
            self.report({'ERROR'},
                        "A selected loop is degenerate (can't fit a plane).")
            return {'CANCELLED'}

        bmesh.update_edit_mesh(obj.data)
        n = len(loops)
        extra = f" ({n_bad} skipped)" if n_bad else ""
        self.report({'INFO'},
                    f"Re-circled {n} loop(s) to {target} verts{extra}.")
        return {'FINISHED'}

    # ------------------------------------------------------------ in-place path

    def _regularize_in_place(self, bm, loops):
        """Count unchanged: slide each loop's verts onto its best-fit circle."""
        for loop in loops:
            centroid, normal = fit_plane([v.co for v in loop])
            if normal is None or normal.length < EPS:
                continue
            positions, _ = resample_ring(
                centroid, normal, [v.co for v in loop], len(loop),
                radius=self.radius, offset=self.offset,
            )
            for v, p in zip(loop, positions):
                v.co = p

    # ------------------------------------------------------------ rebuild path

    def _rebuild(self, bm, loops):
        """Count changed: delete the loops' face strips and bridge new circles in."""
        target = self.segments
        loop_vset = set(v for loop in loops for v in loop)

        # 1. Gather everything we need *before* mutating the mesh.
        infos = []
        strip_faces = set()
        for loop in loops:
            centroid, normal = fit_plane([v.co for v in loop])
            if normal is None or normal.length < EPS:
                return False
            positions, _ = resample_ring(
                centroid, normal, [v.co for v in loop], target,
                radius=self.radius, offset=self.offset,
            )
            loopset = set(loop)
            faces = set()
            for e in _loop_edges(loop):
                faces.update(e.link_faces)
            # Neighbour verts on the outward side(s): reachable via non-loop
            # edges and not part of *any* selected loop.
            nbrs = set()
            for v in loop:
                for e in v.link_edges:
                    ov = e.other_vert(v)
                    if ov not in loop_vset:
                        nbrs.add(ov)
            # A single-face n-gon cap: a face made entirely of this loop's verts.
            has_cap = any(set(f.verts) <= loopset for f in faces)
            strip_faces.update(faces)
            infos.append({"loop": loop, "positions": positions,
                          "nbrs": nbrs, "has_cap": has_cap,
                          "new": None})

        # Adjacency between selected loops (they share a strip face).
        loop_index = {v: i for i, loop in enumerate(loops) for v in loop}
        adjacent_pairs = set()
        for f in strip_faces:
            idxs = {loop_index[v] for v in f.verts if v in loop_index}
            for i in idxs:
                for j in idxs:
                    if i < j:
                        adjacent_pairs.add((i, j))

        # 2. Build the new ring verts (positions captured above are plain Vectors).
        for info in infos:
            ring = [bm.verts.new(p) for p in info["positions"]]
            n = len(ring)
            for i in range(n):
                # bmesh raises if the edge already exists; it can't here.
                bm.edges.new((ring[i], ring[(i + 1) % n]))
            info["new"] = ring

        # 3. Remove the old face strips (faces only) then the old loop verts.
        bmesh.ops.delete(bm, geom=[f for f in strip_faces if f.is_valid],
                         context='FACES_ONLY')
        bmesh.ops.delete(bm, geom=[v for v in loop_vset if v.is_valid],
                         context='VERTS')

        new_faces = []

        # 4a. Bridge new rings to the exposed neighbour loops. A neighbour ring
        # that sits between two rebuilt loops is claimed by both, so it must be
        # bridged on each side; hence "every owner that fully claims it", not
        # just the nearest one.
        all_nbrs = set(v for info in infos for v in info["nbrs"] if v.is_valid)
        for bloop in boundary_cycles(all_nbrs):
            bset = set(bloop)
            owners = [info for info in infos if bset <= info["nbrs"]]
            if not owners:
                best = max(infos, key=lambda info: len(bset & info["nbrs"]))
                if bset & best["nbrs"]:
                    owners = [best]
            for owner in owners:
                new_faces += self._bridge(bm, owner["new"], bloop)

        # 4b. Bridge between adjacent selected loops (same count -> clean quads).
        for i, j in adjacent_pairs:
            new_faces += self._bridge(bm, infos[i]["new"], infos[j]["new"])

        # 4c. Rebuild single-face caps on the new rings.
        if self.fill_caps:
            for info in infos:
                if not info["has_cap"]:
                    continue
                try:
                    new_faces.append(bm.faces.new(info["new"]))
                except ValueError:
                    pass  # cap already closed by bridging on that side

        # 5. Make the new faces face the right way.
        unseeded = orient_new_faces(new_faces)
        if unseeded:
            comp = [f for f in new_faces if f.is_valid]
            bmesh.ops.recalc_face_normals(bm, faces=comp)

        # 6. Select just the new rings.
        for v in bm.verts:
            v.select = False
        for info in infos:
            for v in info["new"]:
                if v.is_valid:
                    v.select = True
        bm.select_flush(True)
        return True

    def _bridge(self, bm, ring_new, ring_other):
        """Loft `ring_new` (ordered) to `ring_other` (ordered), return new faces."""
        ring_other = [v for v in ring_other if v.is_valid]
        if len(ring_new) < 2 or len(ring_other) < 2:
            return []

        pos_new = [v.co for v in ring_new]
        order = align_ring(pos_new, [v.co for v in ring_other])
        rb = [ring_other[k] for k in order]
        faces_idx = bridge_face_indices(pos_new, [v.co for v in rb])

        faces = []
        for spec in faces_idx:
            verts = [ring_new[i] if tag == 'a' else rb[i] for tag, i in spec]
            if len(set(verts)) != len(verts):
                continue
            try:
                faces.append(bm.faces.new(verts))
            except ValueError:
                pass  # face already exists
        return faces
