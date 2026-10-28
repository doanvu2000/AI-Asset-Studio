"""Resident headless Blender worker: line-delimited JSON over localhost TCP. stdlib + bpy + bundled numpy only.

Run: blender.exe -b --factory-startup -noaudio -P worker.py -- <port|0>
Request {"id", "cmd", "params"}; zero or more {"id", "event": "progress", ...}; then {"id", "ok", "result"|"error"}.
"""
import builtins
import contextlib
import io
import json
import math
import os
import socket
import sys
import tempfile
import time
import traceback

import addon_utils
import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleanup_math as cm  # noqa: E402
import skin_math  # noqa: E402

LIGHT_PRESETS = {
    # (rotation_euler degrees, energy) per sun; tuned in P0 because sun 3 + ambient 1 rendered too dark.
    "studio": {"key": ((50, 0, -35), 4.0), "fill": ((65, 0, 55), 1.6), "rim": ((-55, 0, 20), 3.0), "ambient": 1.6},
    "flat": {"key": ((40, 0, -20), 2.5), "fill": ((60, 0, 40), 1.5), "rim": ((-55, 0, 20), 1.0), "ambient": 2.5},
}


def reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def engine_ids():
    return [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]


def set_engine(scene, want):
    if want == "CYCLES" and "CYCLES" not in engine_ids():
        addon_utils.enable("cycles", default_set=True, persistent=True)
    try:
        scene.render.engine = want
    except TypeError as e:
        raise RuntimeError(f"engine {want} invalid: {e}")
    if want == "CYCLES":
        setup_cycles_gpu(scene)


def setup_cycles_gpu(scene):
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type == kind]
            if gpus:
                for d in prefs.devices:
                    d.use = d.type == kind
                scene.cycles.device = "GPU"
                return kind
        except TypeError:
            continue
    scene.cycles.device = "CPU"
    return "CPU"


def import_glb(path):
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no mesh in glb")
    return meshes


