import bpy
import bmesh
import numpy as np
import os
from mathutils import Matrix, Vector

_addon_name = os.path.basename(os.path.dirname(os.path.realpath(__file__)))


def assign_col_material(context, obj):
    """Assign the COL material to the object, creating it if it doesn't exist."""
    mat_name = "COL"
    
    # Try to get preferences from addon
    try:
        prefs = context.preferences.addons[_addon_name].preferences
        mat_name = prefs.col_material_name
    except (KeyError, AttributeError, TypeError):
        pass
    
    # Get or create material
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        # Set a default color (semi-transparent green for collision visualization)
        if mat.node_tree:
            principled = mat.node_tree.nodes.get("Principled BSDF")
            if principled:
                principled.inputs["Base Color"].default_value = (0.0, 1.0, 0.0, 1.0)
                principled.inputs["Alpha"].default_value = 0.5
        mat.blend_method = 'BLEND'
    
    # Clear existing materials and assign
    obj.data.materials.clear()
    obj.data.materials.append(mat)


class CreateEmptyOperator(bpy.types.Operator):
    bl_idname = "object.create_empty"
    bl_label = "Create Root"

    def execute(self, context):
        selected_obj = context.active_object
        if selected_obj is None:
            self.report({'WARNING'}, "No active object selected")
            return {'CANCELLED'}

        bpy.ops.ed.undo_push(message="Create Empty")

        empty = bpy.data.objects.new("Empty", None)
        empty.location = selected_obj.location

        for collection in selected_obj.users_collection:
            collection.objects.link(empty)
            break

        empty.parent = selected_obj.parent
        selected_obj.parent = empty

        if empty.parent:
            empty.name = f"{empty.parent.name}_Composed_COL"
        else:
            empty.name = "Empty_ROOT"

        context.view_layer.update()

        context.view_layer.objects.active = empty
        empty.select_set(True)
        selected_obj.select_set(False)

        return {'FINISHED'}


class MakeBox(bpy.types.Operator):
    bl_idname = "object.first_button"
    bl_label = ""
    bl_description = "Assigns the first free 'Box_' name to the selected objects"
    icon = 'ADD'

    def execute(self, context):
        selected_objects = context.selected_objects
        if not selected_objects:
            self.report({'WARNING'}, "No objects selected.")
            return {'CANCELLED'}

        bpy.ops.ed.undo_push(message="Rename to Box")

        for obj in selected_objects:
            unused_name = self.find_unused_box_name()
            if unused_name:
                obj.name = unused_name
                self.report({'INFO'}, f"Renamed {obj.name} to: {unused_name}")
                
                # Assign COL material if toggle is enabled
                if context.scene.obb_assign_material:
                    assign_col_material(context, obj)
            else:
                self.report({'WARNING'}, "No unused box name found.")
                return {'CANCELLED'}
        return {'FINISHED'}

    def find_unused_box_name(self):
        existing_names = {obj.name for obj in bpy.data.objects}
        for i in range(1, 1000):
            box_name = f"Box_{i:03d}"
            if box_name not in existing_names:
                return box_name
        return None


class MakeHull(bpy.types.Operator):
    bl_idname = "object.second_button"
    bl_label = ""
    bl_description = "Assigns the first free 'Hull_' name to the selected objects"

    def execute(self, context):
        selected_objects = context.selected_objects
        if not selected_objects:
            self.report({'WARNING'}, "No objects selected.")
            return {'CANCELLED'}

        bpy.ops.ed.undo_push(message="Rename to Hull")

        for obj in selected_objects:
            unused_name = self.find_unused_hull_name()
            if unused_name:
                obj.name = unused_name
                self.report({'INFO'}, f"Renamed {obj.name} to: {unused_name}")
                
                # Assign COL material if toggle is enabled
                if context.scene.obb_assign_material:
                    assign_col_material(context, obj)
            else:
                self.report({'WARNING'}, "No unused hull name found.")
                return {'CANCELLED'}
        return {'FINISHED'}

    def find_unused_hull_name(self):
        existing_names = {obj.name for obj in bpy.data.objects}
        for i in range(1, 1000):
            hull_name = f"Hull_{i:03d}"
            if hull_name not in existing_names:
                return hull_name
        return None


