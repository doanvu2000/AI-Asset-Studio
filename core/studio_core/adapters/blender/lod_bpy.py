"""blender.lod body, imported by the resident worker's `script` command (worker dir is on sys.path).

LODs are decimated from the full-detail mesh each (not chained), with the seam / material-border vertices held back so
the atlas UVs survive; the collision shapes and the QA silhouettes read the same LOD0. Writes GLB + FBX per LOD,
collision GLB/FBX, impostor frames and report.json into params["out_dir"].
"""
import importlib
import json
import os
import time

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

import fracture_bpy as fb
import fracture_math as fm
import icon3d_bpy as ib
import lod_math as lm

importlib.reload(lm)

PIN_WEIGHTS = (0.0, 0.3, 1.0)
OVERSHOOT = 1.2
SMOOTH_SHARE = 0.5
UV_EPS = 1e-5


def _tris(o):
    return sum(len(p.vertices) - 2 for p in o.data.polygons)


def _verts(o):
    co = np.empty(len(o.data.vertices) * 3, dtype=np.float64)
    o.data.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def _clone(o, name):
    c = o.copy()
    c.data = o.data.copy()
    c.name = c.data.name = name
    bpy.context.scene.collection.objects.link(c)
    return c


def _drop(o):
    data = o.data
    bpy.data.objects.remove(o, do_unlink=True)
    if data.users == 0:
        bpy.data.meshes.remove(data)


def _is_skinned(new):
    return any(o.type == "ARMATURE" for o in new) or any(
        m.type == "ARMATURE" for o in new if o.type == "MESH" for m in o.modifiers)


def _flatten(meshes, name):
    for o in meshes:
        mw = o.matrix_world.copy()
        if o.data.users > 1:
            o.data = o.data.copy()
        if o.data.shape_keys:
            o.shape_key_clear()
        o.parent = None
        o.data.transform(mw)
        o.matrix_world = Matrix.Identity(4)
    for o in [o for o in bpy.context.scene.objects if o not in meshes]:
        bpy.data.objects.remove(o, do_unlink=True)
    if len(meshes) > 1:
        with fb._override(meshes, meshes[0]):
            bpy.ops.object.join()
    o = meshes[0]
    o.name = o.data.name = name
    return o


def _smooth_share(o):
    n = len(o.data.polygons)
    if not n:
        return 0.0
    flags = np.empty(n, dtype=bool)
    o.data.polygons.foreach_get("use_smooth", flags)
    return float(flags.mean())


def _pinned(o):
    """Vertices on UV island borders, seams, open borders and material borders."""
    bm = fb._bm(o)
    uv = bm.loops.layers.uv.active
    pins = set()
    for e in bm.edges:
        faces = e.link_faces
        hard = e.is_boundary or e.seam or len(faces) != 2 or faces[0].material_index != faces[1].material_index
        if not hard and uv is not None:
            for v in e.verts:
                a = next(l for l in faces[0].loops if l.vert is v)[uv].uv
                b = next(l for l in faces[1].loops if l.vert is v)[uv].uv
                if (a - b).length > UV_EPS:
                    hard = True
                    break
        if hard:
            pins.update(v.index for v in e.verts)
    bm.free()
    return sorted(pins)


