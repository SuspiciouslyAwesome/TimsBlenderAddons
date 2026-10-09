"""Mesh Budget — estimate the in-engine size of all visible meshes.

Blender's statistics show welded vertices; a game engine stores one vertex per
unique combination of position, normal, UV0, UV1 and material. This add-on
counts those splits, converts to bytes with a configurable vertex/index layout
and compares the total against a memory budget.

This is a Blender extension (4.2+): metadata lives in blender_manifest.toml.
"""

if "bpy" in locals():
    import importlib
    if "main" in locals():
        importlib.reload(main)

import bpy

from . import main


def register():
    for cls in main.classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mesh_budget = bpy.props.PointerProperty(type=main.MeshBudgetSettings)


def unregister():
    del bpy.types.Scene.mesh_budget
    for cls in reversed(main.classes):
        bpy.utils.unregister_class(cls)
