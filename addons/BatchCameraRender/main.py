import os

import bpy

INVALID_FILENAME_CHARS = '<>:"/\\|?*'


def sanitize_filename(name):
    """Strip characters that are not allowed in a file name on Windows."""
    cleaned = "".join(
        "_" if c in INVALID_FILENAME_CHARS or ord(c) < 32 else c
        for c in name
    ).strip()
    return cleaned or "unnamed"


def split_render_path(raw):
    """Split render.filepath into (directory, name prefix).

    Done by hand instead of with os.path so Blender's `//` relative paths and
    mixed slash/backslash separators survive untouched.
    """
    index = max(raw.rfind("/"), raw.rfind("\\"))
    if index == -1:
        return "", raw
    return raw[:index + 1], raw[index + 1:]


def get_output_folder(self):
    return split_render_path(self.id_data.render.filepath)[0]


def set_output_folder(self, value):
    """Write the folder back into the Output tab, keeping its name prefix."""
    scene = self.id_data
    prefix = split_render_path(scene.render.filepath)[1]
    if value and not value.endswith(("/", "\\")):
        value += "/"
    scene.render.filepath = value + prefix


def scene_cameras(scene):
    return sorted(
        (obj for obj in scene.objects if obj.type == 'CAMERA'),
        key=lambda obj: obj.name,
    )


def enabled_cameras(scene):
    return [obj for obj in scene_cameras(scene) if obj.batch_render_include]


class BatchCameraRenderSettings(bpy.types.PropertyGroup):
    output_folder: bpy.props.StringProperty(
        name="Output Folder",
        description="Folder the batch renders are written to. "
                    "Mirrors the directory of the path in the Output tab",
        subtype='DIR_PATH',
        get=get_output_folder,
        set=set_output_folder,
    )
    file_name: bpy.props.StringProperty(
        name="File Name",
        description="Prefix put in front of the camera name. "
                    "Include your own separator, e.g. \"Shot_\" gives \"Shot_Camera.png\"",
        default="",
    )


class RENDER_OT_batch_cameras_toggle_all(bpy.types.Operator):
    bl_idname = "render.batch_cameras_toggle_all"
    bl_label = "Toggle All Cameras"
    bl_description = "Enable or disable every camera in the list"
    bl_options = {'INTERNAL'}

    enable: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        for cam in scene_cameras(context.scene):
            cam.batch_render_include = self.enable
        return {'FINISHED'}


class RENDER_OT_batch_cameras(bpy.types.Operator):
    bl_idname = "render.batch_cameras"
    bl_label = "Batch Render Cameras"
    bl_description = ("Render a still from every enabled camera and write it to "
                      "the output folder as FileName + CameraName")
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, width=460, confirm_text="Batch Render Selected Cameras")

    def draw(self, context):
        scene = context.scene
        props = scene.batch_camera_render
        layout = self.layout

        cameras = scene_cameras(scene)
        if not cameras:
            layout.label(text="This scene has no cameras", icon='ERROR')
            return

        header = layout.row(align=True)
        header.label(text=f"Cameras ({len(enabled_cameras(scene))}/{len(cameras)})")
        sub = header.row(align=True)
        sub.operator(RENDER_OT_batch_cameras_toggle_all.bl_idname,
                     text="All").enable = True
        sub.operator(RENDER_OT_batch_cameras_toggle_all.bl_idname,
                     text="None").enable = False

        box = layout.box().column(align=True)
        for cam in cameras:
            row = box.row(align=True)
            row.prop(cam, "batch_render_include", text="")
            row.label(text=cam.name,
                      icon='VIEW_CAMERA' if cam == scene.camera else 'CAMERA_DATA')

        layout.separator()
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(props, "output_folder")
        col.prop(props, "file_name")

        preview = enabled_cameras(scene)
        layout.separator()
        if preview:
            example = sanitize_filename(props.file_name + preview[0].name)
            layout.label(text=f"e.g. {example}{scene.render.file_extension}",
                         icon='FILE_IMAGE')
        else:
            layout.label(text="No cameras enabled", icon='INFO')

    def execute(self, context):
        scene = context.scene
        props = scene.batch_camera_render

        cameras = enabled_cameras(scene)
        if not cameras:
            self.report({'ERROR'}, "No cameras enabled")
            return {'CANCELLED'}

        if not props.output_folder:
            self.report({'ERROR'}, "No output folder set")
            return {'CANCELLED'}

        folder = bpy.path.abspath(props.output_folder)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            self.report({'ERROR'}, f"Cannot create output folder: {exc}")
            return {'CANCELLED'}

        extension = scene.render.file_extension
        original_camera = scene.camera
        written = 0
        skipped = 0
        cancelled = False

        wm = context.window_manager
        wm.progress_begin(0, len(cameras))
        try:
            for index, cam in enumerate(cameras):
                name = sanitize_filename(props.file_name + cam.name)
                path = os.path.join(folder, name + extension)

                if not scene.render.use_overwrite and os.path.exists(path):
                    skipped += 1
                    wm.progress_update(index + 1)
                    continue

                scene.camera = cam
                # write_still is off on purpose: Blender's own writer appends the
                # frame number to the path, we want the exact FileName+CameraName
                result = bpy.ops.render.render(write_still=False)
                if 'CANCELLED' in result:
                    cancelled = True
                    break

                image = bpy.data.images.get("Render Result")
                if image is None:
                    self.report({'WARNING'}, f"No render result for '{cam.name}'")
                    continue

                try:
                    image.save_render(filepath=path, scene=scene)
                except RuntimeError as exc:
                    self.report({'ERROR'}, f"Could not write '{path}': {exc}")
                    break

                written += 1
                wm.progress_update(index + 1)
        finally:
            scene.camera = original_camera
            wm.progress_end()

        message = f"Rendered {written} of {len(cameras)} cameras to {folder}"
        if skipped:
            message += f" ({skipped} skipped, overwrite is off)"
        if cancelled:
            self.report({'WARNING'}, message + " - cancelled")
            return {'CANCELLED'}

        self.report({'INFO'}, message)
        return {'FINISHED'}


def draw_render_menu(self, context):
    self.layout.separator()
    self.layout.operator(RENDER_OT_batch_cameras.bl_idname,
                         text="Batch Render Cameras...", icon='RENDER_STILL')


classes = [
    BatchCameraRenderSettings,
    RENDER_OT_batch_cameras_toggle_all,
    RENDER_OT_batch_cameras,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.batch_camera_render = bpy.props.PointerProperty(
        type=BatchCameraRenderSettings)
    # Lives on the object so the toggle is saved inside the .blend file
    bpy.types.Object.batch_render_include = bpy.props.BoolProperty(
        name="Batch Render",
        description="Include this camera in the batch render",
        default=True,
    )
    bpy.types.TOPBAR_MT_render.append(draw_render_menu)


def unregister():
    bpy.types.TOPBAR_MT_render.remove(draw_render_menu)
    del bpy.types.Object.batch_render_include
    del bpy.types.Scene.batch_camera_render

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
