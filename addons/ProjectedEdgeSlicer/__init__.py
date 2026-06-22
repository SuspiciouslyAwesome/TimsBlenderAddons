"""Projected Edge Slicer.

Make an edge active, aim the viewport, and run this. The active edge is swept
along the current view direction to form a cutting plane, and the whole mesh is
sliced by it: every edge that visually crosses the active edge's line gets a new
vertex at the crossing, and the faces behind are split too.

Because the cut is a plane (the active edge swept along the view), this works
for both orthographic and perspective views, and stays entirely within the
edited object in Edit Mode.

This is a Blender extension (4.2+): metadata lives in blender_manifest.toml,
so no bl_info dict is required here.
"""

import bpy
import bmesh
from mathutils import Vector


class MESH_OT_projected_edge_slicer(bpy.types.Operator):
    """Slice the whole mesh with the active edge projected from the current view"""
    bl_idname = "mesh.projected_edge_slicer"
    bl_label = "Projected Edge Slicer"
    bl_options = {'REGISTER', 'UNDO'}

    threshold: bpy.props.FloatProperty(
        name="Threshold",
        description="Preserves geometry along the cut that is within this distance "
                    "of the plane (keeps the active edge itself intact)",
        default=0.0001,
        min=0.0,
        precision=6,
    )

    @classmethod
    def poll(cls, context):
        obj = context.edit_object
        return (context.mode == 'EDIT_MESH'
                and obj is not None
                and obj.type == 'MESH')

    # ------------------------------------------------------------------ helpers

    def _find_rv3d(self, context):
        """Return the RegionView3D of a 3D viewport, preferring the active one."""
        area = context.area
        if area is not None and area.type == 'VIEW_3D':
            return area.spaces.active.region_3d

        for window in context.window_manager.windows:
            for a in window.screen.areas:
                if a.type == 'VIEW_3D':
                    return a.spaces.active.region_3d
        return None

    def _plane_from_active_edge(self, obj, edge, rv3d):
        """Build the cut plane (local space) from the active edge + view direction.

        The plane contains the edge and runs along the line of sight, so it
        projects onto the active edge's line in the viewport.
        Returns (plane_co, plane_no) in the object's local space, or None.
        """
        a = edge.verts[0].co
        b = edge.verts[1].co
        edge_vec = b - a

        mwi = obj.matrix_world.inverted()

        if rv3d.is_perspective:
            # Plane through the eye and the edge.
            eye_local = mwi @ rv3d.view_matrix.inverted().translation
            plane_no = edge_vec.cross(eye_local - a)
        else:
            # Plane through the edge, swept along the (parallel) view direction.
            view_dir_world = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
            view_dir_local = mwi.to_3x3() @ view_dir_world
            plane_no = edge_vec.cross(view_dir_local)

        if plane_no.length < 1e-9:
            return None  # edge points straight along the view: no usable plane
        plane_no.normalize()
        return a.copy(), plane_no

    # ------------------------------------------------------------------ execute

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)

        active = bm.select_history.active
        if not isinstance(active, bmesh.types.BMEdge):
            self.report({'ERROR'},
                        "No active edge. Select an edge and make it active "
                        "(click it last).")
            return {'CANCELLED'}

        rv3d = self._find_rv3d(context)
        if rv3d is None:
            self.report({'ERROR'}, "No 3D Viewport available to project from.")
            return {'CANCELLED'}

        plane = self._plane_from_active_edge(obj, active, rv3d)
        if plane is None:
            self.report({'ERROR'},
                        "The active edge points straight along the view; "
                        "rotate the view so the edge is visible as a line.")
            return {'CANCELLED'}
        plane_co, plane_no = plane

        # Cut the WHOLE mesh, regardless of selection. The active edge lies on the
        # plane, so it survives; every edge crossing it in the view gets a new vert.
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(
            bm,
            geom=geom,
            dist=self.threshold,
            plane_co=plane_co,
            plane_no=plane_no,
            clear_inner=False,
            clear_outer=False,
        )

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
    self.layout.operator(MESH_OT_projected_edge_slicer.bl_idname)


def register():
    global _owns_tim_menu
    bpy.utils.register_class(MESH_OT_projected_edge_slicer)

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

    bpy.utils.unregister_class(MESH_OT_projected_edge_slicer)
