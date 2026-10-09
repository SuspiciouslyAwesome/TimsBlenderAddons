"""The Circle Fillet operator.

Select a corner vertex and run it: the two edges meeting there are rounded off
with a circular arc of the given Radius, tangent to both edges. The arc lives
in the plane of the two edges, so it works the same on a face corner and on a
bare wire corner (where the plane is simply the one the two edges span).

Which two edges? In order of preference:

  * exactly two of the vertex's edges are selected — those, however many other
    edges it has. This is how to fillet a vertex with more than two edges: edge-
    select the two. Where a face lies between them it is cut; where none does,
    the arc is built in the plane the two edges span (a virtual face) and the
    vertex's other edges are left alone. A vertex with one selected edge is just
    the end of a selection and is ignored;
  * the vertex has exactly two edges — those;
  * the active face (or the one selected face, or the only face) at the vertex
    — the two edges of that face's corner;
  * otherwise it's ambiguous and the vertex is skipped with a message.

Num Points is the vertex count of the *full* circle; the arc gets the whole
number of segments closest to that spacing, so its ends land exactly on the
tangent points.

Modes:

  * default — split the two edges at the tangent points, swap the corner for
    the arc in the corner face, and remove the cut-off corner vertex;
  * Arc Only — leave the mesh alone and just add the arc as new, unconnected
    vertices and edges;
  * Keep Vertex — leave the original corner vertex behind, as a loose vertex,
    so it can still be snapped to or inspected later.

Radii are in the mesh's local space; on an object with non-uniform scale the
arc is circular locally, not in the world.
"""

import bmesh
import bpy
from bpy.types import Operator
from bpy.props import BoolProperty, FloatProperty, IntProperty

from .geometry import EPS, arc_points, corner_of


# ------------------------------------------------------------ shared helpers

def corner_edges(bm, vert):
    """Fallback pick of the two edges to fillet at a vertex with no (or an
    unusable) edge selection, as (edges, problem). Exactly one is None.
    """
    edges = list(vert.link_edges)
    if len(edges) < 2:
        return None, "has fewer than two edges"
    if len(edges) == 2:
        return edges, None

    face = bm.faces.active
    if face is not None and vert in face.verts:
        faces = [face]
    else:
        faces = [f for f in vert.link_faces if f.select]
        if len(faces) != 1:
            faces = list(vert.link_faces) if len(vert.link_faces) == 1 else []
    if len(faces) == 1:
        loop = next(l for l in faces[0].loops if l.vert is vert)
        return [loop.link_loop_prev.edge, loop.edge], None

    return None, (f"has {len(edges)} edges — select the two to round "
                  f"(edge select), or make one of its faces active")


def resolve_edge(vert, edge, direction):
    """`edge` if it still starts at `vert`, else the edge of `vert` running the
    same way.

    Filleting a neighbouring corner can split a shared edge, after which the
    remembered edge may belong to the far half. The new piece at `vert` lies on
    the same line, so matching by direction finds it.
    """
    if edge.is_valid and vert in edge.verts:
        return edge
    best, best_dot = None, 0.999
    for e in vert.link_edges:
        d = e.other_vert(vert).co - vert.co
        if d.length < EPS:
            continue
        dot = d.normalized().dot(direction)
        if dot > best_dot:
            best, best_dot = e, dot
    return best


def select_only(bm, verts):
    """Clear the selection and select `verts` (plus the edges between them)."""
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = False
    for f in bm.faces:
        f.select = False
    for v in verts:
        if v.is_valid:
            v.select = True
    bm.select_flush(True)


def link_chain(bm, chain):
    """Make sure consecutive verts of `chain` are joined by an edge."""
    for a, b in zip(chain, chain[1:]):
        if a is not b and bm.edges.get((a, b)) is None:
            bm.edges.new((a, b))


# --------------------------------------------------------------- the operator

