import bpy


class SelectionSetObject(bpy.types.PropertyGroup):
    obj: bpy.props.PointerProperty(type=bpy.types.Object)


class SelectionSet(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name", default="Set")
    objects: bpy.props.CollectionProperty(type=SelectionSetObject)


def store_selection(sel_set, context):
    sel_set.objects.clear()
    for obj in context.selected_objects:
        sel_set.objects.add().obj = obj


def ensure_object_mode(context):
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')


class OBJSETS_OT_add(bpy.types.Operator):
    bl_idname = "objsets.add"
    bl_label = "Add Selection Set"
    bl_description = "Store the current selection as a new set"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        sets = context.scene.object_selection_sets
        new = sets.add()
        new.name = f"Set {len(sets)}"
        store_selection(new, context)
        return {'FINISHED'}


class OBJSETS_OT_select(bpy.types.Operator):
    bl_idname = "objsets.select"
    bl_label = "Select Set"
    bl_description = "Deselect everything and select the stored objects"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        sets = context.scene.object_selection_sets
        if not 0 <= self.index < len(sets):
            return {'CANCELLED'}
        ensure_object_mode(context)

        view_layer = context.view_layer
        for obj in view_layer.objects:
            obj.select_set(False)

        first = None
        missing = 0
        for item in sets[self.index].objects:
            obj = item.obj
            if obj is None or obj.name not in view_layer.objects:
                missing += 1
                continue
            try:
                obj.select_set(True)
            except RuntimeError:  # hidden or unselectable
                missing += 1
                continue
            if first is None:
                first = obj
        if first is not None:
            view_layer.objects.active = first
        if missing:
            self.report({'INFO'}, f"{missing} stored object(s) not selectable (hidden, deleted or in another scene)")
        return {'FINISHED'}


class OBJSETS_OT_rename(bpy.types.Operator):
    bl_idname = "objsets.rename"
    bl_label = "Rename Selection Set"
    bl_description = "Rename this selection set"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()
    new_name: bpy.props.StringProperty(name="Name")

    def invoke(self, context, event):
        sets = context.scene.object_selection_sets
        if not 0 <= self.index < len(sets):
            return {'CANCELLED'}
        self.new_name = sets[self.index].name
        return context.window_manager.invoke_props_dialog(self, width=250)

    def draw(self, context):
        self.layout.activate_init = True
        self.layout.prop(self, "new_name")

    def execute(self, context):
        sets = context.scene.object_selection_sets
        if not 0 <= self.index < len(sets):
            return {'CANCELLED'}
        sets[self.index].name = self.new_name
        return {'FINISHED'}


class OBJSETS_OT_assign(bpy.types.Operator):
    bl_idname = "objsets.assign"
    bl_label = "Assign Selection"
    bl_description = "Replace this set's contents with the current selection"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        sets = context.scene.object_selection_sets
        if not 0 <= self.index < len(sets):
            return {'CANCELLED'}
        store_selection(sets[self.index], context)
        return {'FINISHED'}


class OBJSETS_OT_delete(bpy.types.Operator):
    bl_idname = "objsets.delete"
    bl_label = "Delete Selection Set"
    bl_description = "Delete this selection set (objects are not affected)"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        sets = context.scene.object_selection_sets
        if not 0 <= self.index < len(sets):
            return {'CANCELLED'}
        sets.remove(self.index)
        return {'FINISHED'}


class OBJSETS_PT_main(bpy.types.Panel):
    bl_label = "Selection Sets"
    bl_idname = "OBJSETS_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'TimsTools'

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        for i, sel_set in enumerate(context.scene.object_selection_sets):
            row = col.row(align=True)
            row.operator(OBJSETS_OT_select.bl_idname, text=sel_set.name).index = i
            row.operator(OBJSETS_OT_rename.bl_idname, text="", icon='FONT_DATA').index = i
            row.operator(OBJSETS_OT_assign.bl_idname, text="", icon='IMPORT').index = i
            row.operator(OBJSETS_OT_delete.bl_idname, text="", icon='X').index = i
        layout.operator(OBJSETS_OT_add.bl_idname, text="", icon='ADD')


classes = [
    SelectionSetObject,
    SelectionSet,
    OBJSETS_OT_add,
    OBJSETS_OT_select,
    OBJSETS_OT_rename,
    OBJSETS_OT_assign,
    OBJSETS_OT_delete,
    OBJSETS_PT_main,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.object_selection_sets = bpy.props.CollectionProperty(type=SelectionSet)


def unregister():
    del bpy.types.Scene.object_selection_sets
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