def world_verts(meshes):
    chunks = []
    deps = bpy.context.evaluated_depsgraph_get()
    for o in meshes:
        me = o.evaluated_get(deps).data
        co = np.empty(len(me.vertices) * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        m = np.array(o.matrix_world, dtype=np.float32)
        chunks.append(co @ m[:3, :3].T + m[:3, 3])
    return np.concatenate(chunks)


def add_lights(scene, p):
    preset = dict(LIGHT_PRESETS.get(p.get("lighting", "studio"), LIGHT_PRESETS["studio"]))
    for name in ("key", "fill", "rim"):
        rot, energy = preset[name]
        energy = p.get(name, energy)
        if energy <= 0:
            continue
        data = bpy.data.lights.new(name, "SUN")
        data.energy = energy
        obj = bpy.data.objects.new(name, data)
        obj.rotation_euler = tuple(math.radians(a) for a in rot)
        scene.collection.objects.link(obj)
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs[1].default_value = p.get("ambient", preset["ambient"])
    scene.world = world


def configure_output(scene, p, res):
    set_engine(scene, p.get("engine", "BLENDER_EEVEE"))
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = p.get("samples", 32)


def normal_material(cam):
    """Emits camera-space normals as 2D sprite normal-map colours (+X right, +Y up, +Z toward the camera).
    Dot products with the camera axes instead of Vector Transform, whose camera space differs between engines."""
    mat = bpy.data.materials.new("studio_normal")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    for n in list(nodes):
        nodes.remove(n)
    geo = nodes.new("ShaderNodeNewGeometry")
    combine = nodes.new("ShaderNodeCombineXYZ")
    m = cam.rotation_euler.to_matrix()
    for i in range(3):
        dot = nodes.new("ShaderNodeVectorMath")
        dot.operation = "DOT_PRODUCT"
        links.new(geo.outputs["Normal"], dot.inputs[0])
        dot.inputs[1].default_value = m.col[i]
        links.new(dot.outputs["Value"], combine.inputs[i])
    enc = nodes.new("ShaderNodeVectorMath")
    enc.operation = "MULTIPLY_ADD"
    enc.inputs[1].default_value = (0.5, 0.5, 0.5)
    enc.inputs[2].default_value = (0.5, 0.5, 0.5)
    links.new(combine.outputs[0], enc.inputs[0])
    emit = nodes.new("ShaderNodeEmission")
    links.new(enc.outputs[0], emit.inputs["Color"])
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(emit.outputs[0], out.inputs["Surface"])
    return mat


@contextlib.contextmanager
def normal_pass(scene, mat):
    vl = bpy.context.view_layer
    vs = scene.view_settings
    prev = (vl.material_override, vs.view_transform, vs.look, scene.render.dither_intensity, scene.render.engine)
    vl.material_override = mat
    # Raw keeps the encoded values linear in the PNG; Standard would apply the sRGB curve to them.
    vs.view_transform = "Raw"
    scene.render.dither_intensity = 0.0
    if scene.render.engine == "BLENDER_WORKBENCH":
        set_engine(scene, "BLENDER_EEVEE")
    try:
        yield
    finally:
        vl.material_override = prev[0]
        vs.view_transform, vs.look = prev[1], prev[2]
        scene.render.dither_intensity = prev[3]
        if scene.render.engine != prev[4]:
            set_engine(scene, prev[4])


def render_shots(scene, shots, normal_mat, emit):
    """Each shot is (path, setup, label); renders colour frames, then `<stem>_n.png` normal frames in a second pass so
    a Workbench job switches engine once instead of twice per frame. Returns per-shot
    (seconds, normal path or None, normal seconds or None)."""
    passes = 2 if normal_mat is not None else 1
    total, done = len(shots) * passes, 0
    out = []
    prev_path = scene.render.filepath
    try:
        for path, setup, label in shots:
            setup()
            scene.render.filepath = path
            t = time.perf_counter()
            bpy.ops.render.render(write_still=True)
            out.append([round(time.perf_counter() - t, 3), None, None])
            done += 1
            emit({"event": "progress", "value": done, "max": total, "label": label})
        if normal_mat is not None:
            with normal_pass(scene, normal_mat):
                for rec, (path, setup, label) in zip(out, shots):
                    setup()
                    rec[1] = scene.render.filepath = path[:-4] + "_n.png"
                    t = time.perf_counter()
                    bpy.ops.render.render(write_still=True)
                    rec[2] = round(time.perf_counter() - t, 3)
                    done += 1
                    emit({"event": "progress", "value": done, "max": total, "label": f"normal · {label}"})
    finally:
        scene.render.filepath = prev_path
    return out


def render_angles(count, mirror):
    step = 360 / count
    angles = [i * step for i in range(count)]
    # Mirror mode renders only 0..180; the core flips them for the other side (op.mirror_dirs).
    return [a for a in angles if a <= 180] if mirror else angles


def render_dirs(p, emit):
    t0 = time.perf_counter()
    reset()
    scene = bpy.context.scene
    meshes = import_glb(p["glb"])
    t_import = time.perf_counter() - t0

    pivot = bpy.data.objects.new("pivot", None)
    scene.collection.objects.link(pivot)
    v = world_verts(meshes)
    lo, hi = v.min(0), v.max(0)
    foot = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]))
    for o in [o for o in scene.objects if o.parent is None and o is not pivot]:
        o.location -= foot
        o.parent = pivot
    v = v - np.array(foot, dtype=np.float32)

    # One ortho scale for every direction: horizontal radius bounds any yaw, so frames stay pixel-aligned.
    elev = math.radians(p.get("elevation", 30))
    r = float(np.sqrt((v[:, :2] ** 2).sum(1)).max())
    zmin, zmax = float(v[:, 2].min()), float(v[:, 2].max())
    vlo = zmin * math.cos(elev) - r * math.sin(elev)
    vhi = zmax * math.cos(elev) + r * math.sin(elev)
    ortho = max(2 * r, vhi - vlo) * p.get("margin", 1.05)
    vmid = (vlo + vhi) / 2

    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    fwd = Vector((0, math.cos(elev), -math.sin(elev)))
    up = Vector((0, math.sin(elev), math.cos(elev)))
    cam.location = up * vmid - fwd * (r + zmax + 10)
    cam.rotation_euler = (math.pi / 2 - elev, 0, 0)
    cam_data.clip_end = (r + zmax) * 4 + 20
    scene.camera = cam

    add_lights(scene, p)
    supersample = max(1, int(p.get("supersample", 1)))
    res = int(p.get("res", 512)) * supersample
    configure_output(scene, p, res)

    out_dir = p["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    angles = render_angles(int(p.get("count", 8)), bool(p.get("mirror")))
    nmat = normal_material(cam) if p.get("normals") else None
    def turn(a):
        return lambda: setattr(pivot, "rotation_euler", (0, 0, math.radians(-a)))

    shots = [(os.path.join(out_dir, f"dir_{round(a):03d}.png"), turn(a), f"{round(a)}°") for a in angles]
    frames = [{"angle": a, "file": path, "s": s, **({"normal": npath, "normal_s": ns} if npath else {})}
              for a, (path, _, _), (s, npath, ns) in zip(angles, shots, render_shots(scene, shots, nmat, emit))]
    return {"import_s": round(t_import, 2), "total_s": round(time.perf_counter() - t0, 2),
            "engine": scene.render.engine, "ortho_scale": round(ortho, 4), "radius": round(r, 4),
            "height": round(zmax - zmin, 4), "verts": int(len(v)), "res": res, "supersample": supersample,
            "elevation": p.get("elevation", 30), "frames": frames,
            # Foot origin (0,0,0) projects to the frame's horizontal centre at up-offset 0 from the camera centre vmid.
            "pivot": [res / 2, res * (0.5 + vmid / ortho)]}


def upright_matrix(spec):
    """Axes are Blender's Z-up frame after glTF import."""
    rot = cm.parse_upright(spec)
    return None if rot is None else Matrix.Rotation(math.radians(rot[1]), 4, rot[0].upper())


def face_count(meshes):
    return sum(len(o.data.polygons) for o in meshes)


def bake_world(meshes):
    """Clear parents and apply transforms into vertex data so later edits work in world space."""
    for o in meshes:
        mw = o.matrix_world.copy()
        if o.data.users > 1:
            o.data = o.data.copy()
        o.parent = None
        o.data.transform(mw)
        o.matrix_world = Matrix.Identity(4)
    for o in [o for o in bpy.context.scene.objects if o.type != "MESH"]:
        bpy.data.objects.remove(o, do_unlink=True)


def bbox(meshes):
    v = world_verts(meshes)
    return v.min(0), v.max(0)


def merge_by_distance(meshes, dist):
    import bmesh
    removed = 0
    for o in meshes:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        n = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
        removed += n - len(bm.verts)
        bm.to_mesh(o.data)
        bm.free()
        o.data.update()
    return removed


def decimate(meshes, ratio):
    for o in meshes:
        mod = o.modifiers.new("studio_decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o]):
            bpy.ops.object.modifier_apply(modifier=mod.name)


def smooth_by_angle(meshes, angle_deg):
    """Decimated triangles shade faceted unless normals are re-derived; sharp edges above the angle stay hard."""
    if angle_deg <= 0:
        return
    for o in meshes:
        with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o],
                                       selected_editable_objects=[o]):
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(angle_deg))


