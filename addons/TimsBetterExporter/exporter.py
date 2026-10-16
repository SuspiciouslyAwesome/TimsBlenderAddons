import bpy
import os
import subprocess

def export_hierarchy(op, context, obj, file_path):
    """Export obj and all its descendants to FBX without modifying the scene.

    Transforms are NOT applied: every object keeps its own location/rotation/scale,
    so mirror/bevel modifiers and child offsets survive. Only the root is moved to
    the world origin during the export (so the file is origin-relative); everything
    that is changed temporarily (root location, visibility, selection) is restored.
    """
    view_layer = context.view_layer
    objects = [obj] + [c for c in obj.children_recursive if c.name in view_layer.objects]

    saved_state = [(o, o.hide_viewport, o.hide_select, o.hide_get()) for o in objects]
    saved_selection = [o for o in context.selected_objects]
    saved_active = view_layer.objects.active
    saved_location = obj.location.copy()

    try:
        for o in objects:
            o.hide_viewport = False
            o.hide_select = False
            o.hide_set(False)

        bpy.ops.object.select_all(action='DESELECT')
        for o in objects:
            try:
                o.select_set(True)
            except RuntimeError:
                pass  # not selectable (e.g. hidden collection)
        view_layer.objects.active = obj

        # Only the root is recentred; its rotation/scale and all children stay untouched.
        # A parented root keeps its local transform (location is relative to the parent).
        if obj.parent is None:
            obj.location = (0, 0, 0)
            context.view_layer.update()

        bpy.ops.export_scene.fbx(
            filepath=file_path,
            use_selection=True,
            use_mesh_modifiers=True,
            bake_anim=False,
            use_tspace=True,
            bake_space_transform=context.scene.apply_transform,
            colors_type=context.scene.vertex_color_space
        )
        return True
    except Exception as e:
        op.report({'ERROR'}, f"Export failed: {str(e)}")
        return False
    finally:
        obj.location = saved_location
        for o, hide_vp, hide_sel, hidden in saved_state:
            o.hide_viewport = hide_vp
            o.hide_select = hide_sel
            o.hide_set(hidden)
        bpy.ops.object.select_all(action='DESELECT')
        for o in saved_selection:
            try:
                o.select_set(True)
            except (RuntimeError, ReferenceError):
                pass
        view_layer.objects.active = saved_active

class ExportOperator(bpy.types.Operator):
    bl_idname = "object.export_operator"
    bl_label = "🚀 Export"
    finalFilePath = ""
    finalObj = ""

    def write_object(self, context, obj, file_path):
        return export_hierarchy(self, context, obj, file_path)

    def export_single_object(self, context, obj):
        """Export a single object"""
        # Check if we should use topmost parent
        if context.scene.use_topmost_parent:
            while obj.parent is not None:
                obj = obj.parent
            print(f"Topmost parent: {obj.name}")
        else:
            print(f"Using selected object: {obj.name}")
        
        directory_path = context.scene.directory_path
        if not directory_path:
            self.report({'ERROR'}, "Directory path not set. Please set a directory path.")
            return False
        
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
                return False
            self.report({'INFO'}, f"✔️ Updating existing file: {file_path}")
            self.write_object(context, obj, file_path)
        else:
            # File does not exist - Check subdirectories
            found = False
            for root, dirs, files in os.walk(directory_path):
                for file in files:
                    if file == file_name:
                        subdirectory_file_path = os.path.join(root, file)
                        if not os.access(subdirectory_file_path, os.W_OK):
                            self.report({'ERROR'}, f"⚠️ File exists, but is read-only: {subdirectory_file_path}. Did you forget to check it out?")
                            return False
                        self.report({'INFO'}, f"✔️ Updating existing file in subdirectory: {subdirectory_file_path}")
                        self.write_object(context, obj, subdirectory_file_path)
                        found = True
                        break
                if found:
                    break
            
            if not found:
                # If no existing file found, show confirmation dialog
                self.finalObj = obj
                bpy.ops.object.confirm_create_file('INVOKE_DEFAULT', file_path=file_path, obj_name=obj.name)
        
        return True

    def execute(self, context):
        # Switch to object mode if not already in it
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        
        # Determine what to export - prioritize scene selection over collection
        objects_to_export = []
        
        # First check for selected objects in the scene
        selected_objects = [obj for obj in context.selected_objects 
                           if obj.type in ['MESH', 'EMPTY', 'ARMATURE'] 
                           and obj.name in context.view_layer.objects]
        
        if selected_objects:
            # Export selected objects
            objects_to_export = selected_objects
        elif hasattr(context, 'collection') and context.collection:
            # Only use collection if no objects are selected in the scene
            objects_to_export = [obj for obj in context.collection.objects 
                               if obj.type in ['MESH', 'EMPTY', 'ARMATURE'] 
                               and obj.name in context.view_layer.objects]
        
        if not objects_to_export:
            self.report({'ERROR'}, "No valid objects to export.")
            return {'CANCELLED'}
        
        # Export each object
        export_count = 0
        for obj in objects_to_export:
            if self.export_single_object(context, obj):
                export_count += 1
        
        if export_count > 0:
            self.report({'INFO'}, f"✔️ Exported {export_count} object(s)")
        
        return {'FINISHED'}

