"""blender.modular_kit body, imported by the resident worker's `script` command (worker dir is on sys.path).

Every piece is built from closed bmesh shells (boxes around openings, an extruded stair profile), trims are
bevelled along their length only so the flat ends of two neighbours meet without a notch, and each face is
UV-mapped into the trim sheet strip of its part (trim_math). Leaves the kit lined up in the scene for the worker's
GLB export and writes per-piece GLB / FBX / OBJ, a sample-room GLB and report.json."""
import importlib
import json
import os
import time

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

import modular_math as mm
import trim_math as tm

importlib.reload(mm)
importlib.reload(tm)

PARTS = ("", "wall", "floor", "pillar", "cap", "base", "frame", "step", "rail", "metal", "dark")
LONG = {"cap", "base", "frame", "step", "rail", "metal", "dark"}


def _op_kwargs(op, kw):
    known = set(op.get_rna_type().properties.keys())
    return {k: v for k, v in kw.items() if k in known}


def _select_only(objs):
    for o in bpy.context.scene.objects:
        o.select_set(o in objs)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


class Shape:
    """bmesh under construction: faces carry a part index, `bevel` collects the edges to bevel."""

    def __init__(self):
        self.bm = bmesh.new()
        self.part = self.bm.faces.layers.int.new("part")
        self.bevel = []

    def face(self, vs, part):
        f = self.bm.faces.new(vs)
        f[self.part] = PARTS.index(part)
        return f

    def box(self, lo, hi, part, faces=None, bevel_axes=()):
        """Axis-aligned closed box; `faces` overrides the part per side ('+z', '-z', 'side'...)."""
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        if min(x1 - x0, y1 - y0, z1 - z0) <= 1e-6:
            return
        v = {(i, j, k): self.bm.verts.new(((x0, x1)[i], (y0, y1)[j], (z0, z1)[k]))
             for i in (0, 1) for j in (0, 1) for k in (0, 1)}
        faces = faces or {}
        sides = {"-x": [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)], "+x": [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)],
                 "-y": [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)], "+y": [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)],
                 "-z": [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)], "+z": [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]}
        for key, corners in sides.items():
            p = faces.get(key) or (faces.get("side") if key[1] != "z" else None) or part
            self.face([v[c] for c in corners], p)
        for axis in bevel_axes:
            for a in (0, 1):
                for b in (0, 1):
                    ends = []
                    for t in (0, 1):
                        idx = [0, 0, 0]
                        others = [i for i in range(3) if i != axis]
                        idx[axis], idx[others[0]], idx[others[1]] = t, a, b
                        ends.append(v[tuple(idx)])
                    self.bevel.append(ends)

    def prism_x(self, profile, x0, x1, parts):
        """Profile (y, z) polygon extruded along X; `parts(i)` names side face i (edge i -> i+1), caps are 'wall'."""
        a = [self.bm.verts.new((x0, y, z)) for y, z in profile]
        b = [self.bm.verts.new((x1, y, z)) for y, z in profile]
        self.face(a, "wall")
        self.face(b, "wall")
        n = len(profile)
        for i in range(n):
            j = (i + 1) % n
            self.face([a[i], a[j], b[j], b[i]], parts(i))

    def finish(self, bevel_w):
        bm = self.bm
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        if bevel_w > 0 and self.bevel:
            edges = []
            for va, vb in self.bevel:
                e = bm.edges.get((va, vb))
                if e is not None and len(e.link_faces) == 2:
                    edges.append(e)
            if edges:
                bmesh.ops.bevel(bm, geom=edges, offset=bevel_w, offset_type="OFFSET", segments=1, profile=0.5,
                                affect="EDGES", clamp_overlap=True)
        for f in bm.faces:
            if f[self.part] == 0:
                near = [g[self.part] for e in f.edges for g in e.link_faces if g is not f and g[self.part]]
                f[self.part] = max(set(near), key=near.count) if near else PARTS.index("wall")
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4], quad_method="BEAUTY",
                              ngon_method="EAR_CLIP")
        bm.normal_update()


