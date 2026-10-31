"""Facial expressions for an existing Spine skeleton (recipe spine-face): the head attachment is upscaled for Flux.2
Klein, Claude boxes the eyes / brows / mouth, every expression is an inpaint of only that feature's hole, and each
feature becomes its own slot (`eyes`, `brow`, `mouth`) above the head with one region attachment per expression,
cut back to the head's texel grid so the neutral one is the head's own pixels. Adds `blink`, `talk` and emotion
animations meant for a second track. Only `slots`, `skins` and `animations` of the file are rewritten."""
from __future__ import annotations

import asyncio
import json
import math
import random
import re
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..contracts import Outputs, StepContext, StepError, StepResult
from . import spine_runtime as rt
from .executors import OpExecutor, _images_in, _put_png
from .rig import _put_json, _read_json
from .spine_anim import _member_spans
from .spine_physics import _tokens

HEAD_WORDS = ("head", "face", "dau", "đầu", "mặt")
GROUPS = ("eyes", "brow", "mouth")
NEUTRAL = {"eyes": "eyes_open", "brow": "brow_neutral", "mouth": "mouth_neutral"}
BASE = ("eyes_half", "eyes_closed", "mouth_a", "mouth_o", "mouth_m")
EMOTIONS: dict[str, dict[str, str]] = {
    "happy": {"eyes": "eyes_happy", "mouth": "mouth_smile"},
    "angry": {"brow": "brow_angry", "mouth": "mouth_angry"},
    "sad": {"brow": "brow_sad", "mouth": "mouth_sad", "eyes": "eyes_half"},
}
WHAT: dict[str, str] = {
    "eyes_half": "the same eyes half closed, sleepy: the upper eyelids lowered over the top half of each iris",
    "eyes_closed": "both eyes fully closed: each eye a single gently curved eyelid line with lashes, no iris and "
                   "no eye white visible",
    "eyes_happy": "both eyes closed in a happy smile: each eye an upward curved arc like ^ ^, no iris visible",
    "mouth_a": "the mouth wide open as if saying 'ah': dark open mouth with a hint of tongue",
    "mouth_o": "a small round open mouth as if saying 'oh'",
    "mouth_m": "the mouth closed, lips pressed together in a short straight line as if saying 'mm'",
    "mouth_smile": "a big happy open smile showing the upper teeth",
    "mouth_angry": "an angry mouth: clenched teeth, the corners pulled down",
    "mouth_sad": "a sad frown, the mouth corners turned down",
    "brow_angry": "both eyebrows angled sharply down toward the nose in an angry frown",
    "brow_sad": "both eyebrows raised at their inner ends, a worried sad look",
}
PROMPT = ("Cartoon character face, front view. In the masked area draw {what}. Match the exact flat 2D cel-shaded "
          "art style, outline thickness, skin color and shading of the rest of the face; the rest of the head stays "
          "unchanged.")
# inpaint_flux2_klein4b grows its mask by 4 px (GrowMask) before compositing: the edit can differ that far out.
GROW = 4
FEATHER = 3.0
WORK = 768


def parse_emotions(raw: Any) -> list[str]:
    s = str(raw or "").strip().lower()
    if s in ("", "none"):
        return []
    if s in ("all", "auto"):
        return list(EMOTIONS)
    out = [e.strip() for e in re.split(r"[,\s]+", s) if e.strip()]
    bad = [e for e in out if e not in EMOTIONS]
    if bad:
        raise StepError("op_bad_param", f"unknown emotion {bad[0]!r}; use {', '.join(EMOTIONS)}")
    return out


def wanted_variants(emotions: list[str], groups: set[str]) -> list[str]:
    names = list(BASE)
    for e in emotions:
        for v in EMOTIONS[e].values():
            if v not in names:
                names.append(v)
    return [v for v in names if v.split("_")[0] in groups]


def find_head(doc: dict[str, Any], skins: list[str], want: str = "auto") -> tuple[dict[str, Any], str, dict[str, Any], str]:
    """(slot, attachment name, attachment, skin holding it): the largest visible region / mesh on a slot whose slot,
    bone or attachment name says head / face, or the slot named by `want`."""
    want = (want or "auto").strip().lower()
    by_skin = {s["name"]: s for s in doc.get("skins") or []}
    order = ["default", *skins]
    best, best_area = None, 0.0
    for slot in doc.get("slots") or []:
        att_name = slot.get("attachment")
        if not att_name:
            continue
        names = (slot["name"], slot.get("bone", ""), att_name)
        if want != "auto":
            if slot["name"].lower() != want and want not in slot["name"].lower():
                continue
        elif not any(t in HEAD_WORDS for n in names for t in _tokens(n)):
            continue
        for skin in reversed(order):
            att = ((by_skin.get(skin) or {}).get("attachments") or {}).get(slot["name"], {}).get(att_name)
            if att is None:
                continue
            if att.get("type", "region") in ("region", "mesh"):
                area = float(att.get("width", 0)) * float(att.get("height", 0))
                if area > best_area:
                    best, best_area = (slot, att_name, att, skin), area
            break
    if best is None:
        raise StepError("op_no_head", "no visible head / face region or mesh attachment found by slot or bone name; "
                        "name it in 'head'")
    return best


def texel_affine(att: dict[str, Any], doc: dict[str, Any], bone: str) -> tuple[np.ndarray, np.ndarray, float]:
    """(A, b, residual): bone-local = A @ texel + b for the attachment's image (x right, y down, original pixels),
    and how far (texels) a mesh strays from one affine map; region attachments are exact."""
    w, h = float(att["width"]), float(att["height"])
    if att.get("type", "region") == "region":
        r = math.radians(float(att.get("rotation", 0.0)))
        rs = np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]]) @ np.diag(
            [float(att.get("scaleX", 1.0)), float(att.get("scaleY", 1.0))])
        a = rs @ np.diag([1.0, -1.0])
        b = rs @ np.array([-w / 2, h / 2]) + (float(att.get("x", 0.0)), float(att.get("y", 0.0)))
        return a, b, 0.0
    pose = rt.Pose(doc)
    world = rt.mesh_world(att, pose, bone)
    m, o = pose.world[bone]
    local = np.linalg.solve(m, (world - o).T).T
    tex = np.array(att["uvs"], np.float64).reshape(-1, 2) * (w, h)
    design = np.c_[tex, np.ones(len(tex))]
    sol, *_ = np.linalg.lstsq(design, local, rcond=None)
    a, b = sol[:2].T, sol[2]
    fit = design @ sol
    per_texel = math.sqrt(abs(np.linalg.det(a))) or 1.0
    return a, b, float(np.abs(fit - local).max() / per_texel)


