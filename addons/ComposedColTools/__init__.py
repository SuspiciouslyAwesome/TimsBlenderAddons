bl_info = {
    "name": "Tim Composed COL Tools",
    "blender": (3, 4, 0),
    "category": "TimsTools",
    "description": "Composed COL Tools",
    "author": "Tim Richter",
    "version": (2, 0),
}

from . import main

def register():
    main.register()

def unregister():
    main.unregister()
