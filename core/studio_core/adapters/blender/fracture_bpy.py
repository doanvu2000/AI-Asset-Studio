"""blender.fracture body, imported by the resident worker's `script` command (worker dir is on sys.path).

Own Voronoi fracture instead of the Cell Fracture extension: the worker runs with --factory-startup (no user
extensions) and must stay offline; each cell is the source mesh clipped by the bisector half-spaces of its seed
(bmesh.bisect_plane + triangle_fill caps), which is what Cell Fracture does for closed meshes.
"""
import importlib
import json
import math
import os
import time

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

import fracture_math as fm

importlib.reload(fm)

_IMAGE_EXT = {"PNG": ".png", "JPEG": ".jpg", "TARGA": ".tga", "BMP": ".bmp", "WEBP": ".webp"}


def _override(objs, active):
    return bpy.context.temp_override(object=active, active_object=active, selected_objects=objs,
                                     selected_editable_objects=objs)


def _op_kwargs(op, kw):
    known = set(op.get_rna_type().properties.keys())
    return {k: v for k, v in kw.items() if k in known}


def _select_only(objs):
    for o in bpy.context.scene.objects:
        o.select_set(o in objs)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def _single_source(name):
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("no mesh in the input GLB")
    for o in meshes:
        mw = o.matrix_world.copy()
        if o.data.users > 1:
            o.data = o.data.copy()
        o.parent = None
        o.data.transform(mw)
        o.matrix_world = Matrix.Identity(4)
    for o in [o for o in scene.objects if o.type != "MESH"]:
        bpy.data.objects.remove(o, do_unlink=True)
    if len(meshes) > 1:
        with _override(meshes, meshes[0]):
            bpy.ops.object.join()
    o = meshes[0]
    o.name = o.data.name = name
    try:
        with _override([o], o):
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
    except RuntimeError:
        pass
    return o


def _bm(o):
    bm = bmesh.new()
    bm.from_mesh(o.data)
    return bm


def _closed(bm):
    """Watertight: no edge borders an odd number of faces. A cut through two parts that touch along a line leaves
    4-face edges there, still a closed solid."""
    return len(bm.edges) > 0 and all(len(e.link_faces) and len(e.link_faces) % 2 == 0 for e in bm.edges)


def _tris(bm):
    return sum(len(f.verts) - 2 for f in bm.faces)


def _volume(o):
    bm = _bm(o)
    v = abs(bm.calc_volume(signed=True))
    bm.free()
    return v


def _diag(bm):
    co = np.array([v.co[:] for v in bm.verts])
    lo, hi = co.min(0), co.max(0)
    return lo, hi, float(np.linalg.norm(hi - lo))


def _write(o, bm):
    bm.to_mesh(o.data)
    o.data.update()


def _weld(o, dist):
    """glTF splits vertices along UV seams, which reads as open edges; welding keeps the per-loop UVs."""
    bm = _bm(o)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    _write(o, bm)
    closed = _closed(bm)
    bm.free()
    return closed


def _voxel(o, diag):
    """Voxel remesh closes holes, fins and self-intersections; it drops UVs, which _transfer_uv brings back."""
    o.data.remesh_voxel_size = diag / 160
    with _override([o], o):
        bpy.ops.object.voxel_remesh()


def _transfer_uv(o, source):
    """UVs from the original surface, after decimation: collapsing an already-transferred mesh drags UVs across
    atlas islands and smears the texture."""
    ref = bpy.data.objects.new("uv_source", source)
    bpy.context.scene.collection.objects.link(ref)
    if source.uv_layers:
        o.data.uv_layers.new(name=source.uv_layers.active.name)
        mod = o.modifiers.new("uv", "DATA_TRANSFER")
        mod.object = ref
        mod.use_loop_data = True
        mod.data_types_loops = {"UV"}
        mod.loop_mapping = "POLYINTERP_NEAREST"
        with _override([o], o):
            bpy.ops.object.modifier_apply(modifier=mod.name)
    o.data.materials.clear()
    for m in source.materials:
        o.data.materials.append(m)
    bpy.data.objects.remove(ref, do_unlink=True)


