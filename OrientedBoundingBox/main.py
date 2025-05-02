import bpy
import bmesh
from mathutils import Matrix, Vector
import numpy as np

class OBJECT_OT_create_obb(bpy.types.Operator):
    bl_idname = "object.create_obb"
    bl_label = "Create Oriented Bounding Box"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "No mesh object selected")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        selected_verts = [v.co for v in bm.verts if v.select]

        if not selected_verts:
            self.report({'ERROR'}, "No vertices selected")
            return {'CANCELLED'}

        points = np.asarray([v.to_tuple() for v in selected_verts])
        means = np.mean(points, axis=0)

        cov = np.cov(points, rowvar=False, bias=True)
        eigvals, eigvecs = np.linalg.eig(cov)

        points_r = np.dot(points - means, eigvecs)

        co_min = np.min(points_r, axis=0)
        co_max = np.max(points_r, axis=0)

        xdif = (co_max[0] - co_min[0]) * 0.5
        ydif = (co_max[1] - co_min[1]) * 0.5
        zdif = (co_max[2] - co_min[2]) * 0.5

        cx = co_min[0] + xdif
        cy = co_min[1] + ydif
        cz = co_min[2] + zdif

        corners = np.array([
            [cx - xdif, cy - ydif, cz - zdif],
            [cx - xdif, cy + ydif, cz - zdif],
            [cx - xdif, cy + ydif, cz + zdif],
            [cx - xdif, cy - ydif, cz + zdif],
            [cx + xdif, cy + ydif, cz + zdif],
            [cx + xdif, cy + ydif, cz - zdif],
            [cx + xdif, cy - ydif, cz + zdif],
            [cx + xdif, cy - ydif, cz - zdif],
        ])

        corners = np.dot(corners, eigvecs.T) + means
        corners = [Vector(corner) for corner in corners]

        mesh = bpy.data.meshes.new("OBB")
        mesh.from_pydata(corners, [], [
            (0, 1, 2, 3), (4, 5, 6, 7),
            (0, 1, 5, 4), (2, 3, 7, 6),
            (0, 3, 7, 4), (1, 2, 6, 5)
        ])
        obb_obj = bpy.data.objects.new("OBB", mesh)
        context.collection.objects.link(obb_obj)

        return {'FINISHED'}

def menu_func(self, context):
    self.layout.operator(OBJECT_OT_create_obb.bl_idname)

def register():
    bpy.utils.register_class(OBJECT_OT_create_obb)
    bpy.types.VIEW3D_MT_mesh_add.append(menu_func)

def unregister():
    bpy.utils.unregister_class(OBJECT_OT_create_obb)
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_func)