def _decimate(o, target, pins, weight):
    if o.vertex_groups:
        o.vertex_groups.clear()
    mod = o.modifiers.new("lod", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = min(1.0, target / max(1, _tris(o)))
    mod.use_collapse_triangulate = True
    if pins and weight < 1.0:
        vg = o.vertex_groups.new(name="lod_keep")
        vg.add(list(range(len(o.data.vertices))), 1.0, "REPLACE")
        vg.add(pins, weight, "REPLACE")
        mod.vertex_group = vg.name
        mod.vertex_group_factor = 1.0
    with fb._override([o], o):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    if o.vertex_groups:
        o.vertex_groups.clear()


def _normals(o, smooth):
    if not smooth:
        return
    fb._smooth(o)
    if hasattr(o.data, "use_auto_smooth"):
        o.data.use_auto_smooth = True
    mod = o.modifiers.new("wn", "WEIGHTED_NORMAL")
    mod.mode = "FACE_AREA"
    mod.keep_sharp = True
    mod.weight = 50
    try:
        with fb._override([o], o):
            bpy.ops.object.modifier_apply(modifier=mod.name)
    except RuntimeError:
        o.modifiers.remove(mod)


def _make_lod(src, target, pins, smooth, name):
    """Lightest pin strength that still reaches the target; the last attempt pins nothing."""
    for weight in PIN_WEIGHTS:
        o = _clone(src, name)
        try:
            with fb._override([o], o):
                bpy.ops.mesh.customdata_custom_splitnormals_clear()
        except RuntimeError:
            pass
        _decimate(o, target, pins, weight)
        if _tris(o) <= target * OVERSHOOT or weight == PIN_WEIGHTS[-1]:
            _normals(o, smooth)
            return o, weight
        _drop(o)


def _box_bm(lo, hi):
    bm = bmesh.new()
    size = np.maximum(np.asarray(hi) - np.asarray(lo), 1e-4)
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(Vector((np.asarray(lo) + np.asarray(hi)) / 2))
                          @ Matrix.Diagonal(Vector((*size, 1.0))))
    return bm


def _hull(points, limit=fm.MAX_HULL_POINTS):
    """(bmesh, points, volume) of the convex hull; capped at `limit` points by rebuilding from a farthest-point
    subset; a flat or collinear cloud falls back to its bounding box."""
    pts = np.asarray(points, dtype=np.float64)
    bm = bmesh.new()
    for p in pts:
        bm.verts.new(p)
    res = bmesh.ops.convex_hull(bm, input=bm.verts[:], use_existing_faces=False)
    bmesh.ops.delete(bm, geom=[g for g in res["geom_interior"] if isinstance(g, bmesh.types.BMVert)], context="VERTS")
    if len(bm.faces) < 4:
        bm.free()
        bm = _box_bm(pts.min(0), pts.max(0))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if len(bm.verts) > limit:
        hull_pts = np.array([v.co[:] for v in bm.verts])
        bm.free()
        return _hull(fm.hull_subset(hull_pts, limit), limit)
    return bm, np.array([v.co[:] for v in bm.verts]), abs(bm.calc_volume(signed=True))


def _bm_object(bm, name):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    return o


def _mesh_volume(o, diag):
    tmp = _clone(o, "volume_probe")
    closed = fb._weld(tmp, diag * 1e-5)
    volume = fb._volume(tmp)
    _drop(tmp)
    return volume, closed


def _collision(lod0, cfg, lo, hi, diag, asset):
    mesh_volume, closed = _mesh_volume(lod0, diag)
    pts = _verts(lod0)
    bm_one, pts_one, vol_one = _hull(pts)
    single_ratio = lm.volume_ratio(vol_one, mesh_volume) if closed else None
    mode = lm.pick_collision(cfg["collision"], single_ratio)
    box = lm.box_from_bounds(lo, hi)
    out = {"mode": mode, "box": box, "mesh_volume": round(mesh_volume, 6), "mesh_closed": closed,
           "single_hull_ratio": single_ratio, "hulls": []}
    objs = []
    if mode == "none":
        bm_one.free()
        return out, objs
    if mode == "hull":
        objs.append(_bm_object(bm_one, f"{asset}_col_00"))
        out["hulls"] = [{"name": objs[0].name, "points": pts_one.round(5).tolist(), "volume": round(vol_one, 6)}]
        total = vol_one
    elif mode == "box":
        bm_one.free()
        objs.append(_bm_object(_box_bm(lo, hi), f"{asset}_col_box"))
        total = float(np.prod(np.asarray(box["size"])))
    else:
        bm_one.free()
        me = lod0.data
        centers = np.empty(len(me.polygons) * 3, dtype=np.float64)
        me.polygons.foreach_get("center", centers)
        labels = lm.cluster_points(centers.reshape(-1, 3), cfg["hulls"], diag / 24)
        groups = {}
        for poly, lab in zip(me.polygons, labels):
            groups.setdefault(int(lab), set()).update(poly.vertices)
        co = _verts(lod0)
        total = 0.0
        for i, idx in enumerate(groups.values()):
            bm, hp, vol = _hull(co[sorted(idx)])
            o = _bm_object(bm, f"{asset}_col_{i:02d}")
            objs.append(o)
            out["hulls"].append({"name": o.name, "points": hp.round(5).tolist(), "volume": round(vol, 6)})
            total += vol
    out["volume"] = round(total, 6)
    out["volume_ratio"] = lm.volume_ratio(total, mesh_volume) if closed else None
    return out, objs


