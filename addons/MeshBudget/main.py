import bpy
import numpy as np

MIB = 1024 * 1024

# Results of the last "Calculate" run: list of per-object dicts. Kept in memory
# only; the panel shows it until the next run.
_results = []


class MeshBudgetSettings(bpy.types.PropertyGroup):
    budget_mib: bpy.props.FloatProperty(
        name="Budget (MiB)", description="Memory budget per mesh",
        default=2.25, min=0.01, precision=3)
    bytes_per_vertex: bpy.props.FloatProperty(
        name="Bytes / Vertex",
        description="Size of one engine vertex (position, normal, UVs, bone, ...). "
                    "Use Calibrate to derive it from a known engine result",
        default=64.98, min=1.0, precision=2)
    bytes_per_index: bpy.props.FloatProperty(
        name="Bytes / Index",
        description="Size of one triangle index (2 = 16-bit, 4 = 32-bit, 0 = not counted)",
        default=0.0, min=0.0, precision=2)
    apply_modifiers: bpy.props.BoolProperty(
        name="Apply Modifiers", description="Measure the evaluated mesh (as exported)",
        default=True)
    split_uv_count: bpy.props.IntProperty(
        name="UV Maps", description="Number of UV maps that cause vertex splits",
        default=2, min=0, max=8)
    split_materials: bpy.props.BoolProperty(
        name="Split by Material",
        description="Vertices on a material boundary are duplicated (one draw batch per material)",
        default=True)
    normal_precision: bpy.props.IntProperty(
        name="Normal Precision", description="Decimal digits when comparing normals",
        default=3, min=1, max=6)
    uv_precision: bpy.props.IntProperty(
        name="UV Precision", description="Decimal digits when comparing UVs",
        default=4, min=1, max=7)
    max_materials: bpy.props.IntProperty(name="Material Cap", default=2, min=1)
    vertex_factor: bpy.props.FloatProperty(
        name="Vertex Factor",
        description="Extra vertex splits added after Blender (FBX import, 3ds Max, engine), "
                    "as a multiplier on the estimated vertex count. Set by Calibrate",
        default=1.882, min=0.1, precision=3)
    triangle_factor: bpy.props.FloatProperty(
        name="Triangle Factor",
        description="Extra triangles added after Blender, as a multiplier. Set by Calibrate",
        default=1.042, min=0.1, precision=3)

    ref_verts: bpy.props.IntProperty(name="Engine Vertices", min=0)
    ref_tris: bpy.props.IntProperty(name="Engine Triangles", min=0)
    ref_mib: bpy.props.FloatProperty(name="Engine MiB", min=0.0, precision=4)


def is_visible_mesh(obj, view_layer):
    return (obj.type == 'MESH'
            and not obj.hide_viewport
            and obj.visible_get(view_layer=view_layer))