class AlignToFace(bpy.types.Operator):
    bl_idname = "object.align_to_face"
    bl_label = ""
    bl_description = "Align object origin and rotation to selected face (geometry stays in place)"
    bl_options = {'REGISTER', 'UNDO'}

    use_face_center: bpy.props.BoolProperty(
        name="Move Origin to Face Center",
        description="Also move the object origin to the face center",
        default=True
    )

    def execute(self, context):
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            self.report({'WARNING'}, "Active object must be a mesh")
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH':
            self.report({'WARNING'}, "Must be in Edit Mode with a face selected")
            return {'CANCELLED'}

        # Get the bmesh from edit mode
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()

        # Find selected faces
        selected_faces = [f for f in bm.faces if f.select]

        if len(selected_faces) == 0:
            self.report({'WARNING'}, "No face selected")
            return {'CANCELLED'}

        if len(selected_faces) > 1:
            self.report({'WARNING'}, "Please select only one face")
            return {'CANCELLED'}

        face = selected_faces[0]

        # Get face normal and center in local space
        face_normal = face.normal.normalized()
        face_center = face.calc_center_median()

        # Build a coordinate system from the face
        # Z axis = face normal (pointing outward)
        # X axis = along one edge of the face
        # Y axis = perpendicular to both

        # Get the first edge direction for X axis
        if len(face.edges) > 0:
            edge = face.edges[0]
            edge_vec = (edge.verts[1].co - edge.verts[0].co).normalized()
        else:
            # Fallback if no edges (shouldn't happen)
            edge_vec = Vector((1, 0, 0))

        # Make sure edge_vec is perpendicular to normal
        # Project edge_vec onto the plane defined by normal
        z_axis = face_normal
        x_axis = (edge_vec - edge_vec.dot(z_axis) * z_axis).normalized()

        # If x_axis is zero (edge was parallel to normal), find another direction
        if x_axis.length < 0.001:
            # Use a different reference vector
            ref = Vector((1, 0, 0)) if abs(z_axis.dot(Vector((1, 0, 0)))) < 0.9 else Vector((0, 1, 0))
            x_axis = (ref - ref.dot(z_axis) * z_axis).normalized()

        y_axis = z_axis.cross(x_axis).normalized()

        # Build the new local-to-world rotation matrix from face orientation
        # This matrix transforms from the "face-aligned" space to local object space
        face_rotation_matrix = Matrix((
            (x_axis.x, y_axis.x, z_axis.x),
            (x_axis.y, y_axis.y, z_axis.y),
            (x_axis.z, y_axis.z, z_axis.z)
        )).to_4x4()

        # Current object matrix
        obj_matrix_world = obj.matrix_world.copy()

        # The new origin in world space
        if self.use_face_center:
            new_origin_local = face_center
        else:
            new_origin_local = Vector((0, 0, 0))

        new_origin_world = obj_matrix_world @ new_origin_local

        # The new rotation: combine current object rotation with face rotation
        # We want the face's coordinate system to become the object's local axes
        obj_rotation = obj_matrix_world.to_3x3().normalized().to_4x4()
        new_rotation = obj_rotation @ face_rotation_matrix

        # Build the new object matrix
        new_obj_matrix = Matrix.Translation(new_origin_world) @ new_rotation.to_4x4()

        # Calculate the inverse transform to apply to geometry
        # geometry_new = inverse(new_obj_matrix) @ obj_matrix_world @ geometry_old
        inverse_new = new_obj_matrix.inverted()
        geometry_transform = inverse_new @ obj_matrix_world

        # Switch to object mode to apply changes
        bpy.ops.object.mode_set(mode='OBJECT')

        # Transform all vertices
        mesh = obj.data
        for vert in mesh.vertices:
            vert.co = geometry_transform @ vert.co

        # Set the new object matrix
        obj.matrix_world = new_obj_matrix

        # Go back to edit mode
        bpy.ops.object.mode_set(mode='EDIT')

        self.report({'INFO'}, "Object aligned to face")
        return {'FINISHED'}


