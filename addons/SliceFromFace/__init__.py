"""Slice From Face.

In Edit Mode, make a face active and run this. A cutting plane is built from
the active face (its median centre + normal) and used to bisect:

  * the OTHER selected faces, if any are selected besides the active face, or
  * the WHOLE mesh, if only the active face is selected.

If the active face is not planar, a warning is reported ("Face is not planar,
slice at your own risk") and the cut proceeds with the planarized face: the
plane through the face's median centre with the face's (Newell-averaged)
normal.

This is a Blender extension (4.2+): metadata lives in blender_manifest.toml,
so no bl_info dict is required here.
"""

import bpy
import bmesh


class MESH_OT_slice_from_face(bpy.types.Operator):
    """Bisect the selected faces (or the whole mesh) with the active face's plane"""
    bl_idname = "mesh.slice_from_face"
    bl_label = "Slice From Face"
    bl_options = {'REGISTER', 'UNDO'}

    use_fill: bpy.props.BoolProperty(
        name="Fill",
        description="Fill the cut with a new face",
        default=False,
    )
    clear_inner: bpy.props.BoolProperty(
        name="Clear Inner",
        description="Remove geometry on the negative side of the plane",
        default=False,
    )
    clear_outer: bpy.props.BoolProperty(
        name="Clear Outer",
        description="Remove geometry on the positive side of the plane",
        default=False,
    )
    flip: bpy.props.BoolProperty(
        name="Flip Plane",
        description="Flip the cut plane normal (swaps which side is inner/outer)",
        default=False,
    )
    threshold: bpy.props.FloatProperty(
        name="Threshold",
        description="Preserves geometry along the cut that is within this distance "
                    "of the plane (keeps the active face itself intact)",
        default=0.0001,
        min=0.0,
        precision=6,
    )
    planar_threshold: bpy.props.FloatProperty(
        name="Planar Threshold",
        description="Max vertex distance from the face's plane before the face "
                    "counts as non-planar",
        default=1e-4,
        min=0.0,
        soft_max=0.01,
        precision=6,
    )

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return (context.mode == 'EDIT_MESH'
                and obj is not None
                and obj.type == 'MESH')

    # ------------------------------------------------------------------ execute

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)

        face = bm.faces.active
        if face is None or not face.select:
            self.report({'ERROR'},
                        "No active face. Select a face and make it active "
                        "(click it last).")
            return {'CANCELLED'}

        # Cut plane from the active face: median centre + Newell-averaged normal.
        # For a non-planar face this IS the planarized face.
        face.normal_update()
        plane_no = face.normal.copy()
        if plane_no.length < 1e-9:
            self.report({'ERROR'}, "Active face is degenerate; no usable normal.")
            return {'CANCELLED'}
        plane_no.normalize()
        if self.flip:
            plane_no = -plane_no
        plane_co = face.calc_center_median()

        # Planarity check: any vert of the face too far off its own plane?
        deviation = max(abs((v.co - plane_co).dot(plane_no)) for v in face.verts)
        if deviation > self.planar_threshold:
            self.report({'WARNING'},
                        f"Face is not planar, slice at your own risk "
                        f"(max deviation {deviation:.6g}).")

        # Target: the other selected faces, or the whole mesh if the active face
        # is the only selected one.
        other_faces = [f for f in bm.faces if f.select and f is not face]
        if other_faces:
            verts = {v for f in other_faces for v in f.verts}
            edges = {e for f in other_faces for e in f.edges}
            geom = list(verts) + list(edges) + other_faces
        else:
            geom = bm.verts[:] + bm.edges[:] + bm.faces[:]

        bmesh.ops.bisect_plane(
            bm,
            geom=geom,
            dist=self.threshold,
            plane_co=plane_co,
            plane_no=plane_no,
            use_snap_center=False,
            clear_inner=self.clear_inner,
            clear_outer=self.clear_outer,
        )

        if self.use_fill:
            # bisect_plane has no fill option; fill the open cut edge loop(s)
            # like bpy.ops.mesh.bisect does, via edgenet_fill on the cut edges.
            cut_edges = [e for e in bm.edges
                         if e.is_valid and not e.is_manifold
                         and abs((e.verts[0].co - plane_co).dot(plane_no)) <= self.threshold
                         and abs((e.verts[1].co - plane_co).dot(plane_no)) <= self.threshold]
            if cut_edges:
                bmesh.ops.edgenet_fill(bm, edges=cut_edges)

        bmesh.update_edit_mesh(obj.data)
        return {'FINISHED'}


# ----------------------------------------------------------------- "Tim" menu
#
# The "Tim" header menu is shared across Tim's addons. Whichever addon loads
# first creates it; the others just append their entry. This block is duplicated
# (defensively) in each addon because extensions are isolated and can't import
# one another.

_TIM_MENU_ID = "VIEW3D_MT_tim"
_owns_tim_menu = False


class VIEW3D_MT_tim(bpy.types.Menu):
    """Tim's mesh tools."""
    bl_idname = "VIEW3D_MT_tim"
    bl_label = "Tim"

    def draw(self, context):
        # Entries are contributed by each addon via Menu.append().
        pass


def _draw_editor_menu(self, context):
    # Only show the "Tim" header menu while editing a mesh.
    if context.mode == 'EDIT_MESH':
        self.layout.menu(_TIM_MENU_ID)


def _menu_func(self, context):
    self.layout.operator(MESH_OT_slice_from_face.bl_idname)


def register():
    global _owns_tim_menu
    bpy.utils.register_class(MESH_OT_slice_from_face)

    # Create the shared "Tim" header menu only if no other addon already has.
    if not hasattr(bpy.types, _TIM_MENU_ID):
        bpy.utils.register_class(VIEW3D_MT_tim)
        bpy.types.VIEW3D_MT_editor_menus.append(_draw_editor_menu)
        _owns_tim_menu = True

    getattr(bpy.types, _TIM_MENU_ID).append(_menu_func)


def unregister():
    global _owns_tim_menu
    menu = getattr(bpy.types, _TIM_MENU_ID, None)
    if menu is not None:
        menu.remove(_menu_func)

    if _owns_tim_menu:
        bpy.types.VIEW3D_MT_editor_menus.remove(_draw_editor_menu)
        bpy.utils.unregister_class(VIEW3D_MT_tim)
        _owns_tim_menu = False

    bpy.utils.unregister_class(MESH_OT_slice_from_face)
