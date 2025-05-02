bl_info = {
    "name": "Tims Oriented BBox",
    "author": "Tim Richter",
    "version": (1, 0, 0),
    "blender": (2, 80, 0),
    "category": "TimsTools",

}

from . import main

def register():
    main.register()

def unregister():
    main.unregister()
