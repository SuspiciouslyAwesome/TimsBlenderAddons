import bpy

_addon_name = __package__


def get_prefs(context):
    try:
        return context.preferences.addons[_addon_name].preferences
    except (KeyError, AttributeError):
        return None


def redraw_view3d_headers(self, context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class WorkLayersPreferences(bpy.types.AddonPreferences):
    bl_idname = _addon_name

    position: bpy.props.EnumProperty(
        name="Position",
        description="Where the work layer bar sits in the 3D viewport header",
        items=[
            ('LEFT', "Left", "Far left of the header, before the mode selector"),
            ('MENUS', "After Menus", "Right after the View/Add/Object menus"),
            ('RIGHT', "Right", "Far right end of the header"),
        ],
        default='RIGHT',
        update=redraw_view3d_headers,
    )
    button_width: bpy.props.FloatProperty(
        name="Button Width",
        description="Width per character of the layer buttons (lower = more compact)",
        default=0.36,
        min=0.2,
        max=0.8,
        update=redraw_view3d_headers,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "position")
        layout.prop(self, "button_width")


def find_layer_collection(layer_collection, collection):
    """Recursively find the LayerCollection wrapping the given collection."""
    if layer_collection.collection == collection:
        return layer_collection
    for child in layer_collection.children:
        found = find_layer_collection(child, collection)
        if found:
            return found
    return None


def all_scene_collections(collection):
    """Recursively yield all collections under the given collection."""
    for child in collection.children:
        yield child
        yield from all_scene_collections(child)


def include_with_children(view_layer, collection):
    """Re-include a collection and all its nested children in the view layer.

    Excluding a parent makes Blender set the exclude flag on all children,
    but re-including the parent does not clear them. Additionally, every
    write to `exclude` triggers a resync that rebuilds the layer-collection
    tree, so each child must be looked up freshly before writing to it.
    """
    lc = find_layer_collection(view_layer.layer_collection, collection)
    if lc and lc.exclude:
        lc.exclude = False
    for child in all_scene_collections(collection):
        lc = find_layer_collection(view_layer.layer_collection, child)
        if lc and lc.exclude:
            lc.exclude = False


def managed_collections(scene):
    """Return the valid collections in the work layer list (skips deleted ones)."""
    return [entry.collection for entry in scene.work_layers if entry.collection is not None]


class WorkLayerEntry(bpy.types.PropertyGroup):
    collection: bpy.props.PointerProperty(type=bpy.types.Collection)


class WORKLAYERS_OT_switch(bpy.types.Operator):
    bl_idname = "worklayers.switch"
    bl_label = "Switch Work Layer"
    bl_description = (
        "Show only this collection, exclude the other work layers.\n"
        "Shift+Click: toggle this layer without affecting the others"
    )
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()
    extend: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        scene = context.scene
        if self.index < 0 or self.index >= len(scene.work_layers):
            return {'CANCELLED'}

        target = scene.work_layers[self.index].collection
        if target is None:
            return {'CANCELLED'}

        view_layer = context.view_layer
        target_lc = find_layer_collection(view_layer.layer_collection, target)
        if target_lc is None:
            self.report({'WARNING'}, f"'{target.name}' is not in this view layer")
            return {'CANCELLED'}

        if self.extend:
            if target_lc.exclude:
                include_with_children(view_layer, target)
            else:
                target_lc.exclude = True
            return {'FINISHED'}

        for col in managed_collections(scene):
            if col == target:
                continue
            lc = find_layer_collection(view_layer.layer_collection, col)
            if lc and not lc.exclude:
                lc.exclude = True
        include_with_children(view_layer, target)
        return {'FINISHED'}


class WORKLAYERS_OT_add(bpy.types.Operator):
    bl_idname = "worklayers.add"
    bl_label = "Add Work Layer"
    bl_description = "Add this collection as a work layer button"
    bl_options = {'REGISTER', 'UNDO'}

    collection_name: bpy.props.StringProperty()

    def execute(self, context):
        scene = context.scene
        col = bpy.data.collections.get(self.collection_name)
        if col is None:
            return {'CANCELLED'}
        if col in managed_collections(scene):
            return {'CANCELLED'}
        entry = scene.work_layers.add()
        entry.collection = col
        return {'FINISHED'}


class WORKLAYERS_OT_remove(bpy.types.Operator):
    bl_idname = "worklayers.remove"
    bl_label = "Remove Work Layer"
    bl_description = "Remove this work layer button (does not delete the collection)"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        scene = context.scene
        if 0 <= self.index < len(scene.work_layers):
            scene.work_layers.remove(self.index)
            return {'FINISHED'}
        return {'CANCELLED'}


class WORKLAYERS_OT_move(bpy.types.Operator):
    bl_idname = "worklayers.move"
    bl_label = "Move Work Layer"
    bl_description = "Reorder this work layer button"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty()
    direction: bpy.props.IntProperty()  # -1 up, +1 down

    def execute(self, context):
        scene = context.scene
        new_index = self.index + self.direction
        if 0 <= self.index < len(scene.work_layers) and 0 <= new_index < len(scene.work_layers):
            scene.work_layers.move(self.index, new_index)
            return {'FINISHED'}
        return {'CANCELLED'}


class WORKLAYERS_PT_manage(bpy.types.Panel):
    bl_label = "Work Layers"
    bl_idname = "WORKLAYERS_PT_manage"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        managed = managed_collections(scene)

        col = layout.column()
        if scene.work_layers:
            col.label(text="Layers:")
            for i, entry in enumerate(scene.work_layers):
                row = col.row(align=True)
                if entry.collection is None:
                    row.label(text="<deleted collection>", icon='ERROR')
                else:
                    row.label(text=entry.collection.name, icon='OUTLINER_COLLECTION')
                op = row.operator(WORKLAYERS_OT_move.bl_idname, text="", icon='TRIA_UP')
                op.index = i
                op.direction = -1
                op = row.operator(WORKLAYERS_OT_move.bl_idname, text="", icon='TRIA_DOWN')
                op.index = i
                op.direction = 1
                row.operator(WORKLAYERS_OT_remove.bl_idname, text="", icon='X').index = i
            col.separator()

        candidates = [c for c in all_scene_collections(scene.collection) if c not in managed]
        if candidates:
            col.label(text="Add collection:")
            for c in candidates:
                col.operator(WORKLAYERS_OT_add.bl_idname, text=c.name,
                             icon='OUTLINER_COLLECTION').collection_name = c.name
        elif not scene.work_layers:
            col.label(text="No collections in scene", icon='INFO')
        else:
            col.label(text="All collections added", icon='CHECKMARK')

        prefs = get_prefs(context)
        if prefs:
            col.separator()
            col.label(text="Bar position:")
            col.row(align=True).prop(prefs, "position", expand=True)


def draw_bar(layout, context):
    scene = context.scene
    view_layer = context.view_layer
    prefs = get_prefs(context)
    char_width = prefs.button_width if prefs else 0.36

    row = layout.row(align=True)
    for i, entry in enumerate(scene.work_layers):
        col = entry.collection
        if col is None:
            continue
        lc = find_layer_collection(view_layer.layer_collection, col)
        active = lc is not None and not lc.exclude
        # Sub-row with an explicit width so the button hugs its label
        # instead of getting the header's default button width
        sub = row.row(align=True)
        sub.ui_units_x = max(1.5, len(col.name) * char_width + 0.8)
        sub.operator(WORKLAYERS_OT_switch.bl_idname, text=col.name,
                     depress=active).index = i
    row.popover(WORKLAYERS_PT_manage.bl_idname, text="", icon='ADD')


def get_position(context):
    prefs = get_prefs(context)
    return prefs.position if prefs else 'RIGHT'


def draw_header_left(self, context):
    if get_position(context) == 'LEFT':
        draw_bar(self.layout, context)


def draw_editor_menus(self, context):
    if get_position(context) == 'MENUS':
        draw_bar(self.layout, context)


def draw_header_right(self, context):
    if get_position(context) == 'RIGHT':
        draw_bar(self.layout, context)


classes = [
    WorkLayersPreferences,
    WorkLayerEntry,
    WORKLAYERS_OT_switch,
    WORKLAYERS_OT_add,
    WORKLAYERS_OT_remove,
    WORKLAYERS_OT_move,
    WORKLAYERS_PT_manage,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.work_layers = bpy.props.CollectionProperty(type=WorkLayerEntry)
    bpy.types.VIEW3D_HT_header.prepend(draw_header_left)
    bpy.types.VIEW3D_MT_editor_menus.append(draw_editor_menus)
    bpy.types.VIEW3D_HT_header.append(draw_header_right)


def unregister():
    bpy.types.VIEW3D_HT_header.remove(draw_header_right)
    bpy.types.VIEW3D_MT_editor_menus.remove(draw_editor_menus)
    bpy.types.VIEW3D_HT_header.remove(draw_header_left)
    del bpy.types.Scene.work_layers

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
