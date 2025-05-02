import bpy
import os
import subprocess

class ExportOperator(bpy.types.Operator):
    bl_idname = "object.export_operator"
    bl_label = "🚀 Export gg"
    finalFilePath = ""
    finalObj = ""

    def apply_transforms_and_clear_animation(self, obj):
        obj.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        if obj.animation_data is not None:
            obj.animation_data_clear()
        obj.hide_viewport = False
        obj.hide_select = False
        obj.hide_render = False
        obj.hide_set(False)
        for child in obj.children:
            self.apply_transforms_and_clear_animation(child)

    def write_object(self, obj, file_path):
        original_location = obj.location.copy()
        obj.location = (0, 0, 0)
        self.apply_transforms_and_clear_animation(obj)
        print("Transforms and animation cleared")
        bpy.context.view_layer.objects.active = obj
        print("Object set as active")
        try:
            bpy.ops.export_scene.fbx(filepath=file_path, use_selection=True, use_mesh_modifiers=True, bake_anim=False, use_tspace=True)
            print("Export successful")
        except Exception as e:
            print(f"Export failed: {str(e)}")
        finally:
            obj.location = original_location  # Ensure the location is restored even if export fails

    def execute(self, context):
        obj = context.object
        if obj is None:
            self.report({'ERROR'}, "No object selected.")
            return {'CANCELLED'}
        while obj.parent is not None:
            obj = obj.parent
        print(f"Topmost parent: {obj.name}")
        directory_path = context.scene.directory_path
        if not directory_path:
            self.report({'ERROR'}, "Directory path not set. Please set a directory path.")
            return {'CANCELLED'}
        # Convert relative path to absolute path if necessary
        if directory_path.startswith('//'):
            directory_path = bpy.path.abspath(directory_path)
        # Ensure the directory exists
        os.makedirs(directory_path, exist_ok=True)
        file_name = f"{obj.name}.fbx"
        file_path = os.path.join(directory_path, file_name)
        # Check if the file exists in the main directory
        if os.path.exists(file_path):
            if not os.access(file_path, os.W_OK):
                self.report({'ERROR'}, f"⚠️ File exists, but is read-only: {file_path}. Did you forget to check it out?")
                return {'CANCELLED'}
            self.report({'INFO'}, f"✔️ Updating existing file: {file_path}")
            self.write_object(obj, file_path)
        else:
            # File does not exist - Check subdirectories
            for root, dirs, files in os.walk(directory_path):
                for file in files:
                    if file == file_name:
                        subdirectory_file_path = os.path.join(root, file)
                        if not os.access(subdirectory_file_path, os.W_OK):
                            self.report({'ERROR'}, f"⚠️ File exists, but is read-only: {subdirectory_file_path}. Did you forget to check it out?")
                            return {'CANCELLED'}
                        self.report({'INFO'}, f"✔️ Updating existing file in subdirectory: {subdirectory_file_path}")
                        self.write_object(obj, subdirectory_file_path)
                        return {'FINISHED'}
            # If no existing file found, show confirmation dialog
            self.finalObj = obj
            bpy.ops.object.confirm_create_file('INVOKE_DEFAULT', file_path=file_path, obj_name=obj.name)
        return {'FINISHED'}

class ConfirmCreateFileOperator(bpy.types.Operator):
    bl_idname = "object.confirm_create_file"
    bl_label = "⚠️ Creating new file:"
    bl_options = {'INTERNAL'}
    file_path: bpy.props.StringProperty()
    obj_name: bpy.props.StringProperty()

    def execute(self, context):
        # Retrieve the object by name
        obj = bpy.data.objects.get(self.obj_name)
        if obj:
            # Apply transforms and clear animation
            obj.select_set(True)
            original_location = obj.location.copy()
            obj.location = (0, 0, 0)
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
            if obj.animation_data is not None:
                obj.animation_data_clear()
            obj.hide_viewport = False
            obj.hide_select = False
            obj.hide_render = False
            obj.hide_set(False)
            for child in obj.children:
                child.select_set(True)
                bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
                if child.animation_data is not None:
                    child.animation_data_clear()
                child.hide_viewport = False
                child.hide_select = False
                child.hide_render = False
                child.hide_set(False)
            # Write the object to the file
            obj.location = (0, 0, 0)
            bpy.context.view_layer.objects.active = obj
            try:
                bpy.ops.export_scene.fbx(filepath=self.file_path, use_selection=True, use_mesh_modifiers=True, bake_anim=False, use_tspace=True)
                self.report({'INFO'}, f"✔️ Creating new file: {self.file_path}")
            except Exception as e:
                self.report({'ERROR'}, f"Export failed: {str(e)}")
            finally:
                obj.location = original_location  # Ensure the location is restored even if export fails
        else:
            self.report({'ERROR'}, "Object not found.")
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.label(text=self.file_path)

class OpenExplorerOperator(bpy.types.Operator):
    bl_idname = "object.open_explorer"
    bl_label = "📂"
    bl_description = "Open the directory in Windows Explorer"

    def execute(self, context):
        directory_path = context.scene.directory_path
        if not directory_path:
            self.report({'ERROR'}, "Directory path not set. Please set a directory path.")
            return {'CANCELLED'}
        # Convert relative path to absolute path if necessary
        if directory_path.startswith('//'):
            directory_path = bpy.path.abspath(directory_path)
        # Ensure the directory exists
        if not os.path.exists(directory_path):
            self.report({'ERROR'}, f"Directory does not exist: {directory_path}")
            return {'CANCELLED'}
        # Open the directory in Windows Explorer
        try:
            subprocess.Popen(f'explorer "{directory_path}"')
        except Exception as e:
            self.report({'ERROR'}, f"Failed to open directory: {str(e)}")
            return {'CANCELLED'}
        return {'FINISHED'}

class ExportPanel(bpy.types.Panel):
    bl_label = "Tims Better Exporter"
    bl_idname = "PT_ExportPanel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "TimsTools"

    def draw(self, context):
        layout = self.layout
        layout.operator("object.export_operator")
        # Create a row for the directory path and explorer button
        row = layout.row(align=True)
        row.prop(context.scene, "directory_path", text="")
        # Add a small spacer
        row.separator(factor=1.0)
        # Add the explorer button
        row.operator("object.open_explorer", text="", icon='FOLDER_REDIRECT')

# List of all classes to register
classes = (
    ExportOperator,
    OpenExplorerOperator,
    ExportPanel,
    ConfirmCreateFileOperator,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.directory_path = bpy.props.StringProperty(name="Directory Path", subtype='DIR_PATH')

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.directory_path