def cleanup(p, emit):
    t0 = time.perf_counter()
    reset()
    meshes = import_glb(p["glb"])
    faces_before = face_count(meshes)
    lo, hi = bbox(meshes)
    dims_before = [round(float(x), 4) for x in hi - lo]
    emit({"event": "progress", "value": 1, "max": 5, "label": "import"})

    bake_world(meshes)
    rot = upright_matrix(p.get("upright", "none"))
    if rot is not None:
        for o in meshes:
            o.data.transform(rot)

    merged = 0
    dist = cm.merge_distance(p.get("merge_by_distance"))
    if dist > 0:
        merged = merge_by_distance(meshes, dist)
    emit({"event": "progress", "value": 2, "max": 5, "label": "merge"})

    faces_merged = face_count(meshes)
    ratio = cm.decimate_ratio(faces_merged, p.get("target_faces"))
    if ratio is not None:
        decimate(meshes, ratio)
        # Collapse overshoots on meshes with many tiny islands; one corrective pass is enough in practice.
        again = cm.decimate_ratio(face_count(meshes), int(int(p["target_faces"]) * 1.05))
        if again is not None:
            decimate(meshes, again * 0.97)
        smooth_by_angle(meshes, float(p.get("smooth_angle", 40)))
    emit({"event": "progress", "value": 3, "max": 5, "label": "decimate"})

    lo, hi = bbox(meshes)
    height = p.get("height")
    scale = cm.height_scale(float(hi[2] - lo[2]), height)
    origin = str(p.get("origin", "feet"))
    if origin == "feet":
        shift = Vector((-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]))
    elif origin == "center":
        shift = Vector([-(a + b) / 2 for a, b in zip(lo, hi)])
    else:
        shift = Vector((0, 0, 0))
    m = Matrix.Scale(scale, 4) @ Matrix.Translation(shift)
    for o in meshes:
        o.data.transform(m)
        o.data.update()
    lo, hi = bbox(meshes)
    emit({"event": "progress", "value": 4, "max": 5, "label": "transform"})

    bpy.ops.export_scene.gltf(filepath=p["out"], export_format="GLB", export_image_format="AUTO",
                              use_selection=False, export_apply=True)
    emit({"event": "progress", "value": 5, "max": 5, "label": "export"})
    return {"faces_before": faces_before, "faces_after": face_count(meshes),
            "verts_after": sum(len(o.data.vertices) for o in meshes), "merged_verts": merged,
            "decimate_ratio": None if ratio is None else round(ratio, 5),
            "dims_before": dims_before, "dims_after": [round(float(x), 4) for x in hi - lo],
            "min_after": [round(float(x), 4) for x in lo], "scale": round(scale, 6),
            "upright": str(p.get("upright", "none")), "applied_rotation": rot is not None, "origin": origin,
            "materials": len(bpy.data.materials), "images": len(bpy.data.images),
            "total_s": round(time.perf_counter() - t0, 2), "out": p["out"]}


def join_meshes(meshes):
    if len(meshes) > 1:
        with bpy.context.temp_override(object=meshes[0], active_object=meshes[0], selected_objects=meshes,
                                       selected_editable_objects=meshes):
            bpy.ops.object.join()
    return meshes[0]


def duplicate(o, name):
    c = o.copy()
    c.data = o.data.copy()
    c.name = name
    bpy.context.scene.collection.objects.link(c)
    return c


def select_only(objs, active):
    for o in bpy.context.scene.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = active


def smart_uv(o):
    select_only([o], o)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.01)
    bpy.ops.object.mode_set(mode="OBJECT")


