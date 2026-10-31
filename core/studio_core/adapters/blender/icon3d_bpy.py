"""Bodies of blender.icon_render (one ortho icon per mesh, same camera preset / lights / exposure for the whole set) and
blender.iso_render (2:1 isometric prop, 4 quarter turns, pixel scale locked to the iso tile width), imported by the
resident worker's `script` command (worker dir is on sys.path). Writes PNGs + report.json into params["out_dir"]."""
import json
import math
import os
import time

import bpy
import numpy as np
from mathutils import Vector

# Directions the light travels in camera space (x right, y up, -z forward) and sun strength; the key/fill/rim
# energies are the worker's tuned "studio" preset, but tied to the camera so every angle preset lights alike.
CAMERA_LIGHTS = {"key": ((0.55, -0.6, -1.0), 4.0), "fill": ((-0.8, -0.15, -1.0), 1.6), "rim": ((0.15, -0.55, 1.0), 3.0)}
AMBIENT = 1.6
# "top" stops short of 90 so the track-to-up axis stays defined; the model's front (-Y) ends up at the frame bottom.
ANGLES = {"iso34": (45.0, 30.0), "front": (0.0, 8.0), "top": (0.0, 89.9)}
# Ground diamond of an iso tile is exactly 2:1 when sin(elevation) = 0.5.
ISO_ELEVATION = 30.0
ISO_AZIMUTH = 45.0
ISO_DIRS = (("sw", 0), ("se", 90), ("ne", 180), ("nw", 270))


def reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def import_any(path):
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise RuntimeError(f"unsupported mesh format {ext}")
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"no mesh in {os.path.basename(path)}")
    return new, meshes


def world_verts(meshes):
    deps = bpy.context.evaluated_depsgraph_get()
    chunks = []
    for o in meshes:
        me = o.evaluated_get(deps).data
        co = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        m = np.array(o.matrix_world, dtype=np.float64)
        chunks.append(co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])
    return np.concatenate(chunks)


def set_engine(scene):
    try:
        scene.render.engine = "BLENDER_EEVEE"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE_NEXT"


def configure(scene, w, h):
    set_engine(scene)
    scene.render.resolution_x, scene.render.resolution_y = int(w), int(h)
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0


def add_world(scene):
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs[1].default_value = AMBIENT
    scene.world = world


def add_camera(scene, rot):
    data = bpy.data.cameras.new("cam")
    data.type = "ORTHO"
    cam = bpy.data.objects.new("cam", data)
    scene.collection.objects.link(cam)
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = rot
    scene.camera = cam
    for name, (d, energy) in CAMERA_LIGHTS.items():
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        obj = bpy.data.objects.new(name, light)
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = rot @ Vector(d).normalized().to_track_quat("-Z", "Y")
        scene.collection.objects.link(obj)
    return cam


def view_rotation(azimuth, elevation):
    az, el = math.radians(azimuth), math.radians(elevation)
    to_cam = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    return (-to_cam).to_track_quat("-Z", "Y"), to_cam