def region_from_box(a: np.ndarray, b: np.ndarray, box: tuple[int, int, int, int]) -> dict[str, Any]:
    """Region attachment (bone-local) that shows texel box (x0, y0, x1, y1) of the head image where the head drew it."""
    x0, y0, x1, y1 = box
    c = a @ np.array([(x0 + x1) / 2, (y0 + y1) / 2]) + b
    m = a @ np.diag([1.0, -1.0])
    rot = math.atan2(m[1, 0], m[0, 0])
    sx = float(np.linalg.norm(m[:, 0]))
    sy = float(m[:, 1] @ np.array([-math.sin(rot), math.cos(rot)]))
    att: dict[str, Any] = {"x": round(float(c[0]), 4), "y": round(float(c[1]), 4),
                           "rotation": round(math.degrees(rot), 4), "width": x1 - x0, "height": y1 - y0}
    if abs(sx - 1) > 1e-6:
        att["scaleX"] = round(sx, 6)
    if abs(sy - 1) > 1e-6:
        att["scaleY"] = round(sy, 6)
    return att


def work_size(w: int, h: int, target: int = WORK) -> tuple[float, tuple[int, int]]:
    """Upscale factor and canvas size (multiple of 16, as Flux latents want) for the head at about `target` px."""
    s = max(1.0, target / max(w, h))
    return s, (int(math.ceil(w * s / 16) * 16), int(math.ceil(h * s / 16) * 16))


def head_on_white(head: Image.Image, s: float, canvas: tuple[int, int]) -> Image.Image:
    big = head.resize((round(head.width * s), round(head.height * s)), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", canvas, (255, 255, 255, 255))
    out.alpha_composite(big)
    return out


def grid_overlay(img: Image.Image, step: int = 64) -> Image.Image:
    out = img.convert("RGB")
    d = ImageDraw.Draw(out)
    for x in range(0, out.width, step):
        d.line((x, 0, x, out.height), fill=(255, 60, 60), width=1)
        d.text((x + 2, 2), str(x), fill=(200, 0, 0))
    for y in range(0, out.height, step):
        d.line((0, y, out.width, y), fill=(60, 60, 255), width=1)
        d.text((2, y + 2), str(y), fill=(0, 0, 200))
    return out


def _box(raw: Any, size: tuple[int, int]) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    x0, y0, x1, y1 = (float(v) for v in raw)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(size[0], x1), min(size[1], y1)
    return (x0, y0, x1, y1) if x1 - x0 >= 2 and y1 - y0 >= 2 else None


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return m
    img = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * r + 1))
    return np.asarray(img) > 127