def wall_like(s, d, kind):
    g, h, t, c = d["grid"], d["storey"], d["thickness"], d["inset"]
    bh, ch, fw = d["base_h"], d["cap_h"], d["frame"]
    holes, boxes = [], []
    base_runs = [(0.0, g)]
    if kind == "wall_window":
        ww, wh, sill = d["window"]
        x0, x1 = (g - ww) / 2 - fw, (g + ww) / 2 + fw
        z0, z1 = sill - fw, sill + wh + fw
        holes.append((x0, x1, z0, z1))
        s.box((x0, 0, z0), (x0 + fw, t, z1), "frame", bevel_axes=(2,))
        s.box((x1 - fw, 0, z0), (x1, t, z1), "frame", bevel_axes=(2,))
        s.box((x0 + fw, 0, z1 - fw), (x1 - fw, t, z1), "frame", bevel_axes=(0,))
        s.box((x0 + fw, 0, z0), (x1 - fw, t, z0 + fw), "step", bevel_axes=(0,))
        boxes += [((0, 0, 0), (x0 + fw, t, h)), ((x1 - fw, 0, 0), (g, t, h)), ((x0 + fw, 0, 0), (x1 - fw, t, z0 + fw)),
                  ((x0 + fw, 0, z1 - fw), (x1 - fw, t, h))]
    elif kind == "wall_door":
        dw, dh = d["door"]
        x0, x1, z1 = (g - dw) / 2 - fw, (g + dw) / 2 + fw, dh + fw
        holes.append((x0, x1, 0.0, z1))
        base_runs = [(0.0, x0), (x1, g)]
        s.box((x0, 0, 0), (x0 + fw, t, z1), "frame", bevel_axes=(2,))
        s.box((x1 - fw, 0, 0), (x1, t, z1), "frame", bevel_axes=(2,))
        s.box((x0 + fw, 0, z1 - fw), (x1 - fw, t, z1), "frame", bevel_axes=(0,))
        boxes += [((0, 0, 0), (x0 + fw, t, h)), ((x1 - fw, 0, 0), (g, t, h)), ((x0 + fw, 0, z1 - fw), (x1 - fw, t, h))]
    else:
        boxes.append(((0, 0, 0), (g, t, h)))
    # Solid columns left and right of the opening keep both wall ends a plain 4-corner profile, identical to the
    # straight wall's, so any two wall pieces placed end to end share their boundary vertices.
    if holes:
        x0, x1, z0, z1 = holes[0]
        s.box((0, c, bh), (x0, t - c, h - ch), "wall")
        s.box((x1, c, bh), (g, t - c, h - ch), "wall")
        if z0 > bh:
            s.box((x0, c, bh), (x1, t - c, z0), "wall")
        s.box((x0, c, max(z1, bh)), (x1, t - c, h - ch), "wall")
    else:
        s.box((0, c, bh), (g, t - c, h - ch), "wall")
    for a, e in base_runs:
        s.box((a, 0, 0), (e, t, bh), "base", bevel_axes=(0,))
    s.box((0, 0, h - ch), (g, t, h), "cap", bevel_axes=(0,))
    return boxes


def corner(s, d, outer):
    """L of two legs (along +X and +Y) meeting at the pivot; the outer corner gets a full-height post. Leg B starts
    where leg A (or the post) ends, so the bodies only touch and no two faces are coplanar."""
    g, h, t, c = d["grid"], d["storey"], d["thickness"], d["inset"]
    bh, ch = d["base_h"], d["cap_h"]
    start = t if outer else 0.0
    s.box((start or c, c, bh), (g, t - c, h - ch), "wall")
    s.box((c, t if outer else t - c, bh), (t - c, g, h - ch), "wall")
    s.box((start, 0, 0), (g, t, bh), "base", bevel_axes=(0,))
    s.box((start, 0, h - ch), (g, t, h), "cap", bevel_axes=(0,))
    s.box((0, t, 0), (t, g, bh), "base", bevel_axes=(1,))
    s.box((0, t, h - ch), (t, g, h), "cap", bevel_axes=(1,))
    if outer:
        s.box((0, 0, 0), (t, t, h), "pillar", faces={"+z": "cap", "-z": "dark"}, bevel_axes=(2,))
    return [((0, 0, 0), (g, t, h)), ((0, t, 0), (t, g, h))]


