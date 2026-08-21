"""The Re-circle operator.

One operator does the lot; everything is a switch in the redo panel (F9), and
with the panel untouched it deliberately changes *nothing* — Vertex Count 0
means "leave the density alone" and every action checkbox starts off. So you can
fire it on a selection, look at the read-out at the top of the panel (how many
runs, how many verts, the fitted radius) and then decide what to do.

What it works on: any closed edge loop, and any *open arc* of at least three
vertices. Arcs are fitted with a least-squares circle, so a fragment of a circle
still yields the centre and radius of the whole one.

The stages, in the order they run:

  * Vertex Count / Subdivide — resample the run. A closed loop is rebuilt and
    re-bridged as before; an arc keeps its two end vertices pinned and, when it
    carries faces, has its face strip re-bridged to the neighbouring run (n-gons
    that span the arc, such as a cap, are patched rather than lost).
  * Round to Circle — pull the existing vertices onto the fit without touching
    the count.
  * Complete to Circle — wire the missing sweep in (edges only, no faces). With
    a Vertex Count set, that count is the count of the *finished* circle.
  * Center Vertex / Support Circle / Cursor to Center — the non-destructive
    extras, each independent of the above.

Pick the circular edge *loop* that runs around the object (Alt-click), not the
perpendicular edge *ring* of rungs.
"""

import math
from collections import defaultdict

import bmesh
import bpy
from bpy.types import Operator
from bpy.props import (
    BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty,
)
from mathutils import Matrix

from .geometry import (
    EPS, align_ring, arc_gap_angles, bridge_chain_face_indices,
    bridge_face_indices, circle_frame, circle_positions, fit_circle, fit_plane,
    point_angles, point_at_angle, resample_arc, resample_ring, unwrap_angles,
)
from .topology import (
    boundary_chains, boundary_cycles, chain_edges, curve_edges, cycle_edges,
    edge_between, orient_new_faces, selected_curves,
)


# ------------------------------------------------------------ shared helpers

def gather_curves(bm):
    """Selected edges as ordered runs: [{'verts': [...], 'closed': bool}, ...].

    Returns (curves, n_bad); closed loops come first.
    """
    cycles, chains, n_bad = selected_curves(bm)
    curves = [{"verts": c, "closed": True} for c in cycles]
    curves += [{"verts": c, "closed": False} for c in chains]
    return curves, n_bad


def circle_of(verts, radius_override=0.0):
    """Best-fit circle through `verts` as a dict, or None if degenerate.

    Keys: center, normal, radius, u, v (the in-plane basis) — plus `fit_radius`,
    the radius before any override.
    """
    pts = [v.co.copy() for v in verts]
    center, normal, radius = fit_circle(pts)
    if center is None or normal is None or normal.length < EPS:
        return None
    c, u, v = circle_frame(center, normal)
    return {
        "center": c, "normal": normal, "u": u, "v": v,
        "radius": radius_override if radius_override > 0.0 else radius,
        "fit_radius": radius,
    }


def no_selection_message(n_bad):
    if n_bad:
        return ("Selection isn't a clean loop or arc "
                "(branching edges, or several runs meeting at a vertex).")
    return ("Select a closed edge loop or an open arc of edges "
            "(Alt-click the loop that runs around the object).")


def is_wire_run(verts, closed):
    """True when no edge of this run carries a face."""
    for e in curve_edges(verts, closed):
        if e.link_faces:
            return False
    return True


def is_free_run(verts, closed):
    """True when the run is wire *and* nothing else hangs off it.

    Such a run can be thrown away and rebuilt from scratch — nothing outside it
    references its vertices.
    """
    edges = set(curve_edges(verts, closed))
    for v in verts:
        if v.link_faces:
            return False
        for e in v.link_edges:
            if e not in edges:
                return False
    return True


def select_only(bm, verts):
    """Clear the selection and select `verts` (plus the edges between them)."""
    for v in bm.verts:
        v.select = False
    for v in verts:
        if v.is_valid:
            v.select = True
    bm.select_flush(True)


def sweep_of(fit, verts):
    """Signed angular sweep (radians) covered by an ordered run."""
    ang = unwrap_angles(point_angles(fit["center"], fit["u"], fit["v"],
                                    [v.co for v in verts]))
    return ang[-1] - ang[0], ang