def _camera(scene):
    data = bpy.data.cameras.new("lod_cam")
    data.type = "ORTHO"
    cam = bpy.data.objects.new("lod_cam", data)
    scene.collection.objects.link(cam)
    cam.rotation_mode = "QUATERNION"
    scene.camera = cam
    return cam


def _render(scene, cam, center, ortho, view, path):
    rot, to_cam = ib.view_rotation(*view)
    cam.rotation_quaternion = rot
    cam.location = center + to_cam * ortho * 3
    cam.data.ortho_scale = ortho
    cam.data.clip_start = ortho * 0.01
    cam.data.clip_end = ortho * 10
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def _read_alpha(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)[..., 3] > 0.5


def _show_only(target):
    for o in bpy.context.scene.objects:
        if o.type == "MESH":
            o.hide_render = o not in target


def _setup_render(scene, res, textured):
    ib.configure(scene, res, res)
    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    shading.light = "STUDIO" if textured else "FLAT"
    shading.color_type = "TEXTURE" if textured else "MATERIAL"
    shading.show_cavity = False
    shading.show_shadows = False


def _silhouettes(scene, cam, lods, center, ortho, res, tmp):
    """Mask of every LOD from each QA view; IoU against LOD0."""
    _setup_render(scene, res, False)
    masks = []
    for o in lods:
        _show_only([o])
        views = []
        for view in lm.silhouette_views():
            _render(scene, cam, center, ortho, view, tmp)
            views.append(_read_alpha(tmp))
        masks.append(views)
    os.remove(tmp)
    return [[lm.mask_iou(a, b) for a, b in zip(masks[0], m)] for m in masks]


def _impostor_frames(scene, cam, lod0, center, ortho, cfg, out_dir):
    _setup_render(scene, cfg["impostor_res"], True)
    _show_only([lod0])
    names = []
    for i, view in enumerate(lm.impostor_views()):
        names.append(f"impostor_{i}.png")
        _render(scene, cam, center, ortho, view, os.path.join(out_dir, names[-1]))
    return {"views": lm.IMPOSTOR_VIEWS, "cols": lm.IMPOSTOR_COLS, "res": cfg["impostor_res"],
            "ortho_scale": round(ortho, 5), "center": [round(float(x), 5) for x in center],
            "elevation": lm.impostor_views()[0][1], "frames": names}