def toonify(meshes, bands):
    """Cel shading that keeps textures: diffuse light -> Shader to RGB -> constant ramp, times the base colour."""
    done = set()
    for o in meshes:
        for slot in o.material_slots:
            mat = slot.material
            if mat is None or mat.name in done or not mat.use_nodes:
                continue
            done.add(mat.name)
            nodes, links = mat.node_tree.nodes, mat.node_tree.links
            bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
            out = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
            if bsdf is None or out is None:
                continue
            base = bsdf.inputs["Base Color"]
            diffuse = nodes.new("ShaderNodeBsdfDiffuse")
            to_rgb = nodes.new("ShaderNodeShaderToRGB")
            ramp = nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.interpolation = "CONSTANT"
            els = ramp.color_ramp.elements
            for i in range(bands):
                e = els[i] if i < len(els) else els.new(0.0)
                e.position = i / bands
                e.color = (0.45 + 0.55 * i / max(1, bands - 1),) * 3 + (1,)
            mul = nodes.new("ShaderNodeMix")
            mul.data_type = "RGBA"
            mul.blend_type = "MULTIPLY"
            mul.inputs[0].default_value = 1.0
            emit = nodes.new("ShaderNodeEmission")
            # Shader to RGB returns raw irradiance (sun key 4 + fill + world 1.6 peaks near 3.4); scale it into the
            # ramp's 0..1 so the bands fall across the form instead of saturating to the top band.
            scale = nodes.new("ShaderNodeMath")
            scale.operation = "MULTIPLY"
            scale.inputs[1].default_value = 0.3
            bw = nodes.new("ShaderNodeRGBToBW")
            links.new(diffuse.outputs[0], to_rgb.inputs[0])
            links.new(to_rgb.outputs[0], bw.inputs[0])
            links.new(bw.outputs[0], scale.inputs[0])
            links.new(scale.outputs[0], ramp.inputs[0])
            a_in, b_in = mul.inputs[6], mul.inputs[7]
            if base.is_linked:
                links.new(base.links[0].from_socket, a_in)
            else:
                a_in.default_value = base.default_value
            links.new(ramp.outputs[0], b_in)
            links.new(mul.outputs[2], emit.inputs[0])
            alpha = bsdf.inputs.get("Alpha")
            if alpha is not None and (alpha.is_linked or alpha.default_value < 0.999):
                transp = nodes.new("ShaderNodeBsdfTransparent")
                mix = nodes.new("ShaderNodeMixShader")
                if alpha.is_linked:
                    links.new(alpha.links[0].from_socket, mix.inputs[0])
                else:
                    mix.inputs[0].default_value = alpha.default_value
                links.new(transp.outputs[0], mix.inputs[1])
                links.new(emit.outputs[0], mix.inputs[2])
                links.new(mix.outputs[0], out.inputs["Surface"])
            else:
                links.new(emit.outputs[0], out.inputs["Surface"])
    return len(done)