def build(piece, d, bevel):
    s = Shape()
    g, h = d["grid"], d["storey"]
    if piece in ("wall", "wall_window", "wall_door"):
        boxes = wall_like(s, d, piece)
    elif piece in ("corner_out", "corner_in"):
        boxes = corner(s, d, piece == "corner_out")
    elif piece in ("floor", "ceiling"):
        z0 = 0.0 if piece == "floor" else h - d["floor_t"]
        lo, hi = (0, 0, z0), (g, g, z0 + d["floor_t"])
        s.box(lo, hi, "floor", faces={"side": "dark"})
        boxes = [(lo, hi)]
    elif piece == "stair":
        n, depth = d["steps"], g * d["stair_cells"]
        r, run = h / n, depth / n
        prof = [(0.0, 0.0)]
        for k in range(n):
            prof += [(k * run, (k + 1) * r), ((k + 1) * run, (k + 1) * r)]
        prof.append((depth, 0.0))
        m = len(prof)
        s.prism_x(prof, 0.0, g, lambda i: "dark" if i == m - 1 else ("wall" if i == m - 2 else "step"))
        boxes = [((0, k * run, 0), (g, (k + 1) * run, (k + 1) * r)) for k in range(n)]
    elif piece == "pillar":
        p = d["pillar"]
        bh = min(0.3, 0.08 * h)
        inset = 0.12 * p
        s.box((0, 0, 0), (p, p, bh), "cap", bevel_axes=(0, 1))
        s.box((0, 0, h - bh), (p, p, h), "cap", bevel_axes=(0, 1))
        s.box((inset, inset, bh), (p - inset, p - inset, h - bh), "pillar", bevel_axes=(2,))
        boxes = [((0, 0, 0), (p, p, h))]
    elif piece == "railing":
        rw, rh = d["rail_w"], d["rail_h"]
        pw, top, low = 0.1, 0.06, 0.04
        s.box((0, 0, 0), (pw / 2, rw, rh - top), "rail", bevel_axes=(2,))
        s.box((g - pw / 2, 0, 0), (g, rw, rh - top), "rail", bevel_axes=(2,))
        s.box((0, 0, rh - top), (g, rw, rh), "rail", bevel_axes=(0,))
        s.box((pw / 2, 0.015, 0.1), (g - pw / 2, rw - 0.015, 0.1 + low), "metal", bevel_axes=(0,))
        nb = max(1, round(g / 0.15) - 1)
        bw = 0.03
        for i in range(1, nb + 1):
            x = i * g / (nb + 1)
            s.box((x - bw / 2, (rw - bw) / 2, 0.1 + low), (x + bw / 2, (rw + bw) / 2, rh - top), "metal")
        boxes = [((0, 0, 0), (g, rw, rh))]
    else:
        raise ValueError(f"unknown piece {piece}")
    s.finish(bevel)
    return s, [[list(map(float, lo)), list(map(float, hi))] for lo, hi in boxes]


def uv_spec(part, d):
    g, h = d["grid"], d["storey"]
    if part == "wall":
        return {"vertical": (0.0, h), "horizontal": None}
    if part == "floor":
        return {"vertical": None, "horizontal": (0.0, g)}
    if part == "pillar":
        return {"vertical": (0.0, h), "horizontal": None}
    return {"vertical": None, "horizontal": None}


