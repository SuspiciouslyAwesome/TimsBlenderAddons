"""Circle Fillet — round a corner vertex with a true circular arc.

Select a vertex, run Circle Fillet, and the two edges meeting there are
replaced by an arc tangent to both. Set the Radius and the Num Points of the
full circle (the arc gets an evenly spaced share of them); optionally add the
arc without cutting anything, or keep the original corner vertex around.

The operator is `mesh.circle_fillet`; its settings live in the redo panel (F9).

This is a Blender extension (4.2+): metadata lives in blender_manifest.toml,
so no bl_info dict is required here.
"""

# Reload guard — must precede the submodule imports. Disable/enable keeps the
# submodules cached in sys.modules, so without this an edited operators.py would
# not be picked up until Blender restarts. (ReCircle's `"bpy" in locals()` check
# only catches Reload Scripts, not a plain disable/enable.)
import importlib
import sys

for _name in ("geometry", "operators"):          # geometry first: operators uses it
    _mod = sys.modules.get(f"{__name__}.{_name}")
    if _mod is not None:
        importlib.reload(_mod)

import bpy

from . import geometry, operators   # noqa: F401  (geometry is imported so the
                                    # reload guard above can see it)


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
    self.layout.operator(operators.MESH_OT_circle_fillet.bl_idname,
                         text="Circle Fillet")


def register():
    global _owns_tim_menu
    for cls in operators.classes:
        bpy.utils.register_class(cls)

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

    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
