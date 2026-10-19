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
    frames = []
    for i, a in enumerate(angles):
        pivot.rotation_euler = (0, 0, math.radians(-a))
        scene.render.filepath = os.path.join(out_dir, f"dir_{round(a):03d}.png")
        t = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        frames.append({"angle": a, "file": scene.render.filepath, "s": round(time.perf_counter() - t, 3)})
        emit({"event": "progress", "value": i + 1, "max": len(angles), "label": f"{round(a)}°"})
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


def _distance_weights(meshes, arm):
    """Inverse-distance skinning to the two nearest bone segments. Generated meshes are rarely watertight, which
    makes Blender's bone heat fail outright, and envelopes leave far vertices (capes, shields) unweighted."""
    bones = [b for b in arm.data.bones if b.use_deform]
    heads = np.array([b.head_local for b in bones], dtype=np.float32)
    tails = np.array([b.tail_local for b in bones], dtype=np.float32)
    seg = tails - heads
    seg_len2 = np.maximum((seg ** 2).sum(1), 1e-12)
    inv = np.array(arm.matrix_world.inverted(), dtype=np.float32)
    for o in meshes:
        n = len(o.data.vertices)
        co = np.empty(n * 3, dtype=np.float32)
        o.data.vertices.foreach_get("co", co)
        m = np.array(o.matrix_world, dtype=np.float32)
        pts = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        pts = pts @ inv[:3, :3].T + inv[:3, 3]
        t = np.clip(((pts[:, None, :] - heads[None]) * seg[None]).sum(2) / seg_len2[None], 0.0, 1.0)
        d = np.linalg.norm(pts[:, None, :] - (heads[None] + t[..., None] * seg[None]), axis=2)
        near = np.argsort(d, axis=1)[:, :2]
        dn = np.take_along_axis(d, near, axis=1)
        w = 1.0 / np.maximum(dn, 1e-5) ** 4
        w /= w.sum(1, keepdims=True)
        o.vertex_groups.clear()
        groups = [o.vertex_groups.new(name=b.name) for b in bones]
        for i in range(n):
            for k in range(2):
                if w[i, k] > 0.01:
                    groups[near[i, k]].add([i], float(w[i, k]), "REPLACE")
        # Neighbouring robe/cape vertices can land on opposite legs and tear apart mid-stride; smoothing along the
        # surface turns that seam into a stretch.
        bpy.ops.object.select_all(action="DESELECT")
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        try:
            bpy.ops.object.vertex_group_smooth(group_select_mode="ALL", factor=0.5, repeat=12)
            bpy.ops.object.vertex_group_normalize_all(lock_active=False)
        except RuntimeError as e:
            print(f"weight smooth skipped: {e}", flush=True)


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
    _distance_weights(meshes, arm)
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


def apply_pose(arm, pose, drop):
    """Rotations are about world axes through each bone's rest head, composed down the chain."""
    deltas = {}
    for name, parent, _, _ in RIG_BONES:
        bone = arm.data.bones[name]
        parent_delta = deltas.get(parent, Matrix.Translation((0, 0, drop)))
        axis, deg = pose.get(name, ("X", 0.0))
        head = bone.head_local
        local = Matrix.Translation(head) @ Matrix.Rotation(math.radians(deg), 4, axis) @ Matrix.Translation(-head)
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
    angles = render_angles(int(p.get("count", 8)), False)
    frames = []
    total = frames_n * len(angles)
    for fi in range(frames_n):
        pose, drop = walk_pose(2 * math.pi * fi / frames_n, height)
        apply_pose(arm, pose, drop)
        for a in angles:
            pivot.rotation_euler = (0, 0, math.radians(-a))
            scene.render.filepath = os.path.join(out_dir, f"dir_{round(a):03d}_f{fi:02d}.png")
            bpy.ops.render.render(write_still=True)
            frames.append({"angle": a, "frame": fi, "file": scene.render.filepath})
            emit({"event": "progress", "value": len(frames), "max": total, "label": f"{round(a)}° · khung {fi + 1}"})
    return {"total_s": round(time.perf_counter() - t0, 2), "engine": scene.render.engine, "ortho_scale": round(ortho, 4),
            "res": res, "supersample": supersample, "elevation": p.get("elevation", 30), "frames": frames,
            "frames_per_dir": frames_n, "weights": weights, "height": round(height, 4),
            "pivot": [res / 2, res * (0.5 + vmid / ortho)]}


HANDLERS = {
    "ping": lambda p, emit: {"blender": bpy.app.version_string, "pid": os.getpid(), "engines": engine_ids()},
    "warmup": warmup,
    "render_dirs": render_dirs,
    "animate_dirs": animate_dirs,
    "cleanup": cleanup,
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