def retopo(o, target_faces, diag):
    """New even topology; UVs and materials are lost, so the caller re-unwraps and bakes colour back from the source.
    QuadriFlow refuses TRELLIS meshes even after a voxel remesh (consistency check), so those get voxel + decimate."""
    select_only([o], o)
    merge_by_distance([o], diag * 1e-4)
    # A quad is two glTF triangles.
    res = bpy.ops.object.quadriflow_remesh(target_faces=max(target_faces // 2, 50), use_mesh_symmetry=False,
                                           use_preserve_sharp=True, use_preserve_boundary=True, seed=0)
    if "FINISHED" in res and o.data.polygons:
        return "quadriflow"
    o.data.remesh_voxel_size = diag / 200
    bpy.ops.object.voxel_remesh()
    ratio = cm.decimate_ratio(sum(len(f.vertices) - 2 for f in o.data.polygons), target_faces)
    if ratio is not None:
        decimate([o], ratio)
    if not o.data.polygons:
        raise RuntimeError("voxel remesh produced no faces")
    return "voxel+decimate"


def bake_image(name, size, non_color):
    img = bpy.data.images.new(name, size, size, alpha=False, float_buffer=False)
    img.colorspace_settings.name = "Non-Color" if non_color else "sRGB"
    return img


def bake_into(scene, high, low, img, bake_type, extrusion, emit_label, emit):
    for slot in low.material_slots:
        nt = slot.material.node_tree
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = img
        nt.nodes.active = node
    select_only([high, low], low)
    kw = {"pass_filter": {"COLOR"}} if bake_type == "DIFFUSE" else {}
    bpy.ops.object.bake(type=bake_type, use_selected_to_active=True, cage_extrusion=extrusion,
                        max_ray_distance=extrusion * 2, margin=8, **kw)
    for slot in low.material_slots:
        nt = slot.material.node_tree
        nt.nodes.remove(nt.nodes.active)
    emit({"event": "progress", "label": emit_label})


def gltf_occlusion_group():
    # The glTF exporter reads ambient occlusion from a node group with this exact name and an "Occlusion" input.
    g = bpy.data.node_groups.get("glTF Material Output")
    if g is None:
        g = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
        g.interface.new_socket("Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
    return g


def wire_maps(mat, normal_img, ao_img):
    nt = mat.node_tree
    bsdf = principled(mat)
    if normal_img:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = normal_img
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(tex.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    if ao_img:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = ao_img
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        grp = nt.nodes.new("ShaderNodeGroup")
        grp.node_tree = gltf_occlusion_group()
        nt.links.new(tex.outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs[0], grp.inputs["Occlusion"])


def game_ready(p, emit):
    t0 = time.perf_counter()
    reset()
    scene = bpy.context.scene
    high = join_meshes(import_glb(p["glb"]))
    bake_world([high])
    high.name = "high"
    faces_high = len(high.data.polygons)
    lo, hi = bbox([high])
    diag = float(np.linalg.norm(hi - lo))
    emit({"event": "progress", "value": 1, "max": 6, "label": "import"})

    low = duplicate(high, "lod0")
    for i, slot in enumerate(low.material_slots):
        if slot.material:
            low.material_slots[i].material = slot.material.copy()
    fallback, remesh = None, None
    if p["mode"] == "remesh":
        # On a copy: a failed remesh leaves its mesh voxelized or empty, and the fallback needs the original.
        trial = duplicate(low, "lod0_remesh")
        try:
            remesh = retopo(trial, p["target_faces"], diag)
        except RuntimeError as e:
            fallback = str(e)
            bpy.data.objects.remove(trial, do_unlink=True)
        else:
            bpy.data.objects.remove(low, do_unlink=True)
            low = trial
            low.name = "lod0"
    if p["mode"] == "decimate" or fallback:
        ratio = cm.decimate_ratio(len(low.data.polygons), p["target_faces"])
        if ratio is not None:
            decimate([low], ratio)
    if not low.data.uv_layers or p["mode"] == "remesh" and not fallback:
        smart_uv(low)
    smooth_by_angle([low], p["smooth_angle"])
    emit({"event": "progress", "value": 2, "max": 6, "label": "lod0"})

    set_engine(scene, "CYCLES")
    scene.cycles.samples = p["samples"]
    if scene.world is None:
        scene.world = bpy.data.worlds.new("w")
    extrusion = 0.01 * diag
    size = p["texture_size"]
    out_dir = os.path.dirname(p["out_prefix"])
    images = {}
    if not (p["mode"] == "remesh" and not fallback):
        if not any(s.material for s in low.material_slots):
            low.data.materials.clear()
        if not low.material_slots or any(s.material is None for s in low.material_slots):
            blank = bpy.data.materials.new("lod0")
            blank.use_nodes = True
            if not low.material_slots:
                low.data.materials.append(blank)
            for slot in low.material_slots:
                if slot.material is None:
                    slot.material = blank
    if p["mode"] == "remesh" and not fallback:
        mat = bpy.data.materials.new("lod0")
        mat.use_nodes = True
        low.data.materials.clear()
        low.data.materials.append(mat)
        color = bake_image("basecolor", size, False)
        bake_into(scene, high, low, color, "DIFFUSE", extrusion, "bake color", emit)
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = color
        mat.node_tree.links.new(tex.outputs["Color"], principled(mat).inputs["Base Color"])
        images["basecolor"] = color
    if "normal" in p["bake"]:
        images["normal"] = bake_image("normal", size, True)
        bake_into(scene, high, low, images["normal"], "NORMAL", extrusion, "bake normal", emit)
    if "ao" in p["bake"]:
        images["ao"] = bake_image("ao", size, True)
        bake_into(scene, high, low, images["ao"], "AO", extrusion, "bake ao", emit)
    for slot in low.material_slots:
        wire_maps(slot.material, images.get("normal"), images.get("ao"))
    files = {}
    for name, img in images.items():
        path = os.path.join(out_dir, f"{name}.png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        files[name] = path
    emit({"event": "progress", "value": 4, "max": 6, "label": "bake"})

    lods = [low]
    for i, r in enumerate(p["lods"][1:], 1):
        o = duplicate(low, f"lod{i}")
        o.data.materials.clear()
        for slot in low.material_slots:
            o.data.materials.append(slot.material)
        decimate([o], r)
        smooth_by_angle([o], p["smooth_angle"])
        lods.append(o)
    emit({"event": "progress", "value": 5, "max": 6, "label": "lods"})

    high.hide_render = True
    out = []
    for i, o in enumerate(lods):
        path = f"{p['out_prefix']}_lod{i}.glb"
        select_only([o], o)
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", export_image_format="AUTO",
                                  use_selection=True, export_apply=True)
        lo_i, hi_i = bbox([o])
        out.append({"file": path, "faces": len(o.data.polygons),
                    "tris": sum(len(f.vertices) - 2 for f in o.data.polygons),
                    "dims": [round(float(x), 4) for x in hi_i - lo_i]})
    # One file with every LOD sharing one texture set; nodes keep the usual _LODn naming (the exported Unity
    # bundle's editor script builds the LODGroup from it, since glTF importers do not).
    # Blender caps object names at 63 bytes; a cut "_LODn" tail would break the Unity script's LOD lookup.
    stem = (p.get("name") or os.path.basename(p["out_prefix"])).encode()[:50].decode(errors="ignore")
    for i, o in enumerate(lods):
        o.name = f"{stem}_LOD{i}"
    combined = f"{p['out_prefix']}_lods.glb"
    select_only(lods, lods[0])
    bpy.ops.export_scene.gltf(filepath=combined, export_format="GLB", export_image_format="AUTO",
                              use_selection=True, export_apply=True)
    emit({"event": "progress", "value": 6, "max": 6, "label": "export"})
    return {"faces_high": faces_high, "mode": p["mode"], "remesh": remesh, "fallback": fallback, "lods": out,
            "combined": combined, "maps": files,
            "device": scene.cycles.device, "dims_high": [round(float(x), 4) for x in hi - lo],
            "total_s": round(time.perf_counter() - t0, 2)}


def warmup(p, emit):
    t0 = time.perf_counter()
    reset()
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_cube_add()
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = (4, -4, 3)
    cam.rotation_euler = (math.radians(65), 0, math.radians(45))
    scene.collection.objects.link(cam)
    scene.camera = cam
    add_lights(scene, {})
    configure_output(scene, p, 64)
    scene.render.filepath = os.path.join(tempfile.gettempdir(), f"studio_blender_warmup_{os.getpid()}.png")
    bpy.ops.render.render(write_still=True)
    try:
        os.remove(scene.render.filepath)
    except OSError:
        pass
    return {"s": round(time.perf_counter() - t0, 2), "engine": scene.render.engine}


SCRIPT_FILE = "<studio_script>"
SCRIPT_IMPORTS = {"bpy", "bmesh", "math", "mathutils", "random", "colorsys", "itertools", "functools", "bpy_extras",
                  "numpy"}
# Guards generated code against accidental file/process access; bpy itself can still touch disk, so not a sandbox.
BLOCKED_BUILTINS = {"open", "exec", "eval", "compile", "input", "breakpoint", "exit", "quit", "__import__"}
CONVERTIBLE = ("CURVE", "SURFACE", "META", "FONT")


class ScriptError(Exception):
    def __init__(self, code, message, trace=""):
        super().__init__(message)
        self.code = code
        self.trace = trace


def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".")[0] not in SCRIPT_IMPORTS:
        raise ImportError(f"import of {name!r} is not allowed in studio scripts")
    return builtins.__import__(name, globals, locals, fromlist, level)


def script_builtins(unrestricted):
    if unrestricted:
        return builtins.__dict__
    b = {k: v for k, v in builtins.__dict__.items() if k not in BLOCKED_BUILTINS}
    b["__import__"] = guarded_import
    return b


def deadline_tracer(deadline):
    """Line-level tracing only inside script frames, so bpy/stdlib calls run untraced at full speed."""
    def local(frame, event, arg):
        if time.monotonic() > deadline:
            raise TimeoutError("script exceeded its time limit")
        return local

    def call(frame, event, arg):
        return local if frame.f_code.co_filename == SCRIPT_FILE else None

    return call


def script_trace(exc):
    te = traceback.TracebackException.from_exception(exc)
    te.stack = traceback.StackSummary.from_list([f for f in te.stack if f.filename == SCRIPT_FILE])
    return "".join(te.format())[-3000:]


def srgb_to_linear(color):
    if isinstance(color, str):
        h = color.lstrip("#")
        color = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [float(x) for x in color]
    rgb = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c[:3]]
    return (*rgb, c[3] if len(c) > 3 else 1.0)


def principled(mat):
    if mat.node_tree is None:
        mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) or nodes.new("ShaderNodeOutputMaterial")
        mat.node_tree.links.new(bsdf.outputs[0], out.inputs[0])
    return bsdf


