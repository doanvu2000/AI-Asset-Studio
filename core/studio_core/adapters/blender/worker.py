"""Resident headless Blender worker: line-delimited JSON over localhost TCP. stdlib + bpy + bundled numpy only.

Run: blender.exe -b --factory-startup -noaudio -P worker.py -- <port|0>
Request {"id", "cmd", "params"}; zero or more {"id", "event": "progress", ...}; then {"id", "ok", "result"|"error"}.
"""
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


HANDLERS = {
    "ping": lambda p, emit: {"blender": bpy.app.version_string, "pid": os.getpid(), "engines": engine_ids()},
    "warmup": warmup,
    "render_dirs": render_dirs,
    "cleanup": cleanup,
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
        except Exception as e:
            resp = {"id": rid, "ok": False, "error": repr(e), "trace": traceback.format_exc()}
        send(resp)
    conn.close()


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    serve(int(argv[0]) if argv else 0)
