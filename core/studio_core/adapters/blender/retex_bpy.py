"""bpy side of the mesh retexture steps; runs in the resident worker through its `script` command
(`import retex_bpy; retex_bpy.prepare(params)`), which exports the scene as GLB afterwards."""
import json
import math
import os

import bpy
import numpy as np

import retex_math as rm

CONVERTIBLE = {"CURVE", "SURFACE", "META", "FONT"}
DATA_ROLES = ("normal", "roughness", "metallic", "ao")


def _kwargs(op, want):
    props = op.get_rna_type().properties
    return {k: v for k, v in want.items() if k in props}


def import_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise ValueError(f"unsupported mesh format {ext}")
    for o in [o for o in bpy.context.scene.objects if o.type in CONVERTIBLE]:
        with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o],
                                       selected_editable_objects=[o]):
            bpy.ops.object.convert(target="MESH")
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and len(o.data.polygons)]
    if not meshes:
        raise ValueError("no mesh with faces in the file")
    return meshes


def _uv_array(me):
    layer = me.uv_layers[0]
    out = np.empty(len(me.loops) * 2, np.float32)
    attr = me.attributes.get(layer.name)
    if attr is not None and attr.data_type == "FLOAT2":
        attr.data.foreach_get("vector", out)
    else:
        layer.data.foreach_get("uv", out)
    return out.reshape(-1, 2)


def _corner_normals(me):
    out = np.empty(len(me.loops) * 3, np.float32)
    if hasattr(me, "corner_normals"):
        me.corner_normals.foreach_get("vector", out)
    else:
        me.calc_normals_split()
        me.loops.foreach_get("normal", out)
    return out.reshape(-1, 3)


def geometry(meshes):
    """World-space triangle corners, corner normals and first-layer UVs of the evaluated meshes."""
    deps = bpy.context.evaluated_depsgraph_get()
    pos, nrm, uvs = [], [], []
    for o in meshes:
        ev = o.evaluated_get(deps)
        me = ev.to_mesh()
        try:
            me.calc_loop_triangles()
            nt = len(me.loop_triangles)
            if not nt:
                continue
            co = np.empty(len(me.vertices) * 3, np.float32)
            me.vertices.foreach_get("co", co)
            tv = np.empty(nt * 3, np.int32)
            me.loop_triangles.foreach_get("vertices", tv)
            tl = np.empty(nt * 3, np.int32)
            me.loop_triangles.foreach_get("loops", tl)
            m = np.array(ev.matrix_world, np.float64)
            world = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
            nm = np.linalg.inv(m[:3, :3]).T
            cn = _corner_normals(me) @ nm.T
            cn /= np.maximum(np.linalg.norm(cn, axis=1, keepdims=True), 1e-12)
            pos.append(world[tv.reshape(-1, 3)])
            nrm.append(cn[tl.reshape(-1, 3)])
            uvs.append(_uv_array(me)[tl.reshape(-1, 3)] if me.uv_layers else np.zeros((nt, 3, 2), np.float32))
        finally:
            ev.to_mesh_clear()
    return (np.concatenate(pos).astype(np.float32), np.concatenate(nrm).astype(np.float32),
            np.concatenate(uvs).astype(np.float32))


def unwrap(meshes, margin):
    for o in meshes:
        if not o.data.uv_layers:
            o.data.uv_layers.new(name="UVMap")
        o.data.uv_layers.active_index = 0
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    sp = bpy.ops.uv.smart_project
    sp(**_kwargs(sp, {"angle_limit": math.radians(66), "island_margin": margin, "area_weight": 0.0,
                      "correct_aspect": True, "scale_to_bounds": False}))
    bpy.ops.uv.select_all(action="SELECT")
    pk = bpy.ops.uv.pack_islands
    pk(**_kwargs(pk, {"margin": margin, "rotate": True}))
    bpy.ops.object.mode_set(mode="OBJECT")


def prepare(p):
    meshes = import_any(p["src"])
    for o in meshes:
        if o.data.users > 1:
            o.data = o.data.copy()
    bpy.ops.export_scene.gltf(filepath=p["original"], export_format="GLB", use_selection=False, export_apply=True)
    mode = str(p.get("uv", "auto"))
    max_overlap = float(p.get("max_overlap", 0.02))
    had_uv = all(len(o.data.uv_layers) > 0 for o in meshes)
    warnings = []
    overlap_before = None
    if had_uv:
        _, _, uv = geometry(meshes)
        overlap_before, _ = rm.uv_overlap(uv, 512)
    unwrapped = False
    if mode == "new" or not had_uv:
        unwrapped = True
        if not had_uv:
            warnings.append("mesh_no_uv")
    elif overlap_before is not None and overlap_before > max_overlap:
        if mode == "auto":
            unwrapped = True
            warnings.append("uv_overlap_unwrapped")
        else:
            warnings.append("uv_overlap_kept")
    if unwrapped:
        unwrap(meshes, float(p.get("margin", 0.004)))
    pos, nrm, uv = geometry(meshes)
    overlap, coverage = rm.uv_overlap(uv, 512)
    np.savez_compressed(p["npz"], pos=pos, nrm=nrm, uv=uv)
    lo, hi = pos.reshape(-1, 3).min(0), pos.reshape(-1, 3).max(0)
    report = {"meshes": len(meshes), "tris": int(len(pos)), "had_uv": had_uv, "uv_mode": mode,
              "unwrapped": unwrapped, "overlap_before": None if overlap_before is None else round(overlap_before, 4),
              "overlap": round(overlap, 4), "uv_coverage": round(coverage, 4), "warnings": warnings,
              "bbox_min": [round(float(x), 4) for x in lo], "bbox_max": [round(float(x), 4) for x in hi],
              "materials_before": sorted({m.name for o in meshes for m in o.data.materials if m})}
    with open(p["report"], "w", encoding="utf-8") as f:
        json.dump(report, f)


def _tex(nodes, links, path, role, x, y):
    img = bpy.data.images.load(path)
    if role in DATA_ROLES:
        img.colorspace_settings.name = "Non-Color"
    n = nodes.new("ShaderNodeTexImage")
    n.image = img
    n.location = (x, y)
    return n


def apply(p):
    """Replaces every material of the imported base mesh with one material wired to the baked maps."""
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise ValueError("base mesh has no mesh objects")
    maps = p["maps"]
    mat = bpy.data.materials.new(str(p.get("name") or "retexture"))
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None) or nodes.new("ShaderNodeBsdfPrincipled")
    links.new(_tex(nodes, links, maps["albedo"], "albedo", -600, 300).outputs["Color"], bsdf.inputs["Base Color"])
    if maps.get("roughness"):
        links.new(_tex(nodes, links, maps["roughness"], "roughness", -600, 0).outputs["Color"],
                  bsdf.inputs["Roughness"])
    if maps.get("metallic"):
        links.new(_tex(nodes, links, maps["metallic"], "metallic", -600, -300).outputs["Color"],
                  bsdf.inputs["Metallic"])
    if maps.get("normal"):
        nm = nodes.new("ShaderNodeNormalMap")
        nm.location = (-250, -600)
        links.new(_tex(nodes, links, maps["normal"], "normal", -600, -600).outputs["Color"], nm.inputs["Color"])
        links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    for o in meshes:
        if o.data.uv_layers:
            o.data.uv_layers.active_index = 0
            o.data.uv_layers[0].active_render = True
        o.data.materials.clear()
        o.data.materials.append(mat)