def set_input(node, names, value):
    for n in names:
        sock = node.inputs.get(n)
        if sock is not None:
            sock.default_value = value
            return True
    return False


def new_material(name, color, metallic=0.0, roughness=0.5, emission=None, emission_strength=0.0):
    """color/emission: sRGB like a color picker, (r, g, b[, a]) in 0..1 or '#rrggbb'."""
    mat = bpy.data.materials.new(name)
    bsdf = principled(mat)
    rgba = srgb_to_linear(color)
    set_input(bsdf, ("Base Color",), rgba)
    set_input(bsdf, ("Metallic",), float(metallic))
    set_input(bsdf, ("Roughness",), float(roughness))
    if emission is not None:
        set_input(bsdf, ("Emission Color", "Emission"), srgb_to_linear(emission))
        set_input(bsdf, ("Emission Strength",), float(emission_strength or 1.0))
    mat.diffuse_color = rgba
    return mat


def convert_to_mesh(objs):
    for o in objs:
        with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o],
                                       selected_editable_objects=[o]):
            bpy.ops.object.convert(target="MESH")


def exec_script(code, p):
    import linecache
    linecache.cache[SCRIPT_FILE] = (len(code), None, code.splitlines(True), SCRIPT_FILE)
    try:
        compiled = compile(code, SCRIPT_FILE, "exec")
    except SyntaxError as e:
        raise ScriptError("blender_script_error", f"SyntaxError: {e.msg} (line {e.lineno})",
                          "".join(traceback.format_exception_only(e)))
    import bmesh
    from mathutils import Euler, Quaternion
    ns = {"__name__": "__studio_script__", "__builtins__": script_builtins(bool(p.get("unrestricted"))),
          "bpy": bpy, "bmesh": bmesh, "math": math, "Vector": Vector, "Matrix": Matrix, "Euler": Euler,
          "Quaternion": Quaternion, "params": dict(p.get("params") or {}), "new_material": new_material,
          "principled": principled}
    out = io.StringIO()
    prev = sys.gettrace()
    sys.settrace(deadline_tracer(time.monotonic() + float(p.get("timeout_s", 120))))
    try:
        with contextlib.redirect_stdout(out):
            exec(compiled, ns)
    except KeyboardInterrupt:
        raise
    except TimeoutError as e:
        raise ScriptError("blender_script_timeout", str(e), script_trace(e))
    except BaseException as e:
        raise ScriptError("blender_script_error", f"{type(e).__name__}: {e}", script_trace(e))
    finally:
        sys.settrace(prev)
    return out.getvalue()