def map_uv(s, trim, d):
    """-> faces whose UVs fell outside their strip (should be none)."""
    bm = s.bm
    uv = bm.loops.layers.uv.verify()
    bad = 0
    for f in bm.faces:
        part = PARTS[f[s.part]]
        strip = tm.resolve_strip(trim, part)
        spec = uv_spec(part, d)
        vertical = abs(f.normal.z) < 0.7
        span = spec["vertical"] if vertical else spec["horizontal"]
        pts = [lp.vert.co[:] for lp in f.loops]
        uvs = tm.face_uv(pts, f.normal[:], strip, span=span, along_long=part in LONG and span is None,
                         cell_m=d["grid"])
        for lp, (u, v) in zip(f.loops, uvs):
            lp[uv].uv = (float(u), float(v))
        if not tm.uv_in_strip(uvs, strip):
            bad += 1
    return bad


def apply_strip(obj, face_indices, strip_name, trim, cell_m=2.0, along_long=True):
    """Helper for any mesh: maps the given faces of `obj` into the named strip of a trim.json doc."""
    strip = next(s for s in trim["strips"] if s["name"] == strip_name)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    uv = bm.loops.layers.uv.verify()
    for i in face_indices:
        f = bm.faces[i]
        uvs = tm.face_uv([lp.vert.co[:] for lp in f.loops], f.normal[:], strip, along_long=along_long, cell_m=cell_m)
        for lp, (u, v) in zip(f.loops, uvs):
            lp[uv].uv = (float(u), float(v))
    bm.to_mesh(obj.data)
    bm.free()