def describe(entries, n_bad):
    """The read-out shown at the top of the redo panel."""
    if not entries:
        return ""
    loops = [e for e in entries if e["closed"]]
    arcs = [e for e in entries if not e["closed"]]
    first = entries[0]
    bits = []
    if len(entries) == 1:
        kind = "Loop" if first["closed"] else "Arc"
        bits.append(f"{kind} · {len(first['verts'])} verts")
        if not first["closed"]:
            sweep, _ = sweep_of(first["fit"], first["verts"])
            bits.append(f"{abs(math.degrees(sweep)):.0f}°")
    else:
        if loops:
            bits.append(f"{len(loops)} loop(s)")
        if arcs:
            bits.append(f"{len(arcs)} arc(s)")
    bits.append(f"r {first['fit']['fit_radius']:.4f}")
    if n_bad:
        bits.append(f"{n_bad} skipped")
    return " · ".join(bits)


def _face_run_split(cycle, runset):
    """Split a face's vertex cycle around the contiguous stretch in `runset`.

    Returns (part, rest) where `part` is that stretch in cycle order and `rest`
    is everything else, such that part + rest is the face's cycle again. Returns
    None when the run's vertices aren't one contiguous stretch (or are the whole
    face), which is the caller's cue not to touch this face.
    """
    n = len(cycle)
    flags = [v in runset for v in cycle]
    if not any(flags) or all(flags):
        return None
    starts = [i for i in range(n) if flags[i] and not flags[(i - 1) % n]]
    if len(starts) != 1:
        return None
    s = starts[0]
    part, k = [], s
    while flags[k % n]:
        part.append(cycle[k % n])
        k += 1
    rest = [cycle[(k + t) % n] for t in range(n - len(part))]
    return part, rest


def _split_edge(edge, start, cuts):
    """Cut `edge` `cuts` times, returning the new verts ordered from `start`.

    Each split hands back the vertex it created, and the next split continues on
    the far side of it, so the list comes out in walk order without any topology
    searching. `bmesh.utils.edge_split` keeps the adjacent faces valid and — the
    reason it's used over `bmesh.ops.subdivide_edges` — never invalidates the
    other elements we're still holding references to. Positions are placeholders;
    the caller moves them onto the circle.
    """
    made = []
    cur_edge, cur_vert = edge, start
    for _ in range(max(int(cuts), 0)):
        if cur_edge is None or not cur_edge.is_valid:
            break
        _, new_vert = bmesh.utils.edge_split(cur_edge, cur_vert, 0.5)
        made.append(new_vert)
        onward = [e for e in new_vert.link_edges if cur_vert not in e.verts]
        if not onward:
            break
        cur_edge, cur_vert = onward[0], new_vert
    return made


# --------------------------------------------------------------- the operator

