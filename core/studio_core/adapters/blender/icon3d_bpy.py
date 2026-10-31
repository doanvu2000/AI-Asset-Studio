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
from toon_bands import band_levels

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
# Band greys are picked as display (sRGB) values: 0.4 linear showed as 0.66 on screen and the bands melted.
TOON_KEY = 0.9
TOON_FILL = 0.3
TOON_AMBIENT = 0.0
TOON_AO = 0.7
TOON_AO_DISTANCE = 0.08
TOON_RIM_EDGE = 0.72
TOON_RIM = 0.45


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


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _math(nodes, links, op, a, b):
    n = nodes.new("ShaderNodeMath")
    n.operation = op
    for sock, v in zip(n.inputs, (a, b)):
        if isinstance(v, (int, float)):
            sock.default_value = v
        else:
            links.new(v, sock)
    return n.outputs[0]


def _feed(links, src, dst):
    if src.is_linked:
        links.new(src.links[0].from_socket, dst)
    else:
        dst.default_value = src.default_value


def ensure_materials(meshes):
    """Meshes imported without any material get a plain grey Principled one, so toon / AO apply to them too."""
    mat = None
    for o in meshes:
        if any(s.material for s in o.material_slots):
            continue
        if mat is None:
            mat = bpy.data.materials.new("icon_default")
            mat.use_nodes = True
            bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
            bsdf.inputs["Base Color"].default_value = (0.62, 0.62, 0.64, 1.0)
        if o.material_slots:
            for s in o.material_slots:
                s.material = mat
        else:
            o.data.materials.append(mat)
    return mat is not None


def _lambert(nodes, links, n_out, to_light):
    dot = nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    links.new(n_out, dot.inputs[0])
    dot.inputs[1].default_value = to_light
    return _math(nodes, links, "MAXIMUM", dot.outputs["Value"], 0.0)


def toonify(meshes, bands, size, key, fill):
    """Cel shading that keeps textures: N.L of the camera-tied key / fill lights (through the material's normal map,
    so baked TRELLIS detail still breaks the bands) darkened by AO -> constant ramp, times the base colour, plus a
    lit rim. `key` / `fill` are world-space directions towards the lights."""
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
            normal = bsdf.inputs["Normal"]
            n_out = (normal.links[0].from_socket if normal.is_linked
                     else nodes.new("ShaderNodeNewGeometry").outputs["Normal"])
            ao = nodes.new("ShaderNodeAmbientOcclusion")
            ao.inputs["Distance"].default_value = size * TOON_AO_DISTANCE
            rim_w = nodes.new("ShaderNodeLayerWeight")
            rim_w.inputs["Blend"].default_value = 0.5
            links.new(n_out, ao.inputs["Normal"])
            links.new(n_out, rim_w.inputs["Normal"])
            ramp = nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.interpolation = "CONSTANT"
            els = ramp.color_ramp.elements
            # Ramp elements re-sort on every position change, so a held element reference can end up on another band:
            # add them all at 1.0 first, then set positions in ascending index order (keeps the order stable).
            while len(els) < bands:
                els.new(1.0)
            while len(els) > max(1, bands):
                els.remove(els[-1])
            levels = band_levels(bands)
            for i in range(bands):
                e = els[i]
                e.position = i / bands
                e.color = (srgb_to_linear(levels[i]),) * 3 + (1,)
            # Shader to RGB irradiance saturated the ramp (sun 4 + fill 1.6 + world 1.6 never drops below the middle
            # band), so the bands come from N.L directly: independent of light energy, full 0..1 range.
            light = _math(nodes, links, "MULTIPLY_ADD", _lambert(nodes, links, n_out, key), TOON_KEY)
            light.node.inputs[2].default_value = TOON_AMBIENT
            light = _math(nodes, links, "ADD", light,
                          _math(nodes, links, "MULTIPLY", _lambert(nodes, links, n_out, fill), TOON_FILL))
            occl = _math(nodes, links, "MULTIPLY_ADD", ao.outputs["AO"], TOON_AO)
            occl.node.inputs[2].default_value = 1 - TOON_AO
            lit = _math(nodes, links, "MULTIPLY", light, occl)
            links.new(lit, ramp.inputs[0])
            mul = nodes.new("ShaderNodeMix")
            mul.data_type = "RGBA"
            mul.blend_type = "MULTIPLY"
            mul.inputs[0].default_value = 1.0
            _feed(links, base, mul.inputs[6])
            links.new(ramp.outputs[0], mul.inputs[7])
            # Rim only where the surface turns away from the camera AND is in light, so it reads as a back-light edge.
            edge = _math(nodes, links, "GREATER_THAN", rim_w.outputs["Facing"], TOON_RIM_EDGE)
            lit_side = _math(nodes, links, "GREATER_THAN", lit, 0.5)
            rim = _math(nodes, links, "MULTIPLY", _math(nodes, links, "MULTIPLY", edge, lit_side), TOON_RIM)
            screen = nodes.new("ShaderNodeMix")
            screen.data_type = "RGBA"
            screen.blend_type = "SCREEN"
            links.new(rim, screen.inputs[0])
            links.new(mul.outputs[2], screen.inputs[6])
            screen.inputs[7].default_value = (1.0, 0.97, 0.9, 1.0)
            emit = nodes.new("ShaderNodeEmission")
            links.new(screen.outputs[2], emit.inputs[0])
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


def toon_lights(rot):
    return tuple(tuple(rot @ -Vector(CAMERA_LIGHTS[k][0]).normalized()) for k in ("key", "fill"))


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
    entries, failed = [], []
    for i, item in enumerate(params["meshes"]):
        reset()
        scene = bpy.context.scene
        try:
            _, meshes = import_any(item["path"])
            v = world_verts(meshes)
            if not len(v) or not np.isfinite(v).all():
                raise RuntimeError("mesh has no usable vertices")
            if float(np.ptp(v, 0).max()) < 1e-6:
                raise RuntimeError("mesh has zero size")
        except Exception as e:  # one broken file must not sink the whole set
            failed.append({"name": item["name"], "error": " ".join(str(e).split())[:200]})
            print(f"icon {i + 1}/{len(params['meshes'])} {item['name']} FAILED: {e}", flush=True)
            continue
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
        toon = 0
        if params.get("toon"):
            ensure_materials(meshes)
            toon = toonify(meshes, int(params.get("toon_bands", 3)), extent, *toon_lights(rot))
        path = os.path.join(out_dir, f"icon_{i:03d}.png")
        t = time.perf_counter()
        render(scene, path)
        entries.append({"name": item["name"], "file": path, "verts": int(len(v)), "toon_materials": toon,
                        "ortho_scale": round(extent / fill, 5), "render_s": round(time.perf_counter() - t, 3)})
        print(f"icon {i + 1}/{len(params['meshes'])} {item['name']}", flush=True)
    report = {"angle": params.get("angle", "iso34"), "azimuth": azimuth, "elevation": elevation, "res": res,
              "fill": fill, "engine": bpy.context.scene.render.engine, "icons": entries, "failed": failed,
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