def material(name, maps):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    albedo = maps.get("albedo")
    if albedo and os.path.isfile(albedo):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(albedo)
        tex.image.name = f"{name}_albedo.png"
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = (0.62, 0.6, 0.56, 1.0)
    rough = maps.get("roughness")
    if rough and os.path.isfile(rough):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(rough)
        tex.image.colorspace_settings.name = "Non-Color"
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = 0.8
    normal = maps.get("normal")
    if normal and os.path.isfile(normal):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(normal)
        tex.image.colorspace_settings.name = "Non-Color"
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(tex.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def _export_glb(objs, path):
    _select_only(objs)
    gltf = bpy.ops.export_scene.gltf
    gltf(**_op_kwargs(gltf, {"filepath": path, "export_format": "GLB", "use_selection": True, "export_apply": True,
                             "export_yup": True, "export_image_format": "JPEG", "export_jpeg_quality": 90}))


def _export_fbx(objs, path):
    # Baking the axis conversion into the vertices gives Unity identity rotations; the material is assigned from
    # the exported PBR .mat, so no texture travels inside the FBX.
    _select_only(objs)
    fbx = bpy.ops.export_scene.fbx
    fbx(**_op_kwargs(fbx, {"filepath": path, "use_selection": True, "object_types": {"MESH"},
                           "apply_unit_scale": True, "apply_scale_options": "FBX_SCALE_ALL", "axis_forward": "-Z",
                           "axis_up": "Y", "bake_space_transform": True, "path_mode": "STRIP",
                           "embed_textures": False, "mesh_smooth_type": "FACE", "add_leaf_bones": False}))


def _export_obj(objs, path):
    _select_only(objs)
    obj = bpy.ops.wm.obj_export
    obj(**_op_kwargs(obj, {"filepath": path, "export_selected_objects": True, "export_materials": False,
                           "export_uv": True, "export_normals": True, "export_triangulated_mesh": True,
                           "forward_axis": "NEGATIVE_Z", "up_axis": "Y", "apply_modifiers": True}))


def _place(src, name, pos, rot, coll):
    o = bpy.data.objects.new(name, src.data)
    coll.objects.link(o)
    o.matrix_world = Matrix.Translation(pos) @ Matrix.Rotation(np.radians(rot), 4, "Z")
    return o


def run(p):
    t0 = time.perf_counter()
    cfg = mm.normalize_params(p)
    d = mm.kit_dims(cfg["grid"], cfg["storey"])
    asset = str(p.get("asset") or "kit")
    out_dir = p["out_dir"]
    trim = None
    if p.get("trim") and os.path.isfile(p["trim"]):
        with open(p["trim"], encoding="utf-8") as fh:
            trim = json.load(fh)
    if trim is None:
        raise RuntimeError("trim layout missing (params.trim)")
    scene = bpy.context.scene
    for o in list(scene.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    mat = material(f"{asset}_trim", p.get("maps") or {})
    pieces, report = {}, []
    for piece in cfg["pieces"]:
        s, boxes = build(piece, d, cfg["bevel"])
        bad_uv = map_uv(s, trim, d)
        parts = sorted({PARTS[f[s.part]] for f in s.bm.faces})
        me = bpy.data.meshes.new(f"{asset}_{piece}")
        s.bm.to_mesh(me)
        s.bm.free()
        me.materials.append(mat)
        o = bpy.data.objects.new(f"{asset}_{piece}", me)
        scene.collection.objects.link(o)
        pieces[piece] = o
        verts = np.array([v.co[:] for v in me.vertices])
        lo, hi = verts.min(0).tolist(), verts.max(0).tolist()
        tris = sum(len(f.vertices) - 2 for f in me.polygons)
        entry = {"piece": piece, "name": o.name, "tris": tris, "verts": len(me.vertices),
                 "bounds_min": [round(v, 5) for v in lo], "bounds_max": [round(v, 5) for v in hi],
                 "bounds": mm.check_bounds(lo, hi, piece, d), "uv_outside": bad_uv, "colliders": boxes,
                 "parts": parts}
        if piece in ("wall", "wall_window", "wall_door", "railing", "floor", "ceiling", "stair"):
            entry["ends_x"] = mm.ends_match(verts.tolist(), d["grid"], 0)
        if piece in ("floor", "ceiling"):
            entry["ends_y"] = mm.ends_match(verts.tolist(), d["grid"], 1)
        report.append(entry)
        base = os.path.join(out_dir, f"{asset}_{piece}")
        _export_glb([o], base + ".glb")
        _export_fbx([o], base + ".fbx")
        _export_obj([o], base + ".obj")
    if "wall" in pieces:
        ref = mm.end_profiles(np.array([v.co[:] for v in pieces["wall"].data.vertices]).tolist(), d["grid"])[0]
        for e in report:
            if e["piece"] in ("wall", "wall_window", "wall_door", "corner_out", "corner_in"):
                verts = [v.co[:] for v in pieces[e["piece"]].data.vertices]
                e["joins_wall"] = mm.end_profiles(verts, d["grid"])[1] == ref
    room = mm.sample_room(d, cfg["pieces"])
    room_coll = bpy.data.collections.new("room")
    scene.collection.children.link(room_coll)
    placed = [_place(pieces[r["piece"]], f"{r['piece']}_{i:02d}", r["pos"], r["rot"], room_coll)
              for i, r in enumerate(room)]
    _export_glb(placed, os.path.join(out_dir, f"{asset}_room.glb"))
    for o in placed:
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.collections.remove(room_coll)
    x = 0.0
    for piece in cfg["pieces"]:
        o = pieces[piece]
        o.location = (x, 0.0, 0.0)
        x += (o.dimensions.x if piece != "stair" else d["grid"]) + d["grid"] * 0.5
    doc = {"asset": asset, "dims": d, "params": cfg, "pieces": report, "room": room,
           "max_tris": max(e["tris"] for e in report), "tris_budget": cfg["max_tris"],
           "all_bounds_ok": all(e["bounds"]["ok"] for e in report),
           "all_ends_ok": all(e[k]["ok"] for e in report for k in ("ends_x", "ends_y") if k in e)
           and all(e.get("joins_wall", True) for e in report),
           "uv_outside": sum(e["uv_outside"] for e in report),
           "seconds": round(time.perf_counter() - t0, 2)}
    with open(p["report"], "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    print(f"modular kit {asset}: {len(report)} pieces, max tris {doc['max_tris']}")
