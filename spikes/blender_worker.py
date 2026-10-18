"""Resident headless Blender worker: line-delimited JSON over localhost TCP. stdlib + bpy only."""
import json
import math
import socket
import sys
import time
import traceback

import bpy
import numpy as np
from mathutils import Vector


def reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def set_engine(scene, want):
    try:
        scene.render.engine = want
    except TypeError as e:
        raise RuntimeError(f"engine {want} invalid: {e}")


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


def render_dirs(p):
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
    margin = p.get("margin", 1.05)
    ortho = max(2 * r, vhi - vlo) * margin
    vmid = (vlo + vhi) / 2

    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    fwd = Vector((0, math.cos(elev), -math.sin(elev)))
    up = Vector((0, math.sin(elev), math.cos(elev)))
    target = up * vmid
    cam.location = target - fwd * (r + zmax + 10)
    cam.rotation_euler = (math.pi / 2 - elev, 0, 0)
    cam_data.clip_end = (r + zmax) * 4 + 20
    scene.camera = cam

    key = bpy.data.lights.new("key", "SUN")
    key.energy = p.get("sun", 3.0)
    key_o = bpy.data.objects.new("key", key)
    key_o.rotation_euler = (math.radians(50), 0, math.radians(-35))
    scene.collection.objects.link(key_o)
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs[1].default_value = p.get("ambient", 1.0)
    scene.world = world

    set_engine(scene, p.get("engine", "BLENDER_EEVEE"))
    res = p.get("res", 512)
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = p.get("samples", 32)
        scene.cycles.device = "GPU"

    frames = []
    count = p.get("count", 8)
    step = 360 / count
    for i in range(count):
        if p.get("mirror") and 0 < i * step < 180:
            continue
        pivot.rotation_euler = (0, 0, math.radians(-i * step))
        scene.render.filepath = f"{p['out_dir']}/dir_{int(i * step):03d}.png"
        t = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        frames.append({"angle": int(i * step), "file": scene.render.filepath, "s": round(time.perf_counter() - t, 3)})
    return {"import_s": round(t_import, 2), "total_s": round(time.perf_counter() - t0, 2), "engine": scene.render.engine,
            "ortho_scale": round(ortho, 4), "radius": round(r, 4), "height": round(zmax - zmin, 4),
            "verts": int(len(v)), "frames": frames}


HANDLERS = {
    "ping": lambda p: {"blender": bpy.app.version_string},
    "engines": lambda p: [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items],
    "render_dirs": render_dirs,
}


def serve(port):
    srv = socket.create_server(("127.0.0.1", port))
    print(f"WORKER_READY {srv.getsockname()[1]}", flush=True)
    conn, _ = srv.accept()
    f = conn.makefile("rwb")
    for line in f:
        req = json.loads(line)
        if req.get("cmd") == "quit":
            break
        try:
            resp = {"id": req.get("id"), "ok": True, "result": HANDLERS[req["cmd"]](req.get("params") or {})}
        except Exception as e:
            resp = {"id": req.get("id"), "ok": False, "error": repr(e), "trace": traceback.format_exc()}
        f.write((json.dumps(resp) + "\n").encode())
        f.flush()
    conn.close()


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    serve(int(argv[0]) if argv else 0)