def run_script(p, emit):
    t0 = time.perf_counter()
    reset()
    if p.get("glb_in"):
        import_glb(p["glb_in"])
    emit({"event": "progress", "value": 0, "max": 3, "label": "script"})
    stdout = exec_script(p.get("code") or "", p)
    script_s = time.perf_counter() - t0
    scene = bpy.context.scene
    convert_to_mesh([o for o in scene.objects if o.type in CONVERTIBLE])
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        raise ScriptError("blender_script_error", "script left no mesh objects in the scene")
    deps = bpy.context.evaluated_depsgraph_get()
    tris = verts = 0
    for o in meshes:
        me = o.evaluated_get(deps).data
        me.calc_loop_triangles()
        tris += len(me.loop_triangles)
        verts += len(me.vertices)
    lo, hi = bbox(meshes)
    mats = {m.name for o in meshes for m in o.data.materials if m}
    emit({"event": "progress", "value": 1, "max": 3, "label": "export"})
    bpy.ops.export_scene.gltf(filepath=p["out"], export_format="GLB", export_apply=True, use_selection=False)
    result = {"objects": [o.name for o in meshes][:64], "mesh_objects": len(meshes), "tris": tris, "verts": verts,
              "materials": sorted(mats), "no_material": [o.name for o in meshes if not any(o.data.materials)][:64],
              "bbox_min": [round(float(x), 4) for x in lo], "bbox_max": [round(float(x), 4) for x in hi],
              "stdout": stdout[-2000:], "script_s": round(script_s, 2), "out": p["out"]}
    if p.get("render"):
        result["render"] = render_dirs({**p["render"], "glb": p["out"], "out_dir": p["render_dir"]}, emit)
    result["total_s"] = round(time.perf_counter() - t0, 2)
    return result


# bone, parent, head joint, tail joint (joints are the 19-point schema Claude marks on the front render)
RIG_BONES = (
    ("spine", None, "pelvis", "neck"),
    ("head", "spine", "neck", "head_top"),
    *((f"pelvis_{s}", None, "pelvis", f"hip_{s}") for s in "lr"),
    *((f"thigh_{s}", f"pelvis_{s}", f"hip_{s}", f"knee_{s}") for s in "lr"),
    *((f"shin_{s}", f"thigh_{s}", f"knee_{s}", f"ankle_{s}") for s in "lr"),
    *((f"foot_{s}", f"shin_{s}", f"ankle_{s}", f"foot_tip_{s}") for s in "lr"),
    *((f"collar_{s}", "spine", "neck", f"shoulder_{s}") for s in "lr"),
    *((f"upper_arm_{s}", f"collar_{s}", f"shoulder_{s}", f"elbow_{s}") for s in "lr"),
    *((f"forearm_{s}", f"upper_arm_{s}", f"elbow_{s}", f"wrist_{s}") for s in "lr"),
    *((f"hand_{s}", f"forearm_{s}", f"wrist_{s}", f"hand_tip_{s}") for s in "lr"),
)


def joints_to_world(joints, verts, p):
    """Front render pixels -> world. x/z come from the ortho camera; depth is the mesh's median y near that point."""
    w = float(p["image_w"])
    scale = float(p["ortho_scale"]) / w
    px, py = (float(c) * w / float(p["render_res"]) for c in p["pivot"])
    height = float(verts[:, 2].max() - verts[:, 2].min()) or 1.0
    out = {}
    for name, (jx, jy) in joints.items():
        x, z = (float(jx) - px) * scale, (py - float(jy)) * scale
        near = verts[(np.abs(verts[:, 2] - z) < 0.04 * height) & (np.abs(verts[:, 0] - x) < 0.06 * height)]
        y = float(np.median(near[:, 1])) if len(near) >= 8 else float(np.median(verts[:, 1]))
        out[name] = Vector((x, y, z))
    return out


def build_armature(scene, pts):
    data = bpy.data.armatures.new("rig")
    arm = bpy.data.objects.new("rig", data)
    scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for name, parent, a, b in RIG_BONES:
        eb = data.edit_bones.new(name)
        eb.head, eb.tail = pts[a], pts[b]
        if (eb.tail - eb.head).length < 1e-4:
            eb.tail = eb.head + Vector((0, 0, 0.01))
        eb.roll = 0.0
        if parent:
            eb.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def _parent(objs, arm, kind):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type=kind)


def _weighted(o):
    if not len(o.vertex_groups):
        return False
    n = len(o.data.vertices)
    return sum(1 for v in o.data.vertices if len(v.groups)) > 0.95 * n


def _distance_weights(meshes, arm, height):
    """Inverse-distance skinning to the two nearest bone segments. Generated meshes are rarely watertight, which
    makes Blender's bone heat fail outright, and envelopes leave far vertices (capes, shields) unweighted."""
    bones = [b for b in arm.data.bones if b.use_deform]
    heads = np.array([b.head_local for b in bones], dtype=np.float32)
    tails = np.array([b.tail_local for b in bones], dtype=np.float32)
    names = [b.name for b in bones]
    inv = np.array(arm.matrix_world.inverted(), dtype=np.float32)
    for o in meshes:
        n = len(o.data.vertices)
        co = np.empty(n * 3, dtype=np.float32)
        o.data.vertices.foreach_get("co", co)
        m = np.array(o.matrix_world, dtype=np.float32)
        pts = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        pts = pts @ inv[:3, :3].T + inv[:3, 3]
        edges = np.empty(len(o.data.edges) * 2, dtype=np.int32)
        o.data.edges.foreach_get("vertices", edges)
        near, w = skin_math.distance_weights(pts, heads, tails, names, height, edges=edges.reshape(-1, 2))
        o.vertex_groups.clear()
        groups = [o.vertex_groups.new(name=b.name) for b in bones]
        for i in range(n):
            for k in range(near.shape[1]):
                if w[i, k] > 0.01:
                    groups[near[i, k]].add([i], float(w[i, k]), "REPLACE")


def _unbind(meshes):
    for o in meshes:
        o.vertex_groups.clear()
        for m in [m for m in o.modifiers if m.type == "ARMATURE"]:
            o.modifiers.remove(m)