def _rect(mask: np.ndarray, box: tuple[float, float, float, float]) -> None:
    """Rounded box: Klein traced the straight edges of a square hole as a faint frame (glasses on a real run)."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    if x1 <= x0 or y1 <= y0:
        return
    img = Image.new("L", (mask.shape[1], mask.shape[0]), 0)
    ImageDraw.Draw(img).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=0.35 * min(x1 - x0, y1 - y0), fill=255)
    mask |= np.asarray(img) > 127


def _pad(box: tuple[float, float, float, float], fx: float, fy: float, px: float = 0) -> tuple[float, ...]:
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * fx + px, (y1 - y0) * fy + px
    return x0 - dx, y0 - dy, x1 + dx, y1 + dy


def refine(box: tuple[float, float, float, float], ink: np.ndarray) -> tuple[float, float, float, float]:
    """Grows a located box to the line art it cuts through (a lash sticking out of Claude's box), by at most a fifth
    of its size per side: a hair strand crossing an eye is ink too and would drag the box into the brow."""
    h, w = ink.shape
    wx0, wy0, wx1, wy1 = (int(round(v)) for v in _pad(box, 0.2, 0.2))
    wx0, wy0, wx1, wy1 = max(0, wx0), max(0, wy0), min(w, wx1), min(h, wy1)
    if wx1 <= wx0 or wy1 <= wy0:
        return box
    win = ink[wy0:wy1, wx0:wx1]
    seed = np.zeros_like(win)
    bx0, by0, bx1, by1 = (int(round(v)) for v in box)
    seed[max(0, by0 - wy0):max(0, by1 - wy0), max(0, bx0 - wx0):max(0, bx1 - wx0)] = True
    grown = seed & win
    if not grown.any():
        return box
    for _ in range(max(win.shape)):
        nxt = _dilate(grown, 1) & win
        if (nxt == grown).all():
            break
        grown = nxt
    ys, xs = np.nonzero(grown)
    return (min(box[0], wx0 + xs.min()), min(box[1], wy0 + ys.min()), max(box[2], wx0 + xs.max() + 1.0),
            max(box[3], wy0 + ys.max() + 1.0))


class FaceHoles(NamedTuple):
    holes: dict[str, np.ndarray]
    boxes: dict[str, Any]
    keep: dict[str, np.ndarray]
    ink: np.ndarray
    skipped: list[str]


FEATURES = ("eye_l", "eye_r", "brow_l", "brow_r", "mouth")
SPLIT = 3


def _tight(box: tuple[float, float, float, float], ink: np.ndarray, off: list[tuple[float, ...]]
           ) -> tuple[float, float, float, float]:
    """Bounding box of the ink inside `box` that no box in `off` claims (Claude's brow and eye boxes overlap)."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    m = np.zeros(ink.shape, bool)
    m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = True
    for o in off:
        ox0, oy0, ox1, oy1 = (int(round(v)) for v in o)
        m[max(0, oy0):max(0, oy1), max(0, ox0):max(0, ox1)] = False
    ys, xs = np.nonzero(m & ink)
    if len(xs) < 10:
        return box
    return float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)


def _flood(seed: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    grown = seed & allowed
    if not grown.any():
        return grown
    for _ in range(max(allowed.shape)):
        nxt = _dilate(grown, 1) & allowed
        if (nxt == grown).all():
            break
        grown = nxt
    return grown


def occluder(hole: np.ndarray, cores: list[tuple[float, ...]], ink: np.ndarray, rgb: np.ndarray, no_seed: np.ndarray,
             blur: int, hair_in_core: bool) -> np.ndarray:
    """Pixels of `hole` drawn over the feature (a fringe strand, the head outline): line art reaching in from outside
    the hole. Inside the feature's own box only pixels nearer the outside colour than the feature's count (brows are
    darker than the hair crossing them). Grown by the upscale's blur so the antialiased hair edge stays original."""
    ring = _dilate(hole, GROW + 3) & ~hole & ink & ~no_seed
    if ring.sum() < 20:
        return np.zeros_like(hole)
    core = _mask_of(cores, hole.shape)
    allowed = (hole | ring) & ink & ~core
    if hair_in_core:
        hair = np.median(rgb[ring], 0)
        own = core & ink & hole
        if own.sum() >= 20:
            feat = np.median(rgb[own], 0)
            if np.linalg.norm(hair - feat) > 40:
                dh = np.linalg.norm(rgb - hair, axis=2)
                allowed |= own & (dh < np.linalg.norm(rgb - feat, axis=2)) & (dh < 48)
    out = _flood(ring, allowed) & hole
    return _dilate(out, blur) & hole if out.sum() >= 20 else np.zeros_like(hole)


def hair_share(box: tuple[float, float, float, float], ink: np.ndarray, rgb: np.ndarray, alpha: np.ndarray) -> float:
    """Share of a feature box in the colour of the line art just around it: high when Claude boxed an eye / brow a
    fringe hides without listing it as hidden (an eye is white, iris and skin, never mostly one outside colour)."""
    inner = _mask_of([box], alpha.shape) & alpha
    ring = _mask_of([_pad(box, 0, 0, 10)], alpha.shape) & ~_mask_of([_pad(box, 0, 0, 3)], alpha.shape) & ink
    if ring.sum() < 20 or inner.sum() < 20:
        return 0.0
    hair = np.median(rgb[ring], 0)
    return float((np.linalg.norm(rgb[inner] - hair, axis=1) < 30).mean())


def feature_holes(work: Image.Image, boxes: dict[str, Any], scale: float = 1.0) -> FaceHoles:
    """Inpaint holes per group (bool masks on the work canvas), the refined boxes, what to keep from the original
    inside each hole (hair over it) and the features dropped with why. A covered feature (Claude's `hidden`, or a
    hole the hair / a neighbour leaves too little of) is skipped; only a face with none left is an error. Eyes and
    brows split the gap between them, the mouth stays clear of both, so slots never repaint each other's pixels."""
    if boxes.get("face_visible") is False:
        raise StepError("op_no_face", "the head shows no face (helmet, mask or back view)", detail={"boxes": boxes})
    arr = np.asarray(work.convert("RGBA"))
    alpha = arr[..., 3] > 200
    size = work.size
    hidden = {str(h).strip().lower() for h in boxes.get("hidden") or []}
    if boxes.get("brows_visible") is False:
        hidden |= {"brow_l", "brow_r"}
    raw = {k: _box(boxes.get(k), size) for k in FEATURES}
    skipped = [f"{k}: hidden" for k in FEATURES if k in hidden and boxes.get(k) is not None]
    skipped += [f"{k}: no box" for k in FEATURES if k not in hidden and raw[k] is None]
    vis = {k: b for k, b in raw.items() if b is not None and k not in hidden}
    if not vis:
        raise StepError("op_no_face", "no eye, brow or mouth is visible on the head; a covered face (helmet, mask) "
                        "has no expressions to draw", detail={"boxes": boxes})
    rgb = arr[..., :3].astype(np.float64)
    eyes = [vis[k] for k in ("eye_l", "eye_r") if k in vis]
    brows = [vis[k] for k in ("brow_l", "brow_r") if k in vis]
    mouth = vis.get("mouth")
    cheek = np.zeros(alpha.shape, bool)
    if eyes and mouth:
        ex = [(b[0] + b[2]) / 2 for b in eyes] if len(eyes) > 1 else [eyes[0][0], eyes[0][2]]
        cy0, cy1 = int(max(b[3] for b in eyes)), int(mouth[1])
        if cy1 - cy0 >= 4:
            cheek[cy0:cy1, int(min(ex)):int(max(ex)) + 1] = True
    cheek &= alpha
    if cheek.sum() < 20:
        cheek = alpha.copy()
        for b in vis.values():
            x0, y0, x1, y1 = (int(round(v)) for v in _pad(b, 0.3, 0.3))
            cheek[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = False
        if cheek.sum() < 20:
            cheek = alpha
    skin = np.median(rgb[cheek], 0) if cheek.any() else np.full(3, 255.0)
    ink = alpha & (np.linalg.norm(rgb - skin, axis=2) > 48)
    blur = max(1, int(round(scale)))

    # A brow can be the hair's colour, so only a box that is nearly all of it counts as covered.
    for k, limit, group in (("eye_l", 0.5, eyes), ("eye_r", 0.5, eyes), ("brow_l", 0.75, brows),
                            ("brow_r", 0.75, brows)):
        if k in vis and (covered := hair_share(vis[k], ink, rgb, alpha)) > limit:
            skipped.append(f"{k}: covered by hair ({covered:.0%} of its box is the hair colour around it)")
            group.remove(vis[k])
    # Refining an eye against brow ink dragged its box (and hole) into the brow on a real run.
    eyes = [refine(b, ink & ~_mask_of(brows, ink.shape)) for b in eyes]
    tight_eyes = [_tight(b, ink, brows) for b in eyes]
    tight_brows = [_tight(b, ink, eyes) for b in brows]
    eye_hole = np.zeros(alpha.shape, bool)
    for b in eyes:
        _rect(eye_hole, _pad(b, 0.18, 0.22, 2))
    brow_hole = np.zeros(alpha.shape, bool)
    for b in brows:
        _rect(brow_hole, _pad(b, 0.25, 0.4, 2))
    rows = np.arange(alpha.shape[0])[:, None]
    for bb in tight_brows:
        for eb in tight_eyes:
            x0, x1 = max(bb[0], eb[0]), min(bb[2], eb[2])
            if x1 <= x0:
                continue
            split = (bb[3] + eb[1]) / 2
            cols = np.zeros(alpha.shape[1], bool)
            lo = int(max(0, min(bb[0], eb[0]) - 0.3 * (eb[2] - eb[0])))
            cols[lo:int(max(bb[2], eb[2]) + 0.3 * (eb[2] - eb[0])) + 1] = True
            brow_hole &= ~((rows >= split - SPLIT) & cols[None, :])
            eye_hole &= ~((rows <= split + SPLIT) & cols[None, :])
    brow_hole &= ~_dilate(eye_hole, 2)

    holes: dict[str, np.ndarray] = {}
    keep: dict[str, np.ndarray] = {}
    out_boxes: dict[str, Any] = {}

    def settle(group: str, hole: np.ndarray, cores: list[tuple[float, ...]], block: np.ndarray, no_seed: np.ndarray,
               hair_in_core: bool, kept_box: Any) -> None:
        hole = hole & alpha & ~block
        occ = occluder(hole, cores, ink, rgb, block | no_seed, blur, hair_in_core)
        core = _mask_of(cores, alpha.shape) & alpha
        left = hole & ~occ
        shown = (core & left).sum() / max(1, core.sum())
        if left.sum() < 30 or shown < 0.25:
            why = "covered by hair" if (core & occ).sum() > (core & left).sum() else "no room beside the others"
            skipped.append(f"{group}: {why} ({shown:.0%} of it left to repaint)")
            return
        holes[group] = left
        out_boxes[group] = kept_box
        if occ.any():
            keep[group] = occ

    none = np.zeros(alpha.shape, bool)
    if eyes:
        settle("eyes", eye_hole, tight_eyes, none, _dilate(brow_hole, 2), False, [list(map(float, b)) for b in eyes])
    if brows:
        settle("brow", brow_hole, tight_brows, none, _dilate(eye_hole, 2), True, [list(map(float, b)) for b in brows])
    keep_off = none.copy()
    for g in ("eyes", "brow"):
        if g in holes:
            keep_off |= _dilate(holes[g], 2 * GROW + 4)
    if mouth:
        mx0, my0, mx1, my1 = refine(mouth, ink)
        mw, mh = mx1 - mx0, my1 - my0
        cx, cy = (mx0 + mx1) / 2, (my0 + my1) / 2 + 0.15 * mh
        hw, hh = mw * 1.35 / 2 + 2, max(mh * 1.6, 0.55 * mw * 1.35) / 2 + 2
        mouth_hole = np.zeros(alpha.shape, bool)
        _rect(mouth_hole, (cx - hw, cy - hh, cx + hw, cy + hh))
        settle("mouth", mouth_hole, [(mx0, my0, mx1, my1)], keep_off, none, False,
               [float(v) for v in (mx0, my0, mx1, my1)])
    if not holes:
        raise StepError("op_no_face", "every located feature is covered (" + "; ".join(skipped) + ")",
                        detail={"boxes": boxes, "skipped": skipped})
    if skipped:
        out_boxes["skipped"] = skipped
    return FaceHoles(holes, out_boxes, keep, ink, skipped)


def _mask_of(boxes: list[tuple[float, ...]], shape: tuple[int, ...]) -> np.ndarray:
    m = np.zeros(shape[:2], bool)
    for b in boxes:
        x0, y0, x1, y1 = (int(round(v)) for v in b)
        m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = True
    return m


def inpaint_input(work: Image.Image, hole: np.ndarray, erase: bool, ink: np.ndarray | None = None) -> Image.Image:
    """RGBA with alpha 0 over the hole (the Klein inpaint graph's mask). Erased holes are filled with the surrounding
    skin first: Klein redraws a closed eye or an open mouth far more reliably from bare skin than over the old
    feature. Line art / hair around the hole stays out of that colour (a fringe made a brow hole hair-brown)."""
    arr = np.asarray(work.convert("RGBA")).copy()
    if erase:
        ring = _dilate(hole, 6) & ~hole & (arr[..., 3] > 200)
        if ink is not None and (ring & ~ink).sum() >= 10:
            ring &= ~ink
        if ring.sum() >= 10:
            arr[hole, :3] = np.median(arr[ring, :3], 0).astype(np.uint8)
    arr[hole, 3] = 0
    return Image.fromarray(arr, "RGBA")


class SpineFacePrep(OpExecutor):
    """Head attachment upscaled on white (+ the same with a pixel grid, for the locator)."""
    kind = "op.spine_face_prep"
    version = "1"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from .spine_ref import read_skeleton, region_images, skin_parts

        _, doc = await read_skeleton(ctx, inputs)
        images = region_images(ctx, inputs)
        skins, _ = rt.resolve_skins(doc, skin_parts({"skin": params.get("skin", "auto")}, doc))
        slot, att_name, att, skin = find_head(doc, skins, str(params.get("head") or "auto"))
        region = att.get("path") or att_name
        img = images.get(region)
        if img is None:
            raise StepError("op_bad_input", f"atlas has no region {region!r} for the head attachment")
        head = img.convert("RGBA")
        s, canvas = work_size(head.width, head.height, int(params.get("work", WORK)))
        work = head_on_white(head, s, canvas)
        info = {"slot": slot["name"], "bone": slot["bone"], "attachment": att_name, "skin": skin, "region": region,
                "size": [head.width, head.height], "scale": s, "work": list(canvas)}
        return StepResult(outputs={"head": [await _put_png(ctx, work.convert("RGB"), "head.png")],
                                   "grid": [await _put_png(ctx, grid_overlay(work), "head_grid.png")],
                                   "info": [await _put_json(ctx, info, "face_head.json")]},
                          meta={"size": list(canvas), "slot": slot["name"], "region": region})


def work_layer(head: Image.Image, info: dict[str, Any]) -> Image.Image:
    """The head upscaled onto the work canvas, transparent around it (what the plan and the build cut from)."""
    s, (w, h) = float(info["scale"]), info["work"]
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(head.convert("RGBA").resize((round(head.width * s), round(head.height * s)), Image.Resampling.LANCZOS),
              (0, 0))
    return out


def one_sided(what: str, count: int) -> str:
    """The expression text for a face showing one eye / one brow (the other under hair or turned away)."""
    if count != 1:
        return what
    for a, b in (("both eyes", "the visible eye"), ("both eyebrows", "the visible eyebrow"), ("each eye", "the eye"),
                 ("each iris", "the iris"), ("their inner ends", "its inner end"), ("eyelids", "eyelid")):
        what = what.replace(a, b)
    return what


def _located(raw: Any) -> dict[str, Any]:
    return (raw.get("boxes", raw) if isinstance(raw, dict) else None) or {}


class SpineFacePlan(OpExecutor):
    """One Klein inpaint input + prompt per expression from the located features."""
    kind = "op.spine_face_plan"
    version = "2"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from .spine_ref import region_images

        info = await _read_json(ctx, inputs, "info")
        boxes = _located(await _read_json(ctx, inputs, "boxes"))
        head = region_images(ctx, inputs).get(info["region"])
        if head is None:
            raise StepError("op_bad_input", f"atlas has no region {info['region']!r}")
        work = work_layer(head, info)
        fh = await asyncio.to_thread(feature_holes, work, boxes, float(info["scale"]))
        variants = wanted_variants(parse_emotions(params.get("emotions", "happy,angry,sad")), set(fh.holes))
        on_white = Image.new("RGBA", work.size, (255, 255, 255, 255))
        on_white.alpha_composite(work)
        refs, prompts = [], []
        for v in variants:
            img = await asyncio.to_thread(inpaint_input, on_white, fh.holes[v.split("_")[0]], v != "eyes_half", fh.ink)
            refs.append(await _put_png(ctx, img, f"hole_{v}.png"))
            prompts.append(PROMPT.format(what=one_sided(WHAT[v], len(fh.boxes.get(v.split("_")[0]) or []))))
        plan = {**info, "variants": variants, "located": boxes, "boxes": fh.boxes}
        if fh.skipped:
            ctx.log("face features skipped: " + "; ".join(fh.skipped), "warning")
        meta: dict[str, Any] = {"variants": variants, "groups": sorted(fh.holes)}
        if fh.skipped:
            meta["skipped"] = fh.skipped
        return StepResult(outputs={"holes": refs,
                                   "layout": [await _put_json(ctx, {"prompts": prompts}, "face_layout.json")],
                                   "plan": [await _put_json(ctx, plan, "face_plan.json")]},
                          meta=meta)


def tone_match(edit: Image.Image, work: Image.Image, hole: np.ndarray) -> Image.Image:
    """Klein repaints the whole hole a few levels lighter or pinker than the skin around it, which showed as a box
    on a real run. The graph composites its 4 px grown mask, so just outside the hole the edit and the original
    show the same surroundings: shift the repainted area by their median difference there."""
    e = np.asarray(edit.convert("RGB"), np.float64)
    o = np.asarray(work.convert("RGBA"), np.float64)
    grown = _dilate(hole, GROW)
    rim = _dilate(hole, GROW - 1) & ~hole & (o[..., 3] > 200)
    if rim.sum() < 20:
        return edit
    delta = np.clip(np.median(o[..., :3][rim] - e[rim], 0), -40, 40)
    w = np.asarray(Image.fromarray((grown * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), np.float64)
    out = e + (w / 255)[..., None] * delta
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")


def group_alpha(hole: np.ndarray, head_alpha: np.ndarray, keep: np.ndarray | None = None) -> np.ndarray:
    """Fades across the hole's edge (half original there) so the seam Klein leaves along it does not show. Hair
    over the feature (`keep`) is fully the original, not faded: the feather there drew a blurred rim round a fringe."""
    m = _dilate(hole, 1).astype(np.float64)
    soft = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(FEATHER)),
                      np.float64) / 255
    if keep is not None:
        soft[keep] = 0
    return np.minimum(soft, head_alpha)


def native_patch(src: Image.Image, alpha: np.ndarray, s: float, size: tuple[int, int]
                 ) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    """Cut `src` (work canvas, or the native head when s == 1 with a work-size alpha) to the texel box that alpha
    touches, on the head's original texel grid."""
    ys, xs = np.nonzero(alpha > 0.01)
    if not len(xs):
        return None
    x0, y0 = max(0, int(math.floor(xs.min() / s)) - 1), max(0, int(math.floor(ys.min() / s)) - 1)
    x1, y1 = min(size[0], int(math.ceil((xs.max() + 1) / s)) + 1), min(size[1], int(math.ceil((ys.max() + 1) / s)) + 1)
    work_box = (x0 * s, y0 * s, x1 * s, y1 * s)
    a = Image.fromarray((alpha * 255).astype(np.uint8)).resize((x1 - x0, y1 - y0), Image.Resampling.BOX, box=work_box)
    if src.size == size:
        rgb = src.convert("RGBA").crop((x0, y0, x1, y1))
    else:
        rgb = src.convert("RGBA").resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS, box=work_box)
    arr = np.asarray(rgb).copy()
    arr[..., 3] = np.minimum(arr[..., 3], np.asarray(a))
    return Image.fromarray(arr, "RGBA"), (x0, y0, x1, y1)


def extend_page(page: Image.Image, patches: list[Image.Image], pma: bool, pad: int = 2
                ) -> tuple[Image.Image, list[tuple[int, int]]]:
    """Patches shelf-packed under the existing page content (old regions keep their bounds)."""
    width = max(page.width, max(p.width for p in patches) + 2 * pad)
    x, y, shelf, pos = pad, page.height + pad, 0, []
    for p in patches:
        if x + p.width + pad > width:
            x, y, shelf = pad, y + shelf + pad, 0
        pos.append((x, y))
        x += p.width + pad
        shelf = max(shelf, p.height)
    out = Image.new("RGBA", (width, y + shelf + pad), (0, 0, 0, 0))
    out.paste(page.convert("RGBA"), (0, 0))
    for p, xy in zip(patches, pos):
        if pma:
            arr = np.asarray(p, np.float64)
            arr[..., :3] *= arr[..., 3:] / 255
            p = Image.fromarray((arr + 0.5).astype(np.uint8), "RGBA")
        out.paste(p, xy)
    return out, pos


def extend_atlas(text: str, page_index: int, new_name: str, size: tuple[int, int],
                 regions: list[tuple[str, int, int, int, int]]) -> str:
    """Atlas text with page `page_index` renamed, resized and given the new regions (other lines untouched)."""
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.strip():
            blocks[-1].append(line)
        elif blocks[-1]:
            blocks.append([])
    blocks = [b for b in blocks if b]
    blk = blocks[page_index]
    blk[0] = new_name
    for i, line in enumerate(blk[1:], 1):
        key, sep, _ = line.partition(":")
        if not sep:
            break
        if key.strip() == "size":
            blk[i] = f"size:{size[0]},{size[1]}"
    for name, x, y, w, h in regions:
        blk += [name, f"bounds:{x},{y},{w},{h}"]
    return "\n\n".join("\n".join(b) for b in blocks) + "\n"


def _unique(name: str, used: set[str]) -> str:
    out, k = name, 2
    while out in used:
        out, k = f"{name}_{k}", k + 1
    used.add(out)
    return out


def blink_keys(slot_name: str, seed: int, has_half: bool, blinks: int = 4) -> tuple[list[dict[str, Any]], float]:
    """Idle blinks 3–5 s apart (seeded), the loop seam one more such gap so a looping track never blinks twice."""
    rng = random.Random(seed)
    gaps = [round(rng.uniform(3.0, 5.0), 2) for _ in range(blinks)]
    t0 = 1.0
    keys: list[dict[str, Any]] = [{"time": 0, "name": NEUTRAL["eyes"]}]
    t = t0
    for g in gaps:
        seq = [(0, "eyes_half"), (0.04, "eyes_closed"), (0.12, "eyes_half"), (0.16, NEUTRAL["eyes"])] if has_half \
            else [(0, "eyes_closed"), (0.12, NEUTRAL["eyes"])]
        keys += [{"time": round(t + dt, 3), "name": n} for dt, n in seq]
        t += g
    end = round(t0 + sum(gaps), 3)
    keys.append({"time": end, "name": NEUTRAL["eyes"]})
    return keys, end


def talk_keys(seed: int, length: float = 2.4) -> list[dict[str, Any]]:
    """Mouth A / O / M switching every 0.08–0.16 s (no shape twice in a row), back to neutral at the end."""
    rng = random.Random(seed)
    keys: list[dict[str, Any]] = []
    t, last = 0.0, ""
    while t < length - 0.2:
        shape = rng.choice([s for s in ("mouth_a", "mouth_o", "mouth_m") if s != last])
        keys.append({"time": round(t, 3), "name": shape})
        last = shape
        t += rng.uniform(0.08, 0.16)
    keys += [{"time": round(t, 3), "name": NEUTRAL["mouth"]}, {"time": round(length, 3), "name": NEUTRAL["mouth"]}]
    return keys


def add_face(doc: dict[str, Any], plan: dict[str, Any], placed: dict[str, dict[str, dict[str, Any]]],
             emotions: list[str], seed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """New doc with face slots after the head slot, their attachments in the head's skin and the face animations.
    `placed` = group -> attachment name -> region attachment."""
    doc = json.loads(json.dumps(doc))
    head = next(s for s in doc["slots"] if s["name"] == plan["slot"])
    used = {s["name"] for s in doc["slots"]}
    slot_names: dict[str, str] = {}
    new_slots = []
    for g in GROUPS:
        if g not in placed:
            continue
        name = _unique(g, used)
        slot_names[g] = name
        slot = {"name": name, "bone": head["bone"], "attachment": NEUTRAL[g]}
        for key in ("color", "dark", "blend"):
            if key in head:
                slot[key] = head[key]
        new_slots.append(slot)
    at = doc["slots"].index(head) + 1
    doc["slots"][at:at] = new_slots
    skin = next(s for s in doc["skins"] if s["name"] == plan["skin"])
    skin.setdefault("attachments", {})
    for g, atts in placed.items():
        skin["attachments"][slot_names[g]] = atts

    anims = doc.setdefault("animations", {})
    report: dict[str, Any] = {"slots": slot_names, "animations": {}, "warnings": []}
    for name, a in anims.items():
        if a.get("drawOrder") or a.get("draworder"):
            report["warnings"].append(f"animation {name} has draw order keys; face slots were inserted after "
                                      f"{plan['slot']}, check its offsets in the editor")
        head_tl = (a.get("slots") or {}).get(plan["slot"]) or {}
        tint = {k: v for k, v in head_tl.items() if k in ("rgba", "rgb", "alpha", "rgba2", "rgb2")}
        if tint:
            for s in slot_names.values():
                a.setdefault("slots", {})[s] = json.loads(json.dumps(tint))
    taken = set(anims)

    def put(name: str, slots: dict[str, list[dict[str, Any]]]) -> None:
        real = _unique(name, taken)
        anims[real] = {"slots": {slot_names[g]: {"attachment": keys} for g, keys in slots.items()}}
        report["animations"][name] = real

    eyes = placed.get("eyes", {})
    if "eyes_closed" in eyes:
        keys, _ = blink_keys(slot_names["eyes"], seed, "eyes_half" in eyes)
        put("blink", {"eyes": keys})
    if {"mouth_a", "mouth_o", "mouth_m"} <= set(placed.get("mouth", {})):
        put("talk", {"mouth": talk_keys(seed + 1)})
    for e in emotions:
        parts = {g: v for g, v in EMOTIONS[e].items() if v in placed.get(g, {})}
        if parts:
            put(e, {g: [{"time": 0, "name": v}, {"time": 1.0, "name": v}] for g, v in parts.items()})
    return doc, report


def members_text(text: str, doc: dict[str, Any], keys: tuple[str, ...] = ("animations", "skins", "slots")) -> str:
    """`text` with these top-level members replaced by `doc`'s (appended when new), the rest byte for byte."""
    out = text
    for key in keys:
        if key not in doc:
            continue
        body = json.dumps(doc[key], ensure_ascii=False, separators=(",", ":"))
        top = _member_spans(out, out.index("{"))
        span = next(((vs, ve) for k, _, vs, ve in top if k == key), None)
        if span is not None:
            out = out[:span[0]] + body + out[span[1]:]
        else:
            last_end = top[-1][3]
            out = out[:last_end] + f',"{key}":' + body + out[last_end:]
    return out


def _with_setup(doc: dict[str, Any], attachments: dict[str, str]) -> dict[str, Any]:
    out = dict(doc)
    out["slots"] = [{**s, "attachment": attachments.get(s["name"], s.get("attachment"))} for s in doc["slots"]]
    return out


def _head_frame(doc: dict[str, Any], atts: dict[str, dict[str, dict[str, Any]]], plan: dict[str, Any],
                height: int) -> tuple[tuple[float, float, float, float], float]:
    pose = rt.Pose(doc)
    head_att = atts[plan["slot"]][plan["attachment"]]
    pts = rt.mesh_world(head_att, pose, plan["bone"]) if head_att.get("type") == "mesh" \
        else rt._region_corners(head_att, pose, plan["bone"])
    (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
    pad = 0.08 * max(x1 - x0, y1 - y0)
    box = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    return box, height / (box[3] - box[1])


def head_render(doc: dict[str, Any], images: rt.RegionImages, skins: list[str], plan: dict[str, Any],
                setup: dict[str, str] | None = None, height: int = 240) -> Image.Image:
    atts = rt.skin_attachments(doc, skins)
    box, scale = _head_frame(doc, atts, plan, height)
    return rt.render(rt.Pose(_with_setup(doc, setup or {})), atts, images, box, scale)


def face_sheet(doc: dict[str, Any], states: list[tuple[str, dict[str, str]]], images: rt.RegionImages,
               skins: list[str], plan: dict[str, Any], height: int = 240) -> Image.Image:
    from .spine_ref import _label

    cells = []
    for label, setup in states:
        img = head_render(doc, images, skins, plan, setup, height)
        cell = Image.new("RGBA", img.size, (236, 232, 244, 255))
        cell.alpha_composite(img)
        _label(cell, label)
        cells.append(cell)
    cols = min(5, len(cells))
    rows = math.ceil(len(cells) / cols)
    w, h = cells[0].size
    sheet = Image.new("RGBA", (cols * (w + 4) - 4, rows * (h + 4) - 4), (40, 36, 52, 255))
    for i, c in enumerate(cells):
        sheet.paste(c, ((i % cols) * (w + 4), (i // cols) * (h + 4)))
    return sheet


def placement_error(doc: dict[str, Any], plan: dict[str, Any], placed: dict[str, dict[str, dict[str, Any]]],
                    boxes: dict[str, tuple[int, int, int, int]], a: np.ndarray, b: np.ndarray) -> float:
    """Largest distance (head texels) between where a face attachment's corners land and where the same texels of
    the head land, in the setup pose."""
    pose = rt.Pose(doc)
    m, o = pose.world[plan["bone"]]
    per_texel = math.sqrt(abs(np.linalg.det(m @ a))) or 1.0
    worst = 0.0
    for g, atts in placed.items():
        x0, y0, x1, y1 = boxes[g]
        want = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float64)
        expect = (m @ (a @ want.T + b[:, None])).T + o
        for att in atts.values():
            got = rt._region_corners(att, pose, plan["bone"])
            worst = max(worst, float(np.abs(got - expect).max()) / per_texel)
    return worst


class SpineFaceBuild(OpExecutor):
    """Expression attachments cut from the inpaints, face slots + animations, the atlas page extended, QA sheet."""
    kind = "op.spine_face_build"
    version = "2"

    async def run(self, ctx: StepContext, params: dict[str, Any], inputs: Outputs) -> StepResult:
        from .spine import PLAYER_VERSION, compare, preview_html, validate_skeleton
        from .spine_ref import read_skeleton, region_images, skin_parts

        text, doc = await read_skeleton(ctx, inputs)
        images = region_images(ctx, inputs)
        plan = await _read_json(ctx, inputs, "plan")
        edits = [Image.open(ctx.blobs.path(r.hash)).convert("RGBA") for r in _images_in(inputs, "edits")]
        variants: list[str] = plan["variants"]
        if len(edits) != len(variants):
            raise StepError("op_bad_input", f"{len(edits)} inpainted images for {len(variants)} expressions")
        emotions = parse_emotions(params.get("emotions", "happy,angry,sad"))
        seed = int(params.get("seed", 7))
        skins, _ = rt.resolve_skins(doc, skin_parts({"skin": params.get("skin", "auto")}, doc))
        new_doc, report, patches, boxes, a, b = await asyncio.to_thread(self._build, doc, plan, images, edits,
                                                                        emotions, seed)
        atlas_ref = inputs["atlas"][0]
        atlas_text = await asyncio.to_thread(ctx.blobs.path(atlas_ref.hash).read_text, encoding="utf-8-sig")
        pi = images.atlas.regions[plan["region"]].page
        page_meta = images.atlas.pages[pi]
        used = set(images.atlas.regions)
        names = {k: _unique(f"{plan['region']}_{k}", used) for k in patches}
        skin = next(s for s in new_doc["skins"] if s["name"] == plan["skin"])
        for slot_name in report["slots"].values():
            for att_name, att in skin["attachments"][slot_name].items():
                att["path"] = names[att_name]
        order = list(patches)
        page_img, pos = await asyncio.to_thread(extend_page, Image.open(images.pages[pi]),
                                                [patches[k] for k in order], page_meta.pma)
        new_page = f"{Path(page_meta.name).stem}_face.png"
        new_atlas = extend_atlas(atlas_text, pi, new_page, page_img.size,
                                 [(names[k], x, y, patches[k].width, patches[k].height)
                                  for k, (x, y) in zip(order, pos)])
        bad = [p for p in validate_skeleton(new_doc, new_atlas) if any(s in p for s in report["slots"].values())]
        if bad:
            raise StepError("op_invalid_output", "; ".join(bad[:6]), detail={"problems": bad})
        ctx.job_dir.mkdir(parents=True, exist_ok=True)
        page_files = list(images.pages)
        page_file = ctx.job_dir / new_page
        await asyncio.to_thread(page_img.save, page_file)
        page_files[pi] = page_file
        sk_stem = Path(inputs["skeleton"][0].name or "skeleton.json").stem
        atlas_file = ctx.job_dir / f"{sk_stem}.atlas"
        atlas_file.write_text(new_atlas, encoding="utf-8")
        new_text = members_text(text, new_doc)
        new_images = rt.RegionImages.from_files(atlas_file, page_files)

        neutral = {report["slots"][g]: NEUTRAL[g] for g in report["slots"]}
        states: list[tuple[str, dict[str, str]]] = [("neutral", neutral)]
        for label, v in (("blink", "eyes_closed"), ("half", "eyes_half"), ("A", "mouth_a"), ("O", "mouth_o"),
                         ("M", "mouth_m")):
            g = v.split("_")[0]
            if g in report["slots"] and v in patches:
                states.append((label, {**neutral, report["slots"][g]: v}))
        for e in emotions:
            parts = {report["slots"][g]: v for g, v in EMOTIONS[e].items() if g in report["slots"] and v in patches}
            if parts:
                states.append((e, {**neutral, **parts}))
        sheet = await asyncio.to_thread(face_sheet, new_doc, states, new_images, skins, plan)
        before = await asyncio.to_thread(head_render, doc, images, skins, plan, None, 480)
        after = await asyncio.to_thread(head_render, new_doc, new_images, skins, plan, neutral, 480)
        report["neutral_match"] = compare(before, after)
        report["placement_error_px"] = round(placement_error(new_doc, plan, report.pop("placed"), boxes, a, b), 4)
        report["regions"] = names
        report["page"] = new_page
        report["runtime"] = ("Spine 4.2: play the body animation on track 0 and blink / talk / an emotion on "
                             "track 1 (AnimationState.setAnimation(1, 'blink', true)).")
        if report["placement_error_px"] > 1.0:
            ctx.log(f"face attachments off by {report['placement_error_px']} px", "warning")

        out: Outputs = {
            "skeleton": [await ctx.blobs.put_bytes(new_text.encode("utf-8"), name=f"{sk_stem}.json",
                                                   mime="application/json")],
            "atlas": [await ctx.blobs.put_file(atlas_file, name=atlas_file.name, mime="text/plain")],
            "texture": [await ctx.blobs.put_file(f, name=f.name, mime="image/png") if i == pi
                        else inputs["atlas"][1 + i] for i, f in enumerate(page_files)],
            "preview": [await _put_png(ctx, sheet, f"{sk_stem}_face.png")],
        }
        if len(page_files) == 1:
            page_html = preview_html(sk_stem, new_text.encode("utf-8"), new_atlas.encode("utf-8"), new_page,
                                     page_file.read_bytes(), list(report["animations"].values()))
            out["html"] = [await ctx.blobs.put_bytes(page_html.encode("utf-8"), name=f"{sk_stem}_face.html",
                                                     mime="text/html")]
            report["player"] = PLAYER_VERSION
        out["report"] = [await _put_json(ctx, report, "face.json")]
        return StepResult(outputs=out, meta={"variants": variants, "animations": list(report["animations"].values()),
                                             "placement_error_px": report["placement_error_px"],
                                             "neutral_mae": report["neutral_match"]["mae"]})

    @staticmethod
    def _build(doc: dict[str, Any], plan: dict[str, Any], images: rt.RegionImages, edits: list[Image.Image],
               emotions: list[str], seed: int):
        head = images.get(plan["region"])
        if head is None:
            raise StepError("op_bad_input", f"atlas has no region {plan['region']!r}")
        head = head.convert("RGBA")
        s = float(plan["scale"])
        wsize = tuple(plan["work"])
        work = work_layer(head, plan)
        head_alpha = np.asarray(work.getchannel("A"), np.float64) / 255
        fh = feature_holes(work, plan["located"], s)
        holes = fh.holes
        alphas: dict[str, np.ndarray] = {}
        for g in GROUPS:
            if g in holes:
                al = group_alpha(holes[g], head_alpha, fh.keep.get(g))
                for prev in alphas.values():
                    al = al * (1 - prev)
                alphas[g] = al
        att = rt.skin_attachments(doc, [plan["skin"]] if plan["skin"] != "default" else [])[plan["slot"]][plan["attachment"]]
        a, b, resid = texel_affine(att, doc, plan["bone"])
        if resid > 0.75:
            raise StepError("op_bad_input", f"head mesh is bent in the setup pose ({resid:.1f} texels off a flat "
                            "map); face patches need a flat head image")
        size = (head.width, head.height)
        patches: dict[str, Image.Image] = {}
        boxes: dict[str, tuple[int, int, int, int]] = {}
        placed: dict[str, dict[str, dict[str, Any]]] = {}
        for g, al in alphas.items():
            cut = native_patch(head, al, s, size)
            if cut is None:
                continue
            img, box = cut
            boxes[g] = box
            patches[NEUTRAL[g]] = img
            placed.setdefault(g, {})[NEUTRAL[g]] = region_from_box(a, b, box)
        for v, edit in zip(plan["variants"], edits):
            g = v.split("_")[0]
            if g not in boxes:
                continue
            if edit.size != wsize:
                edit = edit.resize(wsize, Image.Resampling.LANCZOS)
            edit = tone_match(edit, work, holes[g])
            cut = native_patch(edit, alphas[g], s, size)
            if cut is None:
                continue
            img, box = cut
            patches[v] = img
            placed[g][v] = region_from_box(a, b, box)
        new_doc, report = add_face(doc, plan, placed, emotions, seed)
        report["placed"] = placed
        report["variants"] = {g: list(v) for g, v in placed.items()}
        report["head"] = {k: plan[k] for k in ("slot", "bone", "attachment", "skin", "region", "size")}
        report["boxes"] = {g: list(bx) for g, bx in boxes.items()}
        if fh.skipped:
            report["skipped"] = fh.skipped
        return new_doc, report, patches, boxes, a, b


def face_readme(path: Path, runtime: str) -> str | None:
    """README line for an exported skeleton that has the spine-face slots + animations (None otherwise)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    anims = (doc.get("animations") or {}) if isinstance(doc, dict) else {}
    slots = {s.get("name") for s in doc.get("slots") or []} if isinstance(doc, dict) else set()
    face = [n for n in ("blink", "talk", "happy", "angry", "sad") if n in anims]
    if not face or not ({"eyes", "brow", "mouth"} & slots):
        return None
    call = ('get_animation_state().set_animation("blink", true, 1)' if "godot" in runtime
            else 'AnimationState.SetAnimation(1, "blink", true)')
    return (f"- Biểu cảm mặt: anim {', '.join(face)} chỉ đổi attachment slot mắt / mày / miệng — chạy trên track 1 "
            f"đè lên anim thân ở track 0 ({runtime}: {call}).")


FACE_OPS: tuple[type[OpExecutor], ...] = (SpineFacePrep, SpineFacePlan, SpineFaceBuild)