def _decimate(o, target):
    mod = o.modifiers.new("budget", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = target / max(1, sum(len(p.vertices) - 2 for p in o.data.polygons))
    mod.use_collapse_triangulate = True
    with _override([o], o):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _inside(bvh, p, ray, eps):
    hits, o = 0, p
    for _ in range(64):
        loc, _, _, dist = bvh.ray_cast(o, ray)
        if loc is None:
            break
        hits += 1
        o = loc + ray * eps
    return hits % 2 == 1


def _inside_samples(bm, lo, hi, want, seed):
    """Ray parity along a skewed direction: the nearest-normal test misreads points near concave creases."""
    bvh = BVHTree.FromBMesh(bm)
    rng = np.random.default_rng(seed + 7919)
    ray = Vector((0.5773, 0.5774, 0.5776)).normalized()
    eps = float(np.linalg.norm(hi - lo)) * 1e-6
    out = []
    for _ in range(12):
        for p in rng.uniform(lo, hi, size=(want * 3, 3)):
            if _inside(bvh, Vector(p), ray, eps):
                out.append(p)
        if len(out) >= want:
            break
    return bvh, np.array(out[:want]).reshape(-1, 3)


def _cut_material(name, texture, cfg):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = cfg["roughness"]
    bsdf.inputs["Metallic"].default_value = cfg["metallic"]
    if texture and os.path.isfile(texture):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(texture)
        tex.image.name = f"{name}.png"
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = (0.55, 0.42, 0.28, 1.0)
    return mat


def _style_caps(faces, no, cap_index, uv, tile):
    n = Vector(no)
    u = n.orthogonal().normalized()
    w = n.cross(u)
    for f in faces:
        f.normal_update()
        if f.normal.dot(n) < 0:
            f.normal_flip()
        f.material_index = cap_index
        f.smooth = False
        if uv is not None:
            for loop in f.loops:
                loop[uv].uv = (loop.vert.co.dot(u) / tile, loop.vert.co.dot(w) / tile)


def _cap(bm, no, cap_index, uv, tile):
    """triangle_fill handles nested loops (holes) but gives up on a few degenerate ones; holes_fill closes the rest."""
    edges = [e for e in bm.edges if e.is_boundary]
    if not edges:
        return
    faces = [g for g in bmesh.ops.triangle_fill(bm, use_beauty=True, use_dissolve=False, edges=edges,
                                                normal=no)["geom"] if isinstance(g, bmesh.types.BMFace)]
    rest = [e for e in bm.edges if e.is_boundary]
    if rest:
        ngons = bmesh.ops.holes_fill(bm, edges=rest, sides=0)["faces"]
        faces += bmesh.ops.triangulate(bm, faces=ngons)["faces"] if ngons else []
    _style_caps(faces, no, cap_index, uv, tile)


def _cell(base, seeds, i, scale, cap_index, tile, eps):
    bm = base.copy()
    uv = bm.loops.layers.uv.active
    for j in fm.cut_order(seeds, i, scale):
        co, no = fm.bisector(seeds[i], seeds[j], scale)
        res = bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=eps,
                                     plane_co=co, plane_no=no, clear_outer=True, clear_inner=False)
        if not bm.faces:
            break
        if res["geom_cut"]:
            _cap(bm, no, cap_index, uv, tile)
    return bm


def _solver():
    items = [i.identifier for i in bpy.types.BooleanModifier.bl_rna.properties["solver"].enum_items]
    return next((s for s in ("MANIFOLD", "EXACT") if s in items), items[0])


