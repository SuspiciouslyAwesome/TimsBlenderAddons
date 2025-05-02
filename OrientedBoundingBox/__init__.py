bl_info = {
    "name": "Tims Oriented BBox",
    "blender": (2, 80, 0),
    "category": "TimsTools",
}

from . import main

def register():
    main.register()

def unregister():
    main.unregister()