def run(p):
    t0 = time.perf_counter()
    cfg = lm.normalize_params(p)
    asset = str(p.get("asset") or "prop")
    out_dir = p["out_dir"]
    scene = bpy.context.scene
    new, meshes = ib.import_any(p["src"])
    if _is_skinned(new):
        raise RuntimeError("skinned mesh (armature) is not supported: decimation would break the skin weights; "
                           "export the static prop or LOD the rig separately")
    warnings = []
    if len(meshes) > 1:
        warnings.append(f"{len(meshes)} mesh objects joined into one (material slots kept)")
    if any(o.data.shape_keys for o in meshes):
        warnings.append("shape keys removed before decimation")
    src = _flatten(meshes, f"{asset}_LOD0")
    fb._name_packed_images()
    co = _verts(src)
    lo, hi = co.min(0), co.max(0)
    diag = float(np.linalg.norm(hi - lo))
    # glTF / FBX import splits vertices along seams and hard normals; unwelded, every such edge reads as a border
    # and gets pinned, so nothing would decimate.
    fb._weld(src, diag * 1e-5)
    source_tris = _tris(src)
    center = Vector((lo + hi) / 2)
    ortho = diag * 1.05
    smooth = _smooth_share(src) >= SMOOTH_SHARE
    pins = _pinned(src)
    plan, skipped = lm.plan_lods(source_tris, cfg)
    pristine = _clone(src, "lod_source")
    pristine.hide_render = True

    lods = [src]
    entries = [{"level": 0, "target": plan[0]["target"], "pin_weight": None}]
    if plan[0]["target"] < source_tris:
        o, w = _make_lod(pristine, plan[0]["target"], pins, smooth, f"{asset}_LOD0")
        bpy.data.objects.remove(src, do_unlink=True)
        o.name = f"{asset}_LOD0"
        lods, entries[0]["pin_weight"] = [o], w
    for step in plan[1:]:
        level = len(lods)
        o, w = _make_lod(pristine, step["target"], pins, smooth, f"{asset}_LOD{level}")
        if not lm.keep_lod(_tris(lods[-1]), _tris(o)):
            skipped.append({"target": step["target"], "reason": f"decimation stopped at {_tris(o)} triangles"})
            _drop(o)
            continue
        lods.append(o)
        entries.append({"level": level, "target": step["target"], "pin_weight": w})
    collision, col_objs = _collision(pristine, cfg, lo, hi, diag, asset)
    _drop(pristine)
    for o in col_objs:
        o.hide_render = True
    cam = _camera(scene)
    ious = _silhouettes(scene, cam, lods, center, ortho, cfg["qa_res"], os.path.join(out_dir, "_qa.png"))
    impostor = _impostor_frames(scene, cam, lods[0], center, ortho, cfg, out_dir) if cfg["impostor"] else None

    for e, o, iou in zip(entries, lods, ious):
        o.hide_render = False
        e.update(tris=_tris(o), glb=f"{asset}_LOD{e['level']}.glb", fbx=f"{asset}_LOD{e['level']}.fbx",
                 iou={"min": min(iou), "views": iou})
        fb._export_glb([o], os.path.join(out_dir, e["glb"]))
        fb._export_fbx([o], os.path.join(out_dir, e["fbx"]))
        e["ratio"] = round(e["tris"] / max(1, source_tris), 4)
    if col_objs:
        fb._export_glb(col_objs, os.path.join(out_dir, f"{asset}_collision.glb"))
        fb._export_fbx(col_objs, os.path.join(out_dir, f"{asset}_collision.fbx"))
    if not collision["mesh_closed"]:
        warnings.append("mesh is not watertight: hull / mesh volume ratio unavailable")

    report = {"asset": asset, "preset": cfg["preset"], "source_tris": source_tris, "lods": entries,
              "skipped": skipped, "collision": collision, "impostor": impostor, "warnings": warnings,
              "bounds_min": [round(float(x), 5) for x in lo], "bounds_max": [round(float(x), 5) for x in hi],
              "size": round(float(max(hi - lo)), 5), "smooth_normals": smooth,
              "timing": {"total_s": round(time.perf_counter() - t0, 2)}}
    with open(p["report"], "w", encoding="utf-8") as f:
        json.dump(report, f)
    for o in [*col_objs, cam, *lods[1:]]:
        bpy.data.objects.remove(o, do_unlink=True)
    print(f"lod {asset}: {[e['tris'] for e in entries]} tris, collision {collision['mode']}")
    return report