class CreateOBB(bpy.types.Operator):
    bl_idname = "object.create_obb"
    bl_label = ""
    bl_description = "Create an oriented bounding box for the selected geometry using PCA"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        replace_original = context.scene.obb_replace_original

        if obj is None or obj.type != 'MESH':
            self.report({'WARNING'}, "Active object must be a mesh")
            return {'CANCELLED'}

        # Get vertices in world space
        was_edit_mode = (context.mode == 'EDIT_MESH')

        if was_edit_mode:
            # Get selected vertices from edit mode
            bm = bmesh.from_edit_mesh(obj.data)
            selected_verts = [v for v in bm.verts if v.select]

            if len(selected_verts) < 3:
                self.report({'WARNING'}, "Need at least 3 selected vertices")
                return {'CANCELLED'}

            points_local = np.array([v.co[:] for v in selected_verts])
        else:
            # Use all vertices in object mode
            mesh = obj.data
            if len(mesh.vertices) < 3:
                self.report({'WARNING'}, "Mesh needs at least 3 vertices")
                return {'CANCELLED'}

            points_local = np.array([v.co[:] for v in mesh.vertices])

        # Transform points to world space
        matrix_world = np.array(obj.matrix_world)
        points_homogeneous = np.hstack([points_local, np.ones((len(points_local), 1))])
        points_world = (matrix_world @ points_homogeneous.T).T[:, :3]

        # Compute PCA to find principal axes
        centroid = np.mean(points_world, axis=0)
        centered = points_world - centroid

        # Covariance matrix and eigen decomposition
        cov = np.cov(centered.T)
        eigenvalues, eigenvectors = np.linalg.eigh(cov)

        # Sort by eigenvalue (largest first) - these become our axes
        order = eigenvalues.argsort()[::-1]
        eigenvectors = eigenvectors[:, order]

        # Ensure right-handed coordinate system
        if np.linalg.det(eigenvectors) < 0:
            eigenvectors[:, 2] = -eigenvectors[:, 2]

        # Build rotation matrix from eigenvectors
        rotation_matrix = Matrix((
            (eigenvectors[0, 0], eigenvectors[0, 1], eigenvectors[0, 2]),
            (eigenvectors[1, 0], eigenvectors[1, 1], eigenvectors[1, 2]),
            (eigenvectors[2, 0], eigenvectors[2, 1], eigenvectors[2, 2])
        )).to_4x4()

        # Transform points into OBB space to find extents
        inv_rotation = np.array(rotation_matrix.inverted())[:3, :3]
        points_obb = (inv_rotation @ centered.T).T

        # Find min/max along each axis
        min_bounds = np.min(points_obb, axis=0)
        max_bounds = np.max(points_obb, axis=0)

        # Box dimensions
        dimensions = max_bounds - min_bounds

        # Center of the box in OBB space, then transform to world
        obb_center_local = (min_bounds + max_bounds) / 2
        obb_center_world = eigenvectors @ obb_center_local + centroid

        # Store original object info before potentially switching modes
        original_name = obj.name
        original_parent = obj.parent
        original_collections = list(obj.users_collection)

        if was_edit_mode:
            bpy.ops.object.mode_set(mode='OBJECT')

        if replace_original:
            # Replace the original object's mesh data with a box
            box_mesh = bpy.data.meshes.new(name=f"{original_name}_OBB_mesh")
            
            # Create box vertices (unit cube scaled by dimensions)
            half = [d / 2 for d in dimensions]
            verts = [
                (-half[0], -half[1], -half[2]),
                ( half[0], -half[1], -half[2]),
                ( half[0],  half[1], -half[2]),
                (-half[0],  half[1], -half[2]),
                (-half[0], -half[1],  half[2]),
                ( half[0], -half[1],  half[2]),
                ( half[0],  half[1],  half[2]),
                (-half[0],  half[1],  half[2]),
            ]
            faces = [
                (3, 2, 1, 0),
                (5, 6, 7, 4),
                (1, 5, 4, 0),
                (3, 7, 6, 2),
                (4, 7, 3, 0),
                (2, 6, 5, 1),
            ]
            box_mesh.from_pydata(verts, [], faces)
            box_mesh.update()

            # Replace the mesh data
            old_mesh = obj.data
            obj.data = box_mesh
            
            # Remove old mesh if no other users
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)

            # Set the new transform
            obj.matrix_world = Matrix.Translation(obb_center_world) @ rotation_matrix

            # Rename with Box_ prefix if not already
            if not obj.name.startswith("Box_"):
                existing_names = {o.name for o in bpy.data.objects}
                for i in range(1, 1000):
                    box_name = f"Box_{i:03d}"
                    if box_name not in existing_names:
                        obj.name = box_name
                        break

            obj.select_set(True)
            context.view_layer.objects.active = obj

            # Assign COL material if toggle is enabled
            if context.scene.obb_assign_material:
                assign_col_material(context, obj)

            self.report({'INFO'}, f"Replaced with OBB: {obj.name} ({dimensions[0]:.3f} x {dimensions[1]:.3f} x {dimensions[2]:.3f})")
        else:
            # Create a new box object
            bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
            box_obj = context.active_object

            # Scale vertices to match dimensions
            for vert in box_obj.data.vertices:
                vert.co.x *= dimensions[0]
                vert.co.y *= dimensions[1]
                vert.co.z *= dimensions[2]

            # Set the box transform
            box_obj.matrix_world = Matrix.Translation(obb_center_world) @ rotation_matrix

            # Find unused box name and rename
            existing_names = {o.name for o in bpy.data.objects}
            for i in range(1, 1000):
                box_name = f"Box_{i:03d}"
                if box_name not in existing_names:
                    box_obj.name = box_name
                    break

            # Link to same collections as source object
            for col in box_obj.users_collection:
                col.objects.unlink(box_obj)
            for col in original_collections:
                col.objects.link(box_obj)

            # Parent to same parent as source
            if original_parent:
                box_obj.parent = original_parent

            obj.select_set(True)
            box_obj.select_set(True)
            context.view_layer.objects.active = box_obj

            # Assign COL material if toggle is enabled
            if context.scene.obb_assign_material:
                assign_col_material(context, box_obj)

            self.report({'INFO'}, f"Created OBB: {box_obj.name} ({dimensions[0]:.3f} x {dimensions[1]:.3f} x {dimensions[2]:.3f})")

        return {'FINISHED'}