def bind_mesh(meshes, arm, height):
    """Bone heat when Blender can solve it, else distance skinning. Returns which one held."""
    _parent(meshes, arm, "ARMATURE_AUTO")
    if all(_weighted(o) for o in meshes):
        return "auto"
    _unbind(meshes)
    _distance_weights(meshes, arm, height)
    _parent(meshes, arm, "ARMATURE")
    return "distance"


def walk_pose(phase, height):
    """Bone -> (world axis, degrees) plus root drop for one walk phase; forward is -Y (the model faces the camera)."""
    s = math.sin(phase)
    c = math.cos(phase)
    pose = {}
    for side, sign in (("l", 1.0), ("r", -1.0)):
        swing = sign * s
        pose[f"thigh_{side}"] = ("X", -28.0 * swing)
        pose[f"shin_{side}"] = ("X", 40.0 * max(0.0, sign * c))
        pose[f"foot_{side}"] = ("X", -12.0 * max(0.0, sign * c))
        pose[f"upper_arm_{side}"] = ("X", 22.0 * swing)
        pose[f"forearm_{side}"] = ("X", -15.0 - 10.0 * max(0.0, swing))
    pose["spine"] = ("Z", 4.0 * s)
    drop = -0.025 * height * abs(c)
    return pose, drop


def run_pose(phase, height):
    s = math.sin(phase)
    c = math.cos(phase)
    pose = {}
    for side, sign in (("l", 1.0), ("r", -1.0)):
        swing = sign * s
        lift = max(0.0, sign * c)
        pose[f"thigh_{side}"] = ("X", -42.0 * swing - 8.0 * lift)
        pose[f"shin_{side}"] = ("X", 15.0 + 75.0 * lift)
        pose[f"foot_{side}"] = ("X", -20.0 * lift)
        pose[f"upper_arm_{side}"] = ("X", 38.0 * swing)
        pose[f"forearm_{side}"] = ("X", -70.0 - 15.0 * max(0.0, -swing))
    pose["spine"] = {"X": 12.0, "Z": 7.0 * s}
    pose["head"] = ("X", -8.0)
    drop = -0.045 * height * abs(c)
    return pose, drop


def idle_pose(phase, height):
    s = math.sin(phase)
    breath = (1 - math.cos(phase)) / 2
    pose = {"spine": ("X", -1.5 * breath), "head": ("X", 1.0 * s)}
    for side, sign in (("l", 1.0), ("r", -1.0)):
        pose[f"upper_arm_{side}"] = ("Y", -sign * (2.0 + 2.5 * breath))
        pose[f"forearm_{side}"] = ("X", -6.0 - 3.0 * breath)
    return pose, -0.006 * height * breath


STANCE = {"thigh_l": {"X": -14.0}, "shin_l": {"X": 10.0}, "thigh_r": {"X": 12.0}, "shin_r": {"X": 14.0},
          "foot_r": {"X": -6.0}}
# (t, bone -> {axis: deg}, drop as a fraction of height). The right arm (image left in the front view) swings.
ATTACK_KEYS = (
    (0.0, {}, 0.0),
    (0.35, {**STANCE, "upper_arm_r": {"X": -150.0, "Y": -10.0}, "forearm_r": {"X": -55.0},
            "spine": {"X": -5.0, "Z": -18.0}, "upper_arm_l": {"X": -20.0}, "forearm_l": {"X": -30.0}}, -0.01),
    (0.5, {**STANCE, "upper_arm_r": {"X": -45.0}, "forearm_r": {"X": -8.0},
           "spine": {"X": 12.0, "Z": 20.0}, "head": {"X": -6.0}, "upper_arm_l": {"X": 15.0},
           "forearm_l": {"X": -25.0}}, -0.035),
    (0.65, {**STANCE, "upper_arm_r": {"X": -15.0}, "forearm_r": {"X": -5.0},
            "spine": {"X": 14.0, "Z": 24.0}, "head": {"X": -6.0}, "upper_arm_l": {"X": 18.0},
            "forearm_l": {"X": -25.0}}, -0.04),
    (1.0, {}, 0.0),
)


def _smooth(x):
    return x * x * (3 - 2 * x)


def keyed_pose(keys, t, height):
    t = min(1.0, max(0.0, t))
    i = next((k for k in range(len(keys) - 1) if t <= keys[k + 1][0]), len(keys) - 2)
    (t0, a, d0), (t1, b, d1) = keys[i], keys[i + 1]
    w = _smooth((t - t0) / (t1 - t0) if t1 > t0 else 1.0)
    pose = {}
    for bone in set(a) | set(b):
        ra, rb = a.get(bone, {}), b.get(bone, {})
        pose[bone] = {ax: ra.get(ax, 0.0) * (1 - w) + rb.get(ax, 0.0) * w for ax in set(ra) | set(rb)}
    return pose, (d0 * (1 - w) + d1 * w) * height


def clip_pose(name, fi, n, loop, height):
    """Loops sample [0, 1) so the cycle closes without a duplicate frame; one-shots include both ends."""
    t = fi / n if loop else fi / max(1, n - 1)
    if name == "walk":
        return walk_pose(2 * math.pi * t, height)
    if name == "run":
        return run_pose(2 * math.pi * t, height)
    if name == "idle":
        return idle_pose(2 * math.pi * t, height)
    if name == "attack":
        return keyed_pose(ATTACK_KEYS, t, height)
    raise RuntimeError(f"unknown clip {name}")


def _rotation(spec):
    if isinstance(spec, dict):
        m = Matrix.Identity(4)
        for ax in ("X", "Y", "Z"):
            if spec.get(ax):
                m = Matrix.Rotation(math.radians(spec[ax]), 4, ax) @ m
        return m
    axis, deg = spec
    return Matrix.Rotation(math.radians(deg), 4, axis)