class MESH_OT_recircle(Operator):
    """Rebuild the selected edge loop(s) or arc(s) as clean circles.

Open the redo panel (F9) to choose what to do — on its defaults nothing changes
    """
    bl_idname = "mesh.recircle"
    bl_label = "Re-circle"
    bl_options = {'REGISTER', 'UNDO'}

    # --- density -----------------------------------------------------------
    # SKIP_SAVE on every *action* property: a fresh invocation always starts
    # from "change nothing", instead of silently repeating the last run's edit.
    use_subdivide: BoolProperty(
        name="Subdivide",
        description="Add vertices by cutting the existing edges instead of "
                    "resampling to a vertex count",
        default=False, options={'SKIP_SAVE'},
    )
    vertex_count: IntProperty(
        name="Vertex Count",
        description="Resample every selected run to this many vertices. "
                    "0 leaves the geometry alone. With Complete to Circle on, "
                    "this is the vertex count of the finished full circle",
        default=0, min=0, soft_max=256, options={'SKIP_SAVE'},
    )
    cuts: IntProperty(
        name="Cuts",
        description="New vertices per existing edge, placed on the circle",
        default=1, min=1, soft_max=16, options={'SKIP_SAVE'},
    )
    round_to_circle: BoolProperty(
        name="Round to Circle",
        description="Pull the existing vertices onto the fitted circle without "
                    "changing how many there are",
        default=False, options={'SKIP_SAVE'},
    )
    complete: BoolProperty(
        name="Complete to Circle",
        description="Extend an arc into a full circle of edges (no faces). "
                    "Set a Vertex Count to choose the finished circle's density",
        default=False, options={'SKIP_SAVE'},
    )

    # --- shape -------------------------------------------------------------
    radius: FloatProperty(
        name="Radius",
        description="Circle radius; 0 uses the fitted radius",
        default=0.0, min=0.0,
    )
    offset: FloatProperty(
        name="Offset",
        description="Rotate the new vertices around the circle (closed loops)",
        default=0.0, subtype='ANGLE',
    )
    use_fit_center: BoolProperty(
        name="Fitted Center",
        description="Use the least-squares circle centre instead of the plain "
                    "centroid. Required for arcs; on a lopsided closed loop it "
                    "tracks the roundest circle rather than the average point",
        default=True,
    )
    fill_caps: BoolProperty(
        name="Rebuild Caps",
        description="Re-create a single n-gon cap where a loop bounded one",
        default=True,
    )

    # --- extras ------------------------------------------------------------
    add_center_vertex: BoolProperty(
        name="Center Vertex",
        description="Add a single vertex at the fitted centre",
        default=False, options={'SKIP_SAVE'},
    )
    connect_center: BoolProperty(
        name="Connect to Ends",
        description="Wire the centre vertex to the arc's two end vertices",
        default=False,
    )
    add_support_circle: BoolProperty(
        name="Support Circle",
        description="Add a separate object matching the circle, its plane and "
                    "its phase — something to snap to",
        default=False, options={'SKIP_SAVE'},
    )
    support_segments: IntProperty(
        name="Support Vertices",
        description="Vertices on the support circle; 0 continues the "
                    "selection's own spacing round the full circle",
        default=0, min=0, soft_max=512,
    )
    support_fill: EnumProperty(
        name="Support Fill",
        description="Whether the support circle carries a face",
        items=[
            ('NONE', "None", "Edges only, displayed as wire"),
            ('NGON', "N-Gon", "A single face — snap to its centre or surface"),
            ('TRIS', "Triangle Fan", "Fan from the centre — gives a centre vertex"),
        ],
        default='NONE',
    )
    support_name: StringProperty(
        name="Support Name",
        description="Name for the new object",
        default="SupportCircle",
    )
    snap_cursor: BoolProperty(
        name="Cursor to Center",
        description="Move the 3D cursor to the fitted centre. Geometry is not "
                    "touched",
        default=False, options={'SKIP_SAVE'},
    )
    cursor_align: BoolProperty(
        name="Align Cursor",
        description="Also point the cursor's Z axis along the circle's normal, "
                    "so what you add next lands in the circle's plane",
        default=True,
    )

    _info_text = ""

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return (context.mode == 'EDIT_MESH'
                and obj is not None
                and obj.type == 'MESH')

    # ------------------------------------------------------------ panel

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        if self._info_text:
            row = layout.row()
            row.alignment = 'CENTER'
            row.label(text=self._info_text, icon='MESH_CIRCLE')

        col = layout.column(align=True)
        col.prop(self, "use_subdivide")
        if self.use_subdivide:
            col.prop(self, "cuts")
        else:
            col.prop(self, "vertex_count")
        col.prop(self, "round_to_circle")
        col.prop(self, "complete")

        col = layout.column(align=True)
        col.prop(self, "radius")
        col.prop(self, "offset")
        col.prop(self, "use_fit_center")
        sub = col.column()
        sub.active = not self.use_subdivide and self.vertex_count > 0
        sub.prop(self, "fill_caps")

        layout.separator()
        col = layout.column(align=True)
        col.prop(self, "add_center_vertex")
        sub = col.column()
        sub.active = self.add_center_vertex
        sub.prop(self, "connect_center")

        col = layout.column(align=True)
        col.prop(self, "add_support_circle")
        sub = col.column(align=True)
        sub.active = self.add_support_circle
        sub.prop(self, "support_segments", text="Vertices")
        sub.prop(self, "support_fill", text="Fill")
        sub.prop(self, "support_name", text="Name")

        col = layout.column(align=True)
        col.prop(self, "snap_cursor")
        sub = col.column()
        sub.active = self.snap_cursor
        sub.prop(self, "cursor_align", text="Align Rotation")

    # ------------------------------------------------------------ execute

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)

        curves, n_bad = gather_curves(bm)
        entries = []
        for curve in curves:
            if len(curve["verts"]) < 3:
                continue
            fit = circle_of(curve["verts"], self.radius)
            if fit is None:
                continue
            entries.append({"verts": curve["verts"], "closed": curve["closed"],
                            "fit": fit})
        if not entries:
            type(self)._info_text = ""
            self.report({'ERROR'}, no_selection_message(n_bad))
            return {'CANCELLED'}

        # Snapshot for the panel read-out before anything moves.
        type(self)._info_text = describe(entries, n_bad)

        actions = []
        notes = []
        to_select = []

        self._run_density(bm, entries, actions, notes)
        self._run_round(entries, actions)
        self._run_complete(bm, entries, actions)

        for entry in entries:
            to_select += [v for v in entry["verts"] if v.is_valid]

        to_select += self._run_extras(context, obj, bm, entries, actions)

        if to_select:
            select_only(bm, to_select)
        bmesh.update_edit_mesh(obj.data)

        extra = f" ({n_bad} skipped)" if n_bad else ""
        tail = (" — " + "; ".join(notes)) if notes else ""
        if actions:
            self.report({'WARNING'} if notes else {'INFO'},
                        "Re-circle: " + "; ".join(actions) + extra + tail + ".")
        elif notes:
            self.report({'WARNING'}, "Re-circle: " + "; ".join(notes) + ".")
        else:
            self.report({'INFO'},
                        f"Re-circle: {self._info_text} — nothing changed. "
                        f"Set a Vertex Count or tick an option (F9){extra}.")
        return {'FINISHED'}

    # ------------------------------------------------------- stage: density

    def _run_density(self, bm, entries, actions, notes):
        """Resample or subdivide every run.

        A run whose topology we can't safely rebuild is left exactly as it was
        and mentioned in `notes` — every bail-out happens before the first edit,
        so a partial failure never leaves half-rebuilt geometry behind.
        """
        if self.use_subdivide:
            total = 0
            for entry in entries:
                merged, added = self._subdivide_run(bm, entry)
                entry["verts"] = merged
                total += added
            if total:
                actions.append(f"subdivided {len(entries)} run(s), "
                               f"+{total} vert(s)")
            return

        if self.vertex_count <= 0:
            return

        target = max(self.vertex_count, 3)
        loops = [e for e in entries if e["closed"]]
        arcs = [e for e in entries if not e["closed"]]

        if loops:
            if any(len(e["verts"]) != target for e in loops):
                rings = self._rebuild_loops(bm, [e["verts"] for e in loops])
                if rings is None:
                    notes.append(f"{len(loops)} loop(s) kept their count "
                                 f"(degenerate — no plane to fit)")
                    loops = []
                else:
                    for entry, ring in zip(loops, rings):
                        entry["verts"] = ring
            else:
                for entry in loops:
                    self._round_run(entry)
            if loops:
                actions.append(f"{len(loops)} loop(s) → {target} verts")

        done = 0
        for entry in arcs:
            if self._resample_arc(bm, entry, target):
                done += 1
        if done:
            actions.append(f"{done} arc(s) → {target} verts"
                           + (" (whole circle)" if self.complete else ""))
        if done < len(arcs):
            notes.append(f"{len(arcs) - done} arc(s) kept their count "
                         f"(faces span them in a way that can't be rebuilt "
                         f"safely — try Subdivide)")

    def _resample_arc(self, bm, entry, target):
        """Resample one arc; with Complete on, `target` counts the full circle."""
        run = entry["verts"]

        if self.complete:
            if is_free_run(run, False):
                # Nothing else references these verts, so we can lay down the
                # ideal N-gon rather than a pinned approximation of one.
                ring = self._rebuild_free_circle(bm, entry, target)
                if ring is None:
                    return False
                entry["verts"], entry["closed"] = ring, True
                return True
            arc_target, gap = self._plan_complete(entry, target)
            entry["gap_count"] = gap
        else:
            arc_target = target

        if arc_target == len(run):
            self._round_run(entry)
            return True
        if is_wire_run(run, False):
            new_run = self._rebuild_wire_arc(bm, entry, arc_target)
        else:
            new_run = self._rebuild_faced_arc(bm, entry, arc_target)
        if new_run is None:
            return False
        entry["verts"] = new_run
        return True

    def _plan_complete(self, entry, count):
        """(vertices for the arc, vertices for the gap) of a `count`-vert circle."""
        sweep, _ = sweep_of(entry["fit"], entry["verts"])
        if abs(sweep) < EPS:
            return len(entry["verts"]), 0
        step = math.copysign(2.0 * math.pi / count, sweep)
        share = int(round(sweep / step)) + 1
        share = max(2, min(share, count))
        return share, count - share

    # --------------------------------------------------------- stage: round

    def _run_round(self, entries, actions):
        if not self.round_to_circle:
            return
        for entry in entries:
            self._round_run(entry)
        actions.append(f"rounded {len(entries)} run(s)")

    def _round_run(self, entry):
        """Project a run's vertices onto its circle, keeping their angles.

        Closed loops additionally get evenly redistributed (that is the classic
        re-circle behaviour); an arc keeps each vertex where it is angularly, so
        its ends don't drift away from whatever they connect to.
        """
        fit, run = entry["fit"], [v for v in entry["verts"] if v.is_valid]
        if len(run) < 3:
            return
        if entry["closed"]:
            centroid, normal, center = self._center_for(run)
            if normal is None:
                return
            positions, _ = resample_ring(
                centroid, normal, [v.co.copy() for v in run], len(run),
                radius=self.radius, offset=self.offset, center=center,
            )
        else:
            positions = [point_at_angle(fit["center"], fit["u"], fit["v"],
                                        fit["radius"], a)
                         for a in point_angles(fit["center"], fit["u"],
                                               fit["v"], [v.co for v in run])]
        for v, p in zip(run, positions):
            v.co = p

    def _center_for(self, verts):
        """(centroid, normal, center) — centre may be the least-squares one."""
        pts = [v.co for v in verts]
        centroid, normal = fit_plane(pts)
        if normal is None or normal.length < EPS:
            return None, None, None
        center = centroid
        if self.use_fit_center:
            fit = circle_of(verts)
            if fit is not None:
                center, normal = fit["center"], fit["normal"]
        return centroid, normal, center

    # ------------------------------------------------------ stage: complete

    def _run_complete(self, bm, entries, actions):
        if not self.complete:
            return
        made = 0
        closed = 0
        for entry in entries:
            if entry["closed"]:
                continue
            new = self._complete_run(bm, entry)
            if new is None:
                continue
            entry["verts"] = entry["verts"] + new
            entry["closed"] = True
            made += len(new)
            closed += 1
        if closed:
            actions.append(f"closed {closed} arc(s), +{made} vert(s)")

    def _complete_run(self, bm, entry):
        """Wire the missing sweep of an arc, returning the new verts."""
        run, fit = [v for v in entry["verts"] if v.is_valid], entry["fit"]
        if len(run) < 3:
            return None
        _, angles = sweep_of(fit, run)
        gap = arc_gap_angles(angles, entry.get("gap_count", 0))
        new = [bm.verts.new(point_at_angle(fit["center"], fit["u"], fit["v"],
                                           fit["radius"], a))
               for a in gap]
        chain = [run[-1]] + new + [run[0]]
        for a, b in zip(chain, chain[1:]):
            if a is not b and edge_between(a, b) is None:
                bm.edges.new((a, b))
        return new

    # -------------------------------------------------------- stage: extras

    def _run_extras(self, context, obj, bm, entries, actions):
        made = []
        if self.add_center_vertex:
            for entry in entries:
                made.append(self._add_center_vertex(bm, entry))
            actions.append(f"{len(entries)} centre vertex/vertices")

        if self.add_support_circle:
            names = []
            for entry in entries:
                ob = self._add_support_circle(context, obj, entry)
                if ob is not None:
                    names.append(ob.name)
            if names:
                actions.append("added " + ", ".join(names))

        if self.snap_cursor:
            self._snap_cursor(context, obj, entries[0])
            actions.append("cursor to centre")
        return [v for v in made if v is not None]

    def _add_center_vertex(self, bm, entry):
        vert = bm.verts.new(entry["fit"]["center"])
        if self.connect_center and not entry["closed"]:
            run = [v for v in entry["verts"] if v.is_valid]
            for end in (run[0], run[-1]):
                if edge_between(vert, end) is None:
                    bm.edges.new((vert, end))
        return vert

    def _support_segments(self, entry):
        """Vertex count for the support circle: explicit, or the run's spacing."""
        if self.support_segments > 0:
            return max(int(self.support_segments), 3)
        run = [v for v in entry["verts"] if v.is_valid]
        if entry["closed"]:
            return max(len(run), 3)
        sweep, _ = sweep_of(entry["fit"], run)
        if abs(sweep) < EPS or len(run) < 2:
            return 32
        step = abs(sweep) / (len(run) - 1)
        return max(int(round(2.0 * math.pi / step)), 3)

    def _add_support_circle(self, context, obj, entry):
        fit = entry["fit"]
        segments = self._support_segments(entry)

        mesh = bpy.data.meshes.new(self.support_name)
        tmp = bmesh.new()
        bmesh.ops.create_circle(
            tmp, cap_ends=(self.support_fill != 'NONE'),
            cap_tris=(self.support_fill == 'TRIS'),
            segments=segments, radius=max(fit["radius"], EPS),
        )
        tmp.to_mesh(mesh)
        tmp.free()

        ob = bpy.data.objects.new(self.support_name, mesh)
        if self.support_fill == 'NONE':
            ob.display_type = 'WIRE'
            ob.show_in_front = True

        # The circle is built flat in local XY, so the object's own transform
        # carries the plane and the phase: X -> u, Y -> v, Z -> the normal.
        basis = Matrix((fit["u"], fit["v"], fit["normal"])).transposed().to_4x4()
        ob.matrix_world = obj.matrix_world @ (Matrix.Translation(fit["center"])
                                              @ basis)

        for coll in (obj.users_collection or (context.scene.collection,)):
            coll.objects.link(ob)
        try:
            ob.select_set(True)
        except RuntimeError:
            pass                    # selecting while in edit mode is optional
        return ob

    def _snap_cursor(self, context, obj, entry):
        fit = entry["fit"]
        cursor = context.scene.cursor
        cursor.location = obj.matrix_world @ fit["center"]
        if not self.cursor_align:
            return
        basis = Matrix((fit["u"], fit["v"], fit["normal"]))
        quat = (obj.matrix_world.to_3x3() @ basis.transposed()).to_quaternion()
        if cursor.rotation_mode == 'QUATERNION':
            cursor.rotation_quaternion = quat
        elif cursor.rotation_mode == 'AXIS_ANGLE':
            axis, angle = quat.to_axis_angle()
            cursor.rotation_axis_angle = (angle, *axis)
        else:
            cursor.rotation_euler = quat.to_euler(cursor.rotation_mode)

    # ------------------------------------------------------------ subdivide

    def _subdivide_run(self, bm, entry):
        """Cut every edge of a run and put the new verts on the circle."""
        fit = entry["fit"]
        run = [v for v in entry["verts"] if v.is_valid]
        c, u, v = fit["center"], fit["u"], fit["v"]

        pairs = list(zip(run, run[1:]))
        if entry["closed"]:
            pairs.append((run[-1], run[0]))

        merged, added = [], 0
        for a, b in pairs:
            merged.append(a)
            e = edge_between(a, b)
            if e is None:
                continue
            inner = _split_edge(e, a, self.cuts)
            if not inner:
                continue
            a_ang = point_angles(c, u, v, [a.co])[0]
            b_ang = point_angles(c, u, v, [b.co])[0]
            # Shortest wrap: neighbouring verts are always close in angle, so
            # this picks the short way round even across the +/-pi seam.
            delta = (b_ang - a_ang + math.pi) % (2.0 * math.pi) - math.pi
            steps = len(inner) + 1
            for k, vert in enumerate(inner):
                vert.co = point_at_angle(c, u, v, fit["radius"],
                                         a_ang + delta * (k + 1) / steps)
            merged += inner
            added += len(inner)
        if not entry["closed"]:
            merged.append(run[-1])
        return merged, added

    # ------------------------------------------------------- rebuild: loops

    def _rebuild_loops(self, bm, loops):
        """Rebuild closed loops at the target count, re-bridging their strips.

        Returns one new ring per input loop, or None if a loop was degenerate.
        """
        target = max(self.vertex_count, 3)
        loop_vset = set(v for loop in loops for v in loop)

        # 1. Gather everything we need *before* mutating the mesh.
        infos = []
        strip_faces = set()
        for loop in loops:
            centroid, normal, center = self._center_for(loop)
            if normal is None:
                return None
            positions, _ = resample_ring(
                centroid, normal, [v.co.copy() for v in loop], target,
                radius=self.radius, offset=self.offset, center=center,
            )
            loopset = set(loop)
            faces = set()
            for e in cycle_edges(loop):
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
                          "nbrs": nbrs, "has_cap": has_cap, "new": None})

        # Adjacency between selected loops (they share a strip face).
        loop_index = {v: i for i, loop in enumerate(loops) for v in loop}
        adjacent_pairs = set()
        for f in strip_faces:
            idxs = {loop_index[v] for v in f.verts if v in loop_index}
            for i in idxs:
                for j in idxs:
                    if i < j:
                        adjacent_pairs.add((i, j))

        # 2. Build the new ring verts (positions above are plain Vectors).
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
                new_faces += self._bridge_rings(bm, owner["new"], bloop)

        # 4b. Bridge between adjacent selected loops (same count -> clean quads).
        for i, j in adjacent_pairs:
            new_faces += self._bridge_rings(bm, infos[i]["new"], infos[j]["new"])

        # 4c. Rebuild single-face caps on the new rings.
        if self.fill_caps:
            for info in infos:
                if not info["has_cap"]:
                    continue
                try:
                    new_faces.append(bm.faces.new(info["new"]))
                except ValueError:
                    pass  # cap already closed by bridging on that side

        self._finish_faces(bm, new_faces)
        return [info["new"] for info in infos]

    def _bridge_rings(self, bm, ring_new, ring_other):
        """Loft `ring_new` to the closed ring `ring_other`, return new faces."""
        ring_other = [v for v in ring_other if v.is_valid]
        if len(ring_new) < 2 or len(ring_other) < 2:
            return []
        pos_new = [v.co for v in ring_new]
        order = align_ring(pos_new, [v.co for v in ring_other])
        rb = [ring_other[k] for k in order]
        return self._make_faces(bm, ring_new, rb,
                                bridge_face_indices(pos_new,
                                                    [v.co for v in rb]))

    # --------------------------------------------------------- rebuild: arcs

    def _rebuild_free_circle(self, bm, entry, count):
        """Replace a free-standing wire arc with the full `count`-vert circle."""
        run, fit = entry["verts"], entry["fit"]
        sweep, angles = sweep_of(fit, run)
        winding = 1.0 if sweep >= 0.0 else -1.0
        positions = circle_positions(fit["center"], fit["normal"], count,
                                     start_angle=angles[0] + winding * self.offset,
                                     radius=fit["radius"], winding=winding)
        bmesh.ops.delete(bm, geom=[v for v in run if v.is_valid], context='VERTS')
        ring = [bm.verts.new(p) for p in positions]
        for i in range(count):
            bm.edges.new((ring[i], ring[(i + 1) % count]))
        return ring

    def _rebuild_wire_arc(self, bm, entry, target):
        """Re-space a wire arc at a new vertex count, endpoints pinned."""
        run, fit = entry["verts"], entry["fit"]
        positions, _ = resample_arc(fit["center"], fit["normal"],
                                    [v.co.copy() for v in run], target,
                                    radius=fit["radius"])
        if len(positions) < 2:
            return None
        first, last = run[0], run[-1]
        interior = [v for v in run[1:-1] if v.is_valid]
        if interior:
            bmesh.ops.delete(bm, geom=interior, context='VERTS')
        new_run = [first] + [bm.verts.new(p) for p in positions[1:-1]] + [last]
        for a, b in zip(new_run, new_run[1:]):
            if edge_between(a, b) is None:
                bm.edges.new((a, b))
        return new_run

    def _rebuild_faced_arc(self, bm, entry, target):
        """Change a faced arc's vertex count, keeping the surface closed.

        Faces touching the arc fall into two kinds:

          * strip faces (two arc verts — the quads of a face ring) are deleted
            and re-lofted against the neighbouring run, which is an *open*
            chain here, not a ring; and
          * spanning faces (three or more arc verts — an n-gon cap, say) are
            rebuilt with the arc's stretch of their vertex cycle swapped for the
            new one, so a cap survives a density change instead of vanishing.

        A vertex fanning to three or more arc verts is treated as a fan centre
        and re-fanned. Returns None when the topology doesn't fit those cases,
        leaving the mesh untouched for the caller to report.
        """
        run, fit = entry["verts"], entry["fit"]
        positions, _ = resample_arc(fit["center"], fit["normal"],
                                    [v.co.copy() for v in run], target,
                                    radius=fit["radius"])
        if len(positions) < 2:
            return None

        runset = set(run)
        patch, seen = [], set()
        for e in chain_edges(run):
            for f in e.link_faces:
                if f in seen:
                    continue
                seen.add(f)
                if sum(1 for v in f.verts if v in runset) > 2:
                    spec = _face_run_split(f.verts[:], runset)
                    if spec is None:
                        return None
                    part, rest = spec
                    if part[0] is run[0]:
                        forward = True
                    elif part[0] is run[-1]:
                        forward = False
                    else:
                        return None       # only part of the arc — don't guess
                    patch.append((forward, rest))

        # Neighbours, and how many arc verts each of them touches.
        hits = defaultdict(int)
        for v in run:
            for e in v.link_edges:
                ov = e.other_vert(v)
                if ov not in runset:
                    hits[ov] += 1
        chain_nbrs = [v for v, n in hits.items() if n <= 2]
        fan_nbrs = [v for v, n in hits.items() if n >= 3]

        first, last = run[0], run[-1]
        # Everything touching the arc goes; strips come back as a fresh loft,
        # spanning faces come back from their recorded cycles.
        bmesh.ops.delete(bm, geom=[f for f in seen if f.is_valid],
                         context='FACES_ONLY')
        interior = [v for v in run[1:-1] if v.is_valid]
        if interior:
            bmesh.ops.delete(bm, geom=interior, context='VERTS')

        new_run = [first] + [bm.verts.new(p) for p in positions[1:-1]] + [last]
        for a, b in zip(new_run, new_run[1:]):
            if edge_between(a, b) is None:
                bm.edges.new((a, b))

        new_faces = []
        for bchain in boundary_chains([v for v in chain_nbrs if v.is_valid]):
            new_faces += self._bridge_chains(bm, new_run, bchain)

        for centre in fan_nbrs:
            if not centre.is_valid:
                continue
            for a, b in zip(new_run, new_run[1:]):
                try:
                    new_faces.append(bm.faces.new((a, b, centre)))
                except ValueError:
                    pass

        for forward, rest in patch:
            rest = [v for v in rest if v.is_valid]
            part = new_run if forward else list(reversed(new_run))
            try:
                new_faces.append(bm.faces.new(part + rest))
            except ValueError:
                pass

        self._finish_faces(bm, new_faces)
        return new_run

    def _bridge_chains(self, bm, run_a, chain_b):
        """Loft the open run `run_a` against the open chain `chain_b`."""
        chain_b = [v for v in chain_b if v.is_valid]
        if len(run_a) < 2 or len(chain_b) < 2:
            return []
        # Match the ends up before lofting, or the strip comes out crossed.
        if ((run_a[0].co - chain_b[0].co).length_squared >
                (run_a[0].co - chain_b[-1].co).length_squared):
            chain_b = list(reversed(chain_b))
        specs = bridge_chain_face_indices([v.co for v in run_a],
                                          [v.co for v in chain_b])
        return self._make_faces(bm, run_a, chain_b, specs)

    # ------------------------------------------------------- face plumbing

    def _make_faces(self, bm, ring_a, ring_b, specs):
        faces = []
        for spec in specs:
            verts = [ring_a[i] if tag == 'a' else ring_b[i] for tag, i in spec]
            if len(set(verts)) != len(verts):
                continue
            try:
                faces.append(bm.faces.new(verts))
            except ValueError:
                pass  # face already exists
        return faces

    def _finish_faces(self, bm, new_faces):
        """Point the freshly built faces the same way as their neighbours."""
        unseeded = orient_new_faces(new_faces)
        if unseeded:
            comp = [f for f in new_faces if f.is_valid]
            if comp:
                bmesh.ops.recalc_face_normals(bm, faces=comp)


classes = (MESH_OT_recircle,)
