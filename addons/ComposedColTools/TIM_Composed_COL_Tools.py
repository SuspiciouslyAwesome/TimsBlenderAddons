bl_info = { 
    "name": "Tim Composed COL Tools v2",
    "blender": (3, 4, 0),
    "category": "TimsTools",
    "description": "Composed COL Tools v2",
    "author": "Tim Richter",
    "version": (2, 0),
}

import bpy

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
        
        bpy.context.view_layer.objects.active = empty
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

class CreateEmptyPanel(bpy.types.Panel):
    bl_label = "Composed COL v2"
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

def register():
    bpy.utils.register_class(CreateEmptyOperator)
    bpy.utils.register_class(MakeBox)
    bpy.utils.register_class(MakeHull)
    bpy.utils.register_class(CreateEmptyPanel)

def unregister():
    bpy.utils.unregister_class(CreateEmptyOperator)
    bpy.utils.unregister_class(MakeBox)
    bpy.utils.unregister_class(MakeHull)
    bpy.utils.unregister_class(CreateEmptyPanel)

if __name__ == "__main__":
    register()