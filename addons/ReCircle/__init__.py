"""Re-circle — turn selected edge loops, and now partial arcs, back into clean
circles.

The main operator resamples a closed loop into a flat, regular circle at a new
vertex count, re-bridging the surrounding faces so the mesh stays closed. Handy
for dialling the resolution of round geometry up or down after the fact. Works
on several selected loops at once (resampled to the same count).

Everything also works on an *incomplete* circle: select any three or more
vertices of an arc and the add-on fits the circle they came from (least-squares,
so the missing part doesn't skew the centre), then lets you regularise it,
complete it, subdivide it, mark its centre, build a matching support object, or
just park the 3D cursor there.

It is all one operator, `mesh.recircle`, driven from its redo panel (F9) — and
on the panel's defaults it does nothing at all, so you can run it, read what it
found, and then decide.

This is a Blender extension (4.2+): metadata lives in blender_manifest.toml,
so no bl_info dict is required here.
"""

# Reload guard — must come before the submodule imports. On "Reload Scripts"
# (or a disable/enable) Blender re-runs this file while the old submodules are
# still in sys.modules; without this they'd be handed back unchanged, and any
# name that only exists in the new version raises ImportError. Editing the
# add-on with Blender open is the normal case here, so it's worth the six lines.
if "bpy" in locals():
    import importlib
    for _name in ("geometry", "topology", "operators"):
        if _name in locals():
            importlib.reload(locals()[_name])

import bpy

from . import geometry, topology, operators   # noqa: F401  (geometry/topology
                                              # are imported so the reload guard
                                              # above can see them)


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
    # One entry, one operator: everything else lives in its redo panel (F9).
    self.layout.operator(operators.MESH_OT_recircle.bl_idname, text="Re-circle")


def register():
    global _owns_tim_menu
    # Looked up through the module (not imported by name) so a stale reload can
    # never half-import the operator classes.
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