class ConfirmCreateFileOperator(bpy.types.Operator):
    bl_idname = "object.confirm_create_file"
    bl_label = "⚠️ Creating new file:"
    bl_options = {'INTERNAL'}
    file_path: bpy.props.StringProperty()
    obj_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.obj_name)
        if not obj:
            self.report({'ERROR'}, "Object not found.")
            return {'CANCELLED'}
        if obj.name not in context.view_layer.objects:
            self.report({'ERROR'}, f"Object '{obj.name}' is not in the current view layer.")
            return {'CANCELLED'}
        if export_hierarchy(self, context, obj, self.file_path):
            self.report({'INFO'}, f"✔️ Creating new file: {self.file_path}")
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
        
        # Determine button label based on selection - prioritize scene selection
        selected_objects = [obj for obj in context.selected_objects 
                           if obj.type in ['MESH', 'EMPTY', 'ARMATURE'] 
                           and obj.name in context.view_layer.objects]
        
        # Determine export button label
        if len(selected_objects) > 1:
            # Multiple objects selected in scene
            export_label = f"🚀 Export {len(selected_objects)}"
        elif len(selected_objects) == 1:
            # Single object selected
            export_label = "🚀 Export"
        elif hasattr(context, 'collection') and context.collection:
            # No scene selection, check collection
            collection_objects = [obj for obj in context.collection.objects 
                                 if obj.type in ['MESH', 'EMPTY', 'ARMATURE'] 
                                 and obj.name in context.view_layer.objects]
            if len(collection_objects) > 0:
                collection_name = context.collection.name
                export_label = f"🚀 Export {len(collection_objects)} from  {collection_name}"
            else:
                export_label = "🚀 Export"
        else:
            export_label = "🚀 Export"
        
        # Create a row for export button and explorer button
        row = layout.row(align=True)
        row.operator("object.export_operator", text=export_label)
        row.operator("object.open_explorer", text="", icon='FOLDER_REDIRECT')
        
        # Add collapsible options section
        box = layout.box()
        row = box.row()
        row.prop(context.scene, "show_export_options",
                 icon="TRIA_DOWN" if context.scene.show_export_options else "TRIA_RIGHT",
                 icon_only=True, emboss=False)
        row.label(text="Options")
        
        # Show options when expanded
        if context.scene.show_export_options:
            box.prop(context.scene, "directory_path", text="")
            box.prop(context.scene, "use_topmost_parent", text="Use topmost parent as root")
            box.prop(context.scene, "apply_transform", text="Apply Transform")
            box.prop(context.scene, "vertex_color_space", text="Vertex Colors")

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
    bpy.types.Scene.directory_path = bpy.props.StringProperty(
        name="Directory Path", 
        subtype='DIR_PATH'
    )
    bpy.types.Scene.show_export_options = bpy.props.BoolProperty(
        name="Show Export Options",
        default=False
    )
    bpy.types.Scene.use_topmost_parent = bpy.props.BoolProperty(
        name="Use Topmost Parent",
        description="Export the topmost parent of the selected object instead of just the selection",
        default=True
    )
    bpy.types.Scene.apply_transform = bpy.props.BoolProperty(
        name="Apply Transform",
        description="Bake axis conversion into mesh data for Unity compatibility",
        default=True
    )
    bpy.types.Scene.vertex_color_space = bpy.props.EnumProperty(
        name="Vertex Color Space",
        description="Color space for vertex colors in the exported FBX",
        items=[
            ('SRGB', "sRGB", "Export vertex colors in sRGB color space"),
            ('LINEAR', "Linear", "Export vertex colors in linear color space"),
            ('NONE', "None", "Do not export vertex colors"),
        ],
        default='SRGB'
    )

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.directory_path
    del bpy.types.Scene.show_export_options
    del bpy.types.Scene.use_topmost_parent
    del bpy.types.Scene.apply_transform
    del bpy.types.Scene.vertex_color_space