def render(scene, path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def run_icons(params):
    t0 = time.perf_counter()
    out_dir = params["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    azimuth, elevation = ANGLES[params.get("angle", "iso34")]
    res = int(params.get("res", 512))
    fill = float(params.get("fill", 0.86))
    entries = []
    for i, item in enumerate(params["meshes"]):
        reset()
        scene = bpy.context.scene
        _, meshes = import_any(item["path"])
        v = world_verts(meshes)
        rot, to_cam = view_rotation(azimuth, elevation)
        right, up = rot @ Vector((1, 0, 0)), rot @ Vector((0, 1, 0))
        sx, sy = v @ np.array(right), v @ np.array(up)
        cx, cy = (sx.min() + sx.max()) / 2, (sy.min() + sy.max()) / 2
        extent = max(float(sx.max() - sx.min()), float(sy.max() - sy.min()), 1e-6)
        radius = float(np.sqrt(((v - v.mean(0)) ** 2).sum(1)).max()) + float(np.abs(v.mean(0)).max())
        cam = add_camera(scene, rot)
        cam.data.ortho_scale = extent / fill
        cam.data.clip_end = radius * 4 + 20
        cam.location = right * cx + up * cy + to_cam * (radius * 2 + 10)
        add_world(scene)
        configure(scene, res, res)
        toon = toonify(meshes, int(params.get("toon_bands", 3))) if params.get("toon") else 0
        path = os.path.join(out_dir, f"icon_{i:03d}.png")
        t = time.perf_counter()
        render(scene, path)
        entries.append({"name": item["name"], "file": path, "verts": int(len(v)), "toon_materials": toon,
                        "ortho_scale": round(extent / fill, 5), "render_s": round(time.perf_counter() - t, 3)})
        print(f"icon {i + 1}/{len(params['meshes'])} {item['name']}", flush=True)
    report = {"angle": params.get("angle", "iso34"), "azimuth": azimuth, "elevation": elevation, "res": res,
              "fill": fill, "engine": bpy.context.scene.render.engine, "icons": entries,
              "total_s": round(time.perf_counter() - t0, 2)}
    with open(params["report"], "w", encoding="utf-8") as f:
        json.dump(report, f)


def run_iso(params):
    t0 = time.perf_counter()
    out_dir = params["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    tile_w = int(params.get("tile_w", 128))
    cells = int(params.get("cells", 1))
    fill = float(params.get("fill", 0.9))
    ss = max(1, int(params.get("supersample", 2)))
    reset()
    scene = bpy.context.scene
    roots, meshes = import_any(params["mesh"])
    pivot = bpy.data.objects.new("pivot", None)
    scene.collection.objects.link(pivot)
    v = world_verts(meshes)
    lo, hi = v.min(0), v.max(0)
    foot = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]))
    for o in [o for o in roots if o.parent is None]:
        o.location -= foot
        o.parent = pivot
    v = v - np.array(foot)
    # One world unit = one tile; the footprint fits inside cells x cells with a little air.
    span = max(float(hi[0] - lo[0]), float(hi[1] - lo[1]), 1e-6)
    scale = float(params.get("scale") or 0) or cells * fill / span
    pivot.scale = (scale, scale, scale)
    v = v * scale

    el, az = math.radians(ISO_ELEVATION), math.radians(ISO_AZIMUTH)
    view_h = np.array((-math.sin(az), math.cos(az)))
    right_h = np.array((math.cos(az), math.sin(az)))
    k = tile_w / math.sqrt(2)
    top = 0.0
    for _, deg in ISO_DIRS:
        a = math.radians(deg)
        xy = v[:, :2] @ np.array(((math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))))
        up = v[:, 2] * math.cos(el) + (xy @ view_h) * math.sin(el)
        top = max(top, float(up.max()))
        if float(np.abs(xy @ right_h).max()) * k > cells * tile_w / 2 + 0.5:
            raise RuntimeError("prop wider than its footprint after scaling")
    diamond_half_h = cells * tile_w / 4
    top_px = max(math.ceil(top * k) + 2, math.ceil(diamond_half_h))
    canvas_w = cells * tile_w
    canvas_h = int(top_px + diamond_half_h)
    canvas_h += canvas_h % 2
    rot, to_cam = view_rotation(ISO_AZIMUTH, ISO_ELEVATION)
    cam = add_camera(scene, rot)
    cam.data.ortho_scale = max(canvas_w, canvas_h) / k
    up_center = (2 * top_px - canvas_h) / (2 * k)
    reach = float(np.sqrt((v ** 2).sum(1)).max())
    cam.data.clip_end = reach * 4 + 20
    cam.location = (rot @ Vector((0, 1, 0))) * up_center + to_cam * (reach * 2 + 10)
    add_world(scene)
    configure(scene, canvas_w * ss, canvas_h * ss)
    frames = []
    for name, deg in ISO_DIRS:
        pivot.rotation_euler = (0, 0, math.radians(deg))
        path = os.path.join(out_dir, f"iso_{name}.png")
        t = time.perf_counter()
        render(scene, path)
        frames.append({"dir": name, "rotation": deg, "file": path, "render_s": round(time.perf_counter() - t, 3)})
    report = {"tile_w": tile_w, "tile_h": tile_w // 2, "cells": cells, "canvas": [canvas_w, canvas_h],
              "supersample": ss, "px_per_unit": round(k, 5), "scale": round(scale, 6),
              # Pixels from the canvas top-left: centre of the footprint on the ground, and centre of its front cell.
              "foot": [canvas_w / 2, top_px], "anchor": [canvas_w / 2, top_px + (cells - 1) * tile_w / 4],
              "height_px": round(top * k, 2), "elevation": ISO_ELEVATION, "azimuth": ISO_AZIMUTH,
              "engine": scene.render.engine, "frames": frames, "verts": int(len(v)),
              "total_s": round(time.perf_counter() - t0, 2)}
    with open(params["report"], "w", encoding="utf-8") as f:
        json.dump(report, f)


def run(params):
    (run_iso if params.get("mode") == "iso" else run_icons)(params)
