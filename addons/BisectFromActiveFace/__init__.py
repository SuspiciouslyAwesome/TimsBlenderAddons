import bpy
import bmesh


class MESH_OT_bisect_from_active_face(bpy.types.Operator):
    """Bisect selected geometry using the active face as the cut plane"""
    bl_idname = "mesh.bisect_from_active_face"
    bl_label = "Bisect From Active Face"
    bl_options = {'REGISTER', 'UNDO'}

    # Standard bisect options, exposed so the F6 / redo panel can tweak them.
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
        description="Flip the active face normal (swaps which side is inner/outer)",
        default=False,
    )
    threshold: bpy.props.FloatProperty(
        name="Axis Threshold",
        description="Preserves geometry along the cut that is within this distance of the plane",
        default=0.0001,
        min=0.0,
        precision=6,
    )

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)

        face = bm.faces.active
        if face is None:
            self.report({'ERROR'},
                        "No active face. Select a face and make it active (Shift+Click it).")
            return {'CANCELLED'}
        if not face.select:
            self.report({'ERROR'}, "The active face is not selected.")
            return {'CANCELLED'}

        # Make sure the cached normal reflects the current geometry.
        face.normal_update()

        mw = obj.matrix_world

        # Plane position: the active face's median, in world space.
        plane_co = mw @ face.calc_center_median()

        # Plane normal: transform the local normal to world space. The inverse-transpose
        # keeps the direction correct under non-uniform scale / shear.
        normal_matrix = mw.to_3x3().inverted_safe().transposed()
        plane_no = (normal_matrix @ face.normal).normalized()
        if self.flip:
            plane_no = -plane_no

        # bpy.ops.mesh.bisect takes the plane in WORLD space and converts it into each
        # edit-object's local space internally, then cuts all selected geometry. The active
        # face lies on the plane (coplanar) so it stays intact; everything else crossing the
        # plane gets bisected.
        return self._run_bisect(context, plane_co, plane_no)

    def _run_bisect(self, context, plane_co, plane_no):
        kwargs = dict(
            plane_co=plane_co,
            plane_no=plane_no,
            use_fill=self.use_fill,
            clear_inner=self.clear_inner,
            clear_outer=self.clear_outer,
            threshold=self.threshold,
        )

        # bisect needs a VIEW_3D context. Usually we already have one (called from a menu),
        # but override defensively so the operator also works from the Python console.
        if context.area and context.area.type == 'VIEW_3D':
            return bpy.ops.mesh.bisect(**kwargs)

        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                    with context.temp_override(window=window, area=area, region=region):
                        return bpy.ops.mesh.bisect(**kwargs)

        self.report({'ERROR'}, "No 3D Viewport available to run bisect.")
        return {'CANCELLED'}


def _menu_draw(self, context):
    self.layout.operator(
        MESH_OT_bisect_from_active_face.bl_idname,
        text="Bisect From Active Face",
    )


def register():
    bpy.utils.register_class(MESH_OT_bisect_from_active_face)
    # Mesh menu in the 3D viewport header.
    bpy.types.VIEW3D_MT_edit_mesh.append(_menu_draw)
    # Right-click context menu while in face mode.
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(_menu_draw)


def unregister():
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(_menu_draw)
    bpy.types.VIEW3D_MT_edit_mesh.remove(_menu_draw)
    bpy.utils.unregister_class(MESH_OT_bisect_from_active_face)