class MESH_OT_circle_fillet(Operator):
    """Round the selected corner vertices with a circular arc tangent to both edges"""
    bl_idname = "mesh.circle_fillet"
    bl_label = "Circle Fillet"
    bl_options = {'REGISTER', 'UNDO'}

    radius: FloatProperty(
        name="Radius",
        description="Radius of the fillet circle",
        default=0.25, min=0.0001, unit='LENGTH',
    )
    num_points: IntProperty(
        name="Num Points",
        description="Vertex count of the full circle. The arc gets the whole "
                    "number of segments that matches this spacing best, ends "
                    "included",
        default=32, min=4, soft_max=128,
    )
    arc_only: BoolProperty(
        name="Arc Only",
        description="Just add the arc as new geometry; don't cut the existing "
                    "edges or faces",
        default=False,
    )
    keep_vertex: BoolProperty(
        name="Keep Original Vertex",
        description="Leave the original corner vertex in place (as a loose "
                    "vertex) instead of removing it",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return (context.mode == 'EDIT_MESH'
                and obj is not None
                and obj.type == 'MESH')

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column(align=True)
        col.prop(self, "radius")
        col.prop(self, "num_points")
        col = layout.column(align=True)
        col.prop(self, "arc_only")
        sub = col.column()
        sub.active = not self.arc_only
        sub.prop(self, "keep_vertex")

    # ------------------------------------------------------------ execute

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)

        # Plan every corner before touching the mesh: filleting changes the
        # selection state. Each plan is (vertex, edge, edge, edge directions).
        #   * two selected edges at a vertex -> exactly those, at any valence;
        #   * one selected edge -> the end of a selection, not a corner;
        #   * otherwise the vertex-only rules in corner_edges().
        plans, problems = [], []
        for vert in [v for v in bm.verts if v.select]:
            chosen = [e for e in vert.link_edges if e.select]
            if len(chosen) == 1:
                continue
            if len(chosen) != 2:
                chosen, problem = corner_edges(bm, vert)
                if chosen is None:
                    problems.append("a vertex " + problem)
                    continue
            plans.append((vert, chosen[0], chosen[1],
                          [(e.other_vert(vert).co - vert.co).normalized()
                           for e in chosen]))
        if not plans and not problems:
            self.report({'ERROR'}, "Select a corner vertex, or two edges "
                                   "that share a vertex.")
            return {'CANCELLED'}

        done, clamped = 0, 0
        result = []
        for vert, edge_a, edge_b, (dir_a, dir_b) in plans:
            if not vert.is_valid:
                continue
            edge_a = resolve_edge(vert, edge_a, dir_a)
            edge_b = resolve_edge(vert, edge_b, dir_b)
            if edge_a is None or edge_b is None:
                problems.append("an edge changed shape under an earlier fillet")
                continue
            made, problem, was_clamped = self._fillet(bm, vert, edge_a, edge_b)
            if made is None:
                problems.append(problem)
                continue
            result += made
            clamped += was_clamped
            done += 1

        if not done:
            self.report({'ERROR'}, "Circle Fillet: " + problems[0])
            return {'CANCELLED'}

        select_only(bm, result)
        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)

        notes = []
        if clamped:
            notes.append(f"{clamped} radius reduced to fit the edge length")
        if problems:
            notes.append(f"{len(problems)} skipped ({problems[0]})")
        self.report({'WARNING'} if notes else {'INFO'},
                    f"Circle Fillet: {done} corner(s)"
                    + (" — " + "; ".join(notes) if notes else "") + ".")
        return {'FINISHED'}

    # ------------------------------------------------------------- fillet

    def _fillet(self, bm, vert, edge_a, edge_b):
        """Fillet one corner. Returns (arc verts, problem, radius_was_clamped)."""
        a, b = edge_a.other_vert(vert), edge_b.other_vert(vert)

        corner = corner_of(vert.co, a.co, b.co)
        if corner is None:
            return None, "the two edges are collinear or zero length", 0

        radius, clamped = self.radius, 0
        if not self.arc_only and radius > corner.max_radius:
            radius, clamped = corner.max_radius, 1
        pts = arc_points(vert.co.copy(), corner, radius, self.num_points)

        if self.arc_only:
            arc = [bm.verts.new(p) for p in pts]
            link_chain(bm, arc)
            return arc, None, clamped

        return self._cut(bm, vert, (edge_a, a), (edge_b, b), pts), None, clamped

    def _cut(self, bm, vert, side_a, side_b, pts):
        """Replace the corner at `vert` with the arc `pts` (tangent to tangent)."""
        (edge_a, a), (edge_b, b) = side_a, side_b
        corner_faces = [f for f in vert.link_faces
                        if edge_a in f.edges and edge_b in f.edges]

        # Cut the edges at the tangent points. Splitting keeps every face on
        # either side of the edge valid, which is what makes this safe on a
        # corner that isn't alone.
        t1 = self._tangent_vert(vert, edge_a, a, pts[0])
        t2 = self._tangent_vert(vert, edge_b, b, pts[-1])
        interior = [bm.verts.new(p) for p in pts[1:-1]]
        arc = [t1] + interior + [t2]

        for face in corner_faces:
            loop = next(l for l in face.loops if l.vert is vert)
            # The corner's neighbours in the face are now t1 and t2 (or a, b
            # when the radius used a whole edge). Whichever comes first in the
            # winding decides which way the arc is walked.
            forward = loop.link_loop_prev.vert is t1
            cycle = []
            for v in face.verts:
                if v is vert:
                    cycle += interior if forward else interior[::-1]
                else:
                    cycle.append(v)
            # New face first: it copies its attributes from the old one, which
            # must still exist at that point.
            try:
                bm.faces.new(cycle, face)
            except ValueError:
                pass            # an identical face is already there
            bmesh.ops.delete(bm, geom=[face], context='FACES_ONLY')

        link_chain(bm, arc)

        # The old corner: drop the two spokes where nothing else uses them,
        # then the vertex itself unless asked to keep it (or it's still needed).
        for t in (t1, t2):
            spoke = bm.edges.get((vert, t))
            if spoke is not None and not spoke.link_faces:
                bm.edges.remove(spoke)
        if not self.keep_vertex and not vert.link_edges:
            bm.verts.remove(vert)
        return arc

    @staticmethod
    def _tangent_vert(vert, edge, far, point):
        """The vertex at `point` on `edge`, splitting the edge unless it's `far`."""
        if (far.co - point).length < EPS:
            return far
        _, new_vert = bmesh.utils.edge_split(edge, vert, 0.5)
        new_vert.co = point         # the split fraction is only a placeholder
        return new_vert


classes = (MESH_OT_circle_fillet,)
