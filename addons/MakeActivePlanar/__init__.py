"""Make Active Planar — flatten the active face by moving only the active
vertex, or the two vertices of the active edge, sliding them along their
connected (spoke) edges.

This is a Blender extension (4.2+): metadata lives in blender_manifest.toml,
so no bl_info dict is required here.
"""

import bpy

from .operators import MESH_OT_make_active_planar


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
    self.layout.operator(MESH_OT_make_active_planar.bl_idname)


def register():
    global _owns_tim_menu
    bpy.utils.register_class(MESH_OT_make_active_planar)

    # Add "Tim" to the row of header menus (View / Select / Add / Mesh / ...),
    # but only if another of Tim's addons hasn't already created it.
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

    bpy.utils.unregister_class(MESH_OT_make_active_planar)