def measure_mesh(mesh, s):
    """Return (tris, welded_verts, engine_verts, materials_used, uv_maps_used)."""
    mesh.calc_loop_triangles()
    n_tris = len(mesh.loop_triangles)
    n_loops = len(mesh.loops)
    if n_tris == 0 or n_loops == 0:
        return 0, len(mesh.vertices), 0, 0, 0

    vert = np.empty(n_loops, dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", vert)

    cols = [vert[:, None]]

    normals = np.empty(n_loops * 3, dtype=np.float32)
    mesh.corner_normals.foreach_get("vector", normals)
    cols.append(np.round(normals.reshape(-1, 3) * 10 ** s.normal_precision).astype(np.int64))

    uv_used = min(s.split_uv_count, len(mesh.uv_layers))
    uv_scale = 10 ** s.uv_precision
    for i in range(uv_used):
        uv = np.empty(n_loops * 2, dtype=np.float32)
        mesh.uv_layers[i].uv.foreach_get("vector", uv)
        cols.append(np.round(uv.reshape(-1, 2) * uv_scale).astype(np.int64))

    tri_loops = np.empty(n_tris * 3, dtype=np.int64)
    mesh.loop_triangles.foreach_get("loops", tri_loops)
    tri_mat = np.empty(n_tris, dtype=np.int64)
    mesh.loop_triangles.foreach_get("material_index", tri_mat)
    n_mats = len(np.unique(tri_mat))

    if s.split_materials:
        # A corner belongs to exactly one face, so its material is unambiguous.
        loop_mat = np.zeros(n_loops, dtype=np.int64)
        loop_mat[tri_loops] = np.repeat(tri_mat, 3)
        cols.append(loop_mat[:, None])

    keys = np.concatenate(cols, axis=1)[np.unique(tri_loops)]
    engine_verts = len(np.unique(keys, axis=0))
    return n_tris, len(mesh.vertices), engine_verts, n_mats, uv_used


def run_measure(context):
    s = context.scene.mesh_budget
    view_layer = context.view_layer
    depsgraph = context.evaluated_depsgraph_get()
    _results.clear()

    for obj in context.scene.objects:
        if not is_visible_mesh(obj, view_layer):
            continue
        if s.apply_modifiers:
            eval_obj = obj.evaluated_get(depsgraph)
            mesh = eval_obj.to_mesh()
        else:
            eval_obj = None
            mesh = obj.data
        try:
            tris, welded, verts, mats, uvs = measure_mesh(mesh, s)
        finally:
            if eval_obj is not None:
                eval_obj.to_mesh_clear()
        _results.append(dict(name=obj.name, tris=tris, welded=welded,
                             verts=verts, mats=mats, uvs=uvs))

    if context.window is not None:
        for area in context.window.screen.areas:
            area.tag_redraw()


class MESHBUDGET_OT_calculate(bpy.types.Operator):
    bl_idname = "meshbudget.calculate"
    bl_label = "Calculate Budget"
    bl_description = "Measure all visible mesh objects"

    def execute(self, context):
        run_measure(context)
        return {'FINISHED'}


class MESHBUDGET_OT_calibrate(bpy.types.Operator):
    bl_idname = "meshbudget.calibrate"
    bl_label = "Calibrate from Engine Numbers"
    bl_description = ("Measure the visible meshes now and derive the vertex factor, "
                      "triangle factor and bytes/vertex so the estimate matches the "
                      "engine numbers entered")

    def execute(self, context):
        s = context.scene.mesh_budget
        if s.ref_verts <= 0 or s.ref_mib <= 0:
            self.report({'ERROR'}, "Enter engine vertices and MiB first")
            return {'CANCELLED'}
        run_measure(context)
        est_verts = sum(r["verts"] for r in _results)
        est_tris = sum(r["tris"] for r in _results)
        if est_verts == 0:
            self.report({'ERROR'}, "No visible meshes to calibrate against")
            return {'CANCELLED'}
        s.vertex_factor = s.ref_verts / est_verts
        if s.ref_tris > 0 and est_tris > 0:
            s.triangle_factor = s.ref_tris / est_tris
        s.bytes_per_vertex = (s.ref_mib * MIB - s.ref_tris * 3 * s.bytes_per_index) / s.ref_verts
        self.report({'INFO'}, f"Vertex x{s.vertex_factor:.3f}, tris x{s.triangle_factor:.3f}, "
                              f"{s.bytes_per_vertex:.2f} B/vertex")
        return {'FINISHED'}


def scaled(r, s):
    """(engine vertices, engine triangles) estimated for one result."""
    return r["verts"] * s.vertex_factor, r["tris"] * s.triangle_factor


def object_bytes(r, s):
    verts, tris = scaled(r, s)
    return verts * s.bytes_per_vertex + tris * 3 * s.bytes_per_index


class MESHBUDGET_PT_panel(bpy.types.Panel):
    bl_label = "Mesh Budget"
    bl_idname = "MESHBUDGET_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Budget"

    def draw(self, context):
        layout = self.layout
        s = context.scene.mesh_budget
        layout.operator("meshbudget.calculate", icon='FILE_REFRESH')

        if not _results:
            layout.label(text="No results yet")
            return

        budget = s.budget_mib * MIB
        total = sum(object_bytes(r, s) for r in _results)
        frac = total / budget if budget else 0.0

        box = layout.box()
        row = box.row()
        row.alert = frac > 1.0
        row.label(text=f"{total / MIB:.3f} / {s.budget_mib:.3f} MiB  ({frac * 100:.1f}%)")
        remaining = budget - total
        box.label(text=(f"{remaining / MIB:.3f} MiB left" if remaining >= 0
                        else f"{-remaining / MIB:.3f} MiB OVER"))
        if s.bytes_per_vertex > 0:
            box.label(text=f"~{int(max(remaining, 0) / s.bytes_per_vertex)} more vertices fit")
        col = box.column(align=True)
        col.label(text=f"Triangles: ~{int(sum(scaled(r, s)[1] for r in _results))}"
                       f"  (Blender: {sum(r['tris'] for r in _results)})")
        col.label(text=f"Engine vertices: ~{int(sum(scaled(r, s)[0] for r in _results))}"
                       f"  (Blender: {sum(r['welded'] for r in _results)})")
        over = [r for r in _results if r["mats"] > s.max_materials]
        for r in over:
            row = box.row()
            row.alert = True
            row.label(text=f"{r['name']}: {r['mats']} materials (cap {s.max_materials})",
                      icon='ERROR')


class MESHBUDGET_PT_breakdown(bpy.types.Panel):
    bl_label = "Breakdown"
    bl_idname = "MESHBUDGET_PT_breakdown"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Budget"
    bl_parent_id = "MESHBUDGET_PT_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.mesh_budget
        for r in _results:
            sub = self.layout.box().column(align=True)
            sub.label(text=f"{r['name']}: {object_bytes(r, s) / MIB:.3f} MiB", icon='MESH_DATA')
            sub.label(text=f"{r['tris']} tris, {r['verts']} split verts (from {r['welded']})")


class MESHBUDGET_PT_settings(bpy.types.Panel):
    bl_label = "Settings"
    bl_idname = "MESHBUDGET_PT_settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Budget"
    bl_parent_id = "MESHBUDGET_PT_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = context.scene.mesh_budget
        for name in ("budget_mib", "vertex_factor", "triangle_factor", "bytes_per_vertex",
                     "bytes_per_index", "apply_modifiers", "split_uv_count",
                     "split_materials", "max_materials", "normal_precision", "uv_precision"):
            layout.prop(s, name)

        box = layout.box()
        box.label(text="Calibrate from engine numbers")
        box.prop(s, "ref_verts")
        box.prop(s, "ref_tris")
        box.prop(s, "ref_mib")
        box.operator("meshbudget.calibrate")


classes = [
    MeshBudgetSettings,
    MESHBUDGET_OT_calculate,
    MESHBUDGET_OT_calibrate,
    MESHBUDGET_PT_panel,
    MESHBUDGET_PT_breakdown,
    MESHBUDGET_PT_settings,
]