def apply_pose(arm, pose, drop):
    """Rotations are about world axes through each bone's rest head, composed down the chain."""
    deltas = {}
    for name, parent, _, _ in RIG_BONES:
        bone = arm.data.bones[name]
        parent_delta = deltas.get(parent, Matrix.Translation((0, 0, drop)))
        head = bone.head_local
        local = Matrix.Translation(head) @ _rotation(pose.get(name, ("X", 0.0))) @ Matrix.Translation(-head)
        deltas[name] = parent_delta @ local
        pb = arm.pose.bones[name]
        pb.matrix = deltas[name] @ bone.matrix_local
        bpy.context.view_layer.update()


def animate_dirs(p, emit):
    t0 = time.perf_counter()
    reset()
    scene = bpy.context.scene
    meshes = import_glb(p["glb"])
    pivot = bpy.data.objects.new("pivot", None)
    scene.collection.objects.link(pivot)
    v = world_verts(meshes)
    lo, hi = v.min(0), v.max(0)
    foot = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]))
    for o in [o for o in scene.objects if o.parent is None and o is not pivot]:
        o.location -= foot
    bpy.context.view_layer.update()
    v = world_verts(meshes)
    height = float(v[:, 2].max() - v[:, 2].min())
    pts = joints_to_world(p["joints"], v, p)
    arm = build_armature(scene, pts)
    weights = bind_mesh(meshes, arm, height)
    arm.parent = pivot

    frames_n = max(2, int(p.get("frames", 8)))
    elev = math.radians(p.get("elevation", 30))
    # Limbs swing past the rest silhouette, so the shared ortho box gets extra margin over render_dirs.
    r = float(np.sqrt((v[:, :2] ** 2).sum(1)).max()) * 1.3
    zmin, zmax = float(v[:, 2].min()), float(v[:, 2].max())
    vlo = zmin * math.cos(elev) - r * math.sin(elev) - 0.03 * height
    clips = p.get("clips") or [{"name": "walk", "frames": frames_n, "loop": True}]
    # The attack wind-up lifts the hand above the head.
    headroom = 0.3 * height if any(c["name"] == "attack" for c in clips) else 0.0
    vhi = (zmax + headroom) * math.cos(elev) + r * math.sin(elev)
    ortho = max(2 * r, vhi - vlo) * p.get("margin", 1.05)
    vmid = (vlo + vhi) / 2
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    fwd = Vector((0, math.cos(elev), -math.sin(elev)))
    up = Vector((0, math.sin(elev), math.cos(elev)))
    cam.location = up * vmid - fwd * (r + zmax + 10)
    cam.rotation_euler = (math.pi / 2 - elev, 0, 0)
    cam_data.clip_end = (r + zmax) * 4 + 20
    scene.camera = cam
    add_lights(scene, p)
    supersample = max(1, int(p.get("supersample", 1)))
    res = int(p.get("res", 512)) * supersample
    configure_output(scene, p, res)

    out_dir = p["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    angles = render_angles(int(p.get("count", 8)), False)
    nmat = normal_material(cam) if p.get("normals") else None
    posed = {}

    def setup(name, fi, n, loop, a):
        def go():
            if posed.get("key") != (name, fi):
                apply_pose(arm, *clip_pose(name, fi, n, loop, height))
                posed["key"] = (name, fi)
            pivot.rotation_euler = (0, 0, math.radians(-a))
        return go

    shots, info = [], []
    for clip in clips:
        name, n = clip["name"], max(2, int(clip["frames"]))
        for fi in range(n):
            for a in angles:
                path = os.path.join(out_dir, f"{name}_dir_{round(a):03d}_f{fi:02d}.png")
                shots.append((path, setup(name, fi, n, bool(clip.get("loop", True)), a),
                              f"{name} · {round(a)}° · khung {fi + 1}"))
                info.append({"clip": name, "angle": a, "frame": fi, "file": path})
    frames = [{**rec, "s": s, **({"normal": npath, "normal_s": ns} if npath else {})}
              for rec, (s, npath, ns) in zip(info, render_shots(scene, shots, nmat, emit))]
    return {"total_s": round(time.perf_counter() - t0, 2), "engine": scene.render.engine, "ortho_scale": round(ortho, 4),
            "res": res, "supersample": supersample, "elevation": p.get("elevation", 30), "frames": frames,
            "frames_per_dir": max(int(c["frames"]) for c in clips), "weights": weights, "height": round(height, 4),
            "pivot": [res / 2, res * (0.5 + vmid / ortho)]}


HANDLERS = {
    "ping": lambda p, emit: {"blender": bpy.app.version_string, "pid": os.getpid(), "engines": engine_ids()},
    "warmup": warmup,
    "render_dirs": render_dirs,
    "animate_dirs": animate_dirs,
    "cleanup": cleanup,
    "game_ready": game_ready,
    "script": run_script,
}


def serve(port):
    srv = socket.create_server(("127.0.0.1", port))
    print(f"WORKER_READY {srv.getsockname()[1]}", flush=True)
    conn, _ = srv.accept()
    srv.close()
    f = conn.makefile("rwb")

    def send(obj):
        f.write((json.dumps(obj) + "\n").encode())
        f.flush()

    for line in f:
        req = json.loads(line)
        rid = req.get("id")
        if req.get("cmd") == "quit":
            send({"id": rid, "ok": True, "result": None})
            break
        try:
            result = HANDLERS[req["cmd"]](req.get("params") or {}, lambda ev: send({"id": rid, **ev}))
            resp = {"id": rid, "ok": True, "result": result}
        except ScriptError as e:
            resp = {"id": rid, "ok": False, "code": e.code, "error": str(e), "trace": e.trace}
        except Exception as e:
            resp = {"id": rid, "ok": False, "error": repr(e), "trace": traceback.format_exc()}
        send(resp)
    conn.close()


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    serve(int(argv[0]) if argv else 0)
