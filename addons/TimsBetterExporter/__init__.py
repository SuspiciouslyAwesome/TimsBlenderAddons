import bpy
from . import exporter

def register():
    exporter.register()

def unregister():
    exporter.unregister()

if __name__ == "__main__":
    register()