def _cell_boolean(src, seeds, i, scale, cap_index, tile, lo, hi, cap_mat):
    """Fallback for a cell whose bisect caps did not close (loops touching along the plane): intersect the mesh with
    the convex cell polytope, whose own cut loops are always single convex polygons."""
    bm = bmesh.new()
    size = float(np.linalg.norm(hi - lo)) * 2
    bmesh.ops.create_cube(bm, size=size, matrix=Matrix.Translation(Vector((lo + hi) / 2)))
    uv_name = src.data.uv_layers.active.name if src.data.uv_layers else "UVMap"
    uv = bm.loops.layers.uv.new(uv_name)
    for f in bm.faces:
        f.material_index = cap_index
    for j in fm.cut_order(seeds, i, scale):
        co, no = fm.bisector(seeds[i], seeds[j], scale)
        res = bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=size * 1e-7,
                                     plane_co=co, plane_no=no, clear_outer=True, clear_inner=False)
        if res["geom_cut"]:
            edges = [e for e in bm.edges if e.is_boundary]
            if edges:
                _style_caps(bmesh.ops.holes_fill(bm, edges=edges, sides=0)["faces"], no, cap_index, uv, tile)
    cutter = bpy.data.objects.new("cell", bpy.data.meshes.new("cell"))
    bm.to_mesh(cutter.data)
    bm.free()
    work = src.copy()
    work.data = src.data.copy()
    mats = list(src.data.materials)
    if cap_mat.name not in [m.name for m in mats if m]:
        work.data.materials.append(cap_mat)
        mats.append(cap_mat)
    for m in mats:
        cutter.data.materials.append(m)
    for o in (cutter, work):
        bpy.context.scene.collection.objects.link(o)
    cutter.hide_render = True
    mod = work.modifiers.new("cell", "BOOLEAN")
    mod.operation = "INTERSECT"
    mod.solver = _solver()
    mod.object = cutter
    if hasattr(mod, "material_mode"):
        mod.material_mode = "INDEX"
    with _override([work], work):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    out = _bm(work)
    bmesh.ops.triangulate(out, faces=out.faces[:])
    for o in (work, cutter):
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.meshes.remove(data)
    return out


def _cell_info(cell, cap_index, how):
    return {"volume": abs(cell.calc_volume(signed=True)), "closed": _closed(cell), "tris": _tris(cell),
            "cap_tris": sum(len(f.verts) - 2 for f in cell.faces if f.material_index == cap_index), "method": how}


def _cut_all(src, seeds, scale, cap_index, cfg, eps, lo, hi, cap_mat):
    base = _bm(src)
    bmesh.ops.triangulate(base, faces=base.faces[:])
    if not base.loops.layers.uv:
        base.loops.layers.uv.new("UVMap")
    cells = []
    for i in range(len(seeds)):
        cell = _cell(base, seeds, i, scale, cap_index, cfg["cut_scale"], eps)
        info = _cell_info(cell, cap_index, "bisect")
        if cell.faces and not info["closed"]:
            alt = _cell_boolean(src, seeds, i, scale, cap_index, cfg["cut_scale"], lo, hi, cap_mat)
            alt_info = _cell_info(alt, cap_index, "boolean")
            if alt.faces and alt_info["closed"]:
                cell.free()
                cell, info = alt, alt_info
            else:
                alt.free()
        if not cell.faces:
            cell.free()
            continue
        cells.append((cell, info))
    base.free()
    return cells


def _smooth(o):
    try:
        with _override([o], o):
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(40))
    except (RuntimeError, AttributeError):
        pass


