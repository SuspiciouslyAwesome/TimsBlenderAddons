bl_info = {
    "name": "Tims Better Exporter",
    "author": "Tim Richter",
    "version": (6, 0, 0),
    "blender": (2, 80, 0),
    "location": "View3D > Sidebar > TimsTools",
    "description": "Export FBX files with proper transforms",
    "warning": "",
    "doc_url": "",
    "category": "TimsTools",
}

import bpy
from . import exporter

def register():
    exporter.register()

def unregister():
    exporter.unregister()

if __name__ == "__main__":
    register()