bl_info = {
    "name": "Tim Composed COL Tools",
    "author": "Tim Richter",
    "version": (1, 0, 0),
    "blender": (3, 4, 0),
    "category": "TimsTools",
    "description": "Composed COL Tools",
}

from . import main

def register():
    main.register()

def unregister():
    main.unregister()