def _hull(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    res = bmesh.ops.convex_hull(bm, input=bm.verts[:], use_existing_faces=False)
    pts = [v.co[:] for v in res["geom"] if isinstance(v, bmesh.types.BMVert)] or [v.co[:] for v in bm.verts]
    bm.free()
    return fm.hull_subset(np.array(pts)).round(5).tolist()


def _name_packed_images():
    for img in bpy.data.images:
        if img.packed_file is None or os.path.splitext(img.filepath)[1]:
            continue
        img.filepath_raw = f"{os.path.splitext(img.name)[0] or 'texture'}{_IMAGE_EXT.get(img.file_format, '.png')}"


def _export_glb(objs, path):
    _select_only(objs)
    gltf = bpy.ops.export_scene.gltf
    gltf(**_op_kwargs(gltf, {"filepath": path, "export_format": "GLB", "use_selection": True, "export_apply": True,
                             "export_yup": True, "export_image_format": "AUTO"}))


def _export_fbx(objs, path):
    # Static props: baking the axis conversion into the vertices gives Unity clean identity rotations on every chunk.
    _select_only(objs)
    fbx = bpy.ops.export_scene.fbx
    fbx(**_op_kwargs(fbx, {"filepath": path, "use_selection": True, "object_types": {"MESH"},
                           "apply_unit_scale": True, "apply_scale_options": "FBX_SCALE_ALL", "axis_forward": "-Z",
                           "axis_up": "Y", "bake_space_transform": True, "path_mode": "COPY",
                           "embed_textures": True, "mesh_smooth_type": "FACE", "add_leaf_bones": False}))


def _needs_remesh(src, cfg, diag):
    closed = _weld(src, diag * 1e-5)
    if cfg["remesh"] == "always" or not closed:
        if cfg["remesh"] == "never":
            raise RuntimeError("mesh is not closed (open or non-manifold edges) and remesh=never")
        return "forced" if closed else "not_closed"
    return None


def _fit_budget(src, prepared, voxel, target, diag):
    """Fresh copy of the closed mesh (voxel remesh if any, else the welded source), decimated to `target`; a collapse
    that opens a non-remeshed surface keeps it undecimated."""
    old = src.data
    src.data = (voxel or prepared).copy()
    bpy.data.meshes.remove(old)
    done = None
    if target is not None:
        _decimate(src, target)
        if _weld(src, diag * 1e-6):
            done = target
        elif voxel is None:
            old = src.data
            src.data = prepared.copy()
            bpy.data.meshes.remove(old)
        else:
            raise RuntimeError("decimating the remeshed surface opened it")
    if voxel is not None:
        _transfer_uv(src, prepared)
    return done


def run(p):
    t0 = time.perf_counter()
    cfg = fm.normalize_params(p)
    asset = str(p.get("asset") or "prop")
    scene = bpy.context.scene
    src = _single_source(f"{asset}_intact")
    source_tris = sum(len(f.vertices) - 2 for f in src.data.polygons)
    bm = _bm(src)
    lo, hi, diag = _diag(bm)
    bm.free()
    remeshed = _needs_remesh(src, cfg, diag)
    if not src.data.materials:
        src.data.materials.append(bpy.data.materials.new(f"{asset}_surface"))
    prepared = src.data.copy()
    voxel = None
    if remeshed:
        _voxel(src, diag)
        if not _weld(src, diag * 1e-6):
            raise RuntimeError("mesh is still not closed after voxel remesh")
        voxel = src.data.copy()
    pre_tris = sum(len(f.vertices) - 2 for f in src.data.polygons)
    target = fm.pre_cut_target(pre_tris, cfg["max_tris"])
    decimated = _fit_budget(src, prepared, voxel, target, diag)

    base = _bm(src)
    bmesh.ops.triangulate(base, faces=base.faces[:])
    lo, hi, diag = _diag(base)
    bvh, inside = _inside_samples(base, lo, hi, 3000, cfg["seed"])
    base.free()
    axis = fm.grain_axis(cfg["grain"], hi - lo)
    scale = fm.metric(cfg["pattern"], axis)
    impact = lo + np.array(cfg["impact"]) * (hi - lo)
    loc, *_ = bvh.find_nearest(Vector(impact))
    if loc is not None:
        impact = np.array(loc[:])
    seeds = fm.place_seeds(inside, cfg["pieces"], cfg["pattern"], cfg["seed"], impact, scale)
    t_prep = time.perf_counter() - t0

    mats = list(src.data.materials)
    cap_mat = _cut_material(f"{asset}_inside", p.get("texture"), cfg)
    cap_index = len(mats)
    attempts = []
    while True:
        if attempts:
            decimated = _fit_budget(src, prepared, voxel, target, diag)
        cells = _cut_all(src, seeds, scale, cap_index, cfg, diag * 1e-6, lo, hi, cap_mat)
        total_volume = _volume(src)
        tris = sum(info["tris"] for _, info in cells)
        caps = sum(info["cap_tris"] for _, info in cells)
        attempts.append({"target": decimated, "tris": tris})
        nxt = fm.next_target(target or pre_tris, tris, caps, cfg["max_tris"])
        if tris <= cfg["max_tris"] * 1.02 or len(attempts) >= 3 or nxt is None:
            break
        for cell, _ in cells:
            cell.free()
        target = nxt
    if not cells:
        raise RuntimeError("fracture produced no pieces")
    t_cut = time.perf_counter() - t0

    chunks = []
    for cell, info in cells:
        me = bpy.data.meshes.new(fm.chunk_name(asset, len(chunks)))
        cell.to_mesh(me)
        cell.free()
        for m in mats:
            me.materials.append(m)
        me.materials.append(cap_mat)
        o = bpy.data.objects.new(me.name, me)
        scene.collection.objects.link(o)
        with _override([o], o):
            bpy.ops.object.origin_set(type="ORIGIN_CENTER_OF_VOLUME")
        _smooth(o)
        info.update(name=o.name, center=[round(x, 5) for x in o.location], hull=_hull(me))
        chunks.append((o, info))
    for m in (prepared, voxel):
        if m is not None:
            bpy.data.meshes.remove(m)

    _smooth(src)
    _name_packed_images()
    objs = [o for o, _ in chunks]
    _export_glb([src], p["out_intact"])
    _export_fbx([src], p["out_intact_fbx"])
    _export_fbx(objs, p["out_fractured_fbx"])
    middle = (lo + hi) / 2
    exploded = []
    for o, info in chunks:
        c = o.copy()
        scene.collection.objects.link(c)
        c.location = Vector(info["center"]) + Vector(fm.exploded_offset(np.array(info["center"]), middle,
                                                                         float((hi - lo)[2]), cfg["explode"]))
        exploded.append(c)
    _export_glb(exploded, p["out_exploded"])
    intact_tris = sum(len(f.vertices) - 2 for f in src.data.polygons)
    intact_hull = _hull(src.data)
    for o in [*exploded, src]:
        bpy.data.objects.remove(o, do_unlink=True)
    _select_only(objs)

    volumes = [info["volume"] for _, info in chunks]
    mass = fm.masses(volumes, cfg["density"])
    report = {
        "asset": asset, "params": cfg, "source_tris": source_tris, "pre_cut_tris": pre_tris,
        "decimated_to": decimated, "budget_attempts": attempts, "remeshed": remeshed, "intact_tris": intact_tris,
        "intact_hull": intact_hull, "total_volume": round(total_volume, 6), "chunks_volume": round(sum(volumes), 6),
        "volume_error": round(fm.volume_error(total_volume, volumes), 5),
        "all_closed": all(info["closed"] for _, info in chunks),
        "cap_tris": sum(info["cap_tris"] for _, info in chunks),
        "tris_total": sum(info["tris"] for _, info in chunks), "grain_axis": "xyz"[axis],
        "impact_point": [round(float(x), 4) for x in impact], "bbox_min": [round(float(x), 4) for x in lo],
        "bbox_max": [round(float(x), 4) for x in hi], "total_mass": round(sum(mass), 4),
        "chunks": [{**info, "volume": round(info["volume"], 6), "mass": m} for (_, info), m in zip(chunks, mass)],
        "timing": {"prep_s": round(t_prep, 2), "cut_s": round(t_cut - t_prep, 2),
                   "total_s": round(time.perf_counter() - t0, 2)}}
    with open(p["report"], "w", encoding="utf-8") as f:
        json.dump(report, f)
    print(f"fracture {asset}: {len(chunks)} pieces, volume error {report['volume_error']:.4f}, "
          f"closed {report['all_closed']}, {report['tris_total']} tris")
    return report