class ComposedCOLPreferences(bpy.types.AddonPreferences):
    bl_idname = _addon_name

    col_material_name: bpy.props.StringProperty(
        name="COL Material Name",
        description="Name of the material to assign to collision objects",
        default="COL"
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "col_material_name")


class CreateEmptyPanel(bpy.types.Panel):
    bl_label = "Tim's COL Tools"
    bl_idname = "OBJECT_PT_create_empty"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'TimsTools'

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.operator(CreateEmptyOperator.bl_idname)
        row.operator(MakeBox.bl_idname, icon='CUBE')
        row.operator(MakeHull.bl_idname, icon='MESH_ICOSPHERE')

        layout.separator()

        row = layout.row(align=True)
        row.operator(AlignToFace.bl_idname, text="Align to Face", icon='ORIENTATION_NORMAL')
        row.operator(CreateOBB.bl_idname, text="Create OBB", icon='META_CUBE')
        row.prop(context.scene, "obb_replace_original", text="", icon='MESH_CUBE')
        row.prop(context.scene, "obb_assign_material", text="", icon='MATERIAL')


classes = [ComposedCOLPreferences, CreateEmptyOperator, MakeBox, MakeHull, AlignToFace, CreateOBB, CreateEmptyPanel]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.obb_replace_original = bpy.props.BoolProperty(
        name="Replace Original",
        description="Replace the original geometry with the OBB instead of creating a new object",
        default=False
    )
    
    bpy.types.Scene.obb_assign_material = bpy.props.BoolProperty(
        name="Assign COL Material",
        description="Assign the COL material to the created OBB (material name configurable in addon preferences)",
        default=False
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Scene.obb_replace_original
    del bpy.types.Scene.obb_assign_material
