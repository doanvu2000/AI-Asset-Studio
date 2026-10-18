"""Step-kind catalog for the flow editor: lane, typed ports and typed params of every registered kind."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..contracts import ExecutorRegistry

log = logging.getLogger(__name__)

P = dict[str, Any]


def _int(default: int | None = None, **kw: Any) -> P:
    return {"type": "int", **({"default": default} if default is not None else {}), **kw}


def _float(default: float | None = None, **kw: Any) -> P:
    return {"type": "float", **({"default": default} if default is not None else {}), **kw}


def _bool(default: bool, **kw: Any) -> P:
    return {"type": "bool", "default": default, **kw}


def _text(default: str | None = None, **kw: Any) -> P:
    return {"type": "text", **({"default": default} if default is not None else {}), **kw}


def _enum(options: list[Any], default: Any, **kw: Any) -> P:
    return {"type": "enum", "options": options, "default": default, **kw}


RESAMPLE = ["lanczos", "bicubic", "bilinear", "box", "nearest"]
TIMEOUT = {"_timeout_s": _int(label="Giới hạn thời gian (giây)", advanced=True)}
COMFY_OPTIONS: dict[str, P] = {
    "_free_before": _bool(False, label="Giải phóng VRAM trước khi chạy", advanced=True),
    "_timeout_s": _int(1800, label="Giới hạn thời gian (giây)", advanced=True),
    "_auto_retry": _int(1, min=0, max=3, label="Tự thử lại khi lỗi GPU", advanced=True),
}

# "in"/"out" map port name -> port type (image, image[], mesh, json, text, any); "*" means pass-through.
CATALOG: dict[str, P] = {
    "gate.pick": {"label": "Chọn ảnh", "in": {"images": "image[]"}, "out": {"images": "image[]"},
                  "params": {"k": _int(1, min=0, label="Số ảnh cần chọn (0 = ít nhất 1)")}},
    "gate.approve": {"label": "Duyệt kết quả", "in": {"*": "any"}, "out": {"*": "any"}, "params": {}},
    "op.trim_pad_resize": {"label": "Cắt viền & căn cỡ", "in": {"images": "image[]"}, "out": {"images": "image[]"},
                           "params": {"size": _int(512, min=8, max=4096, label="Cỡ (px)"),
                                      "pad": _float(0.06, min=0, max=0.5, label="Lề"),
                                      "square": _bool(True, label="Vuông"),
                                      "resample": _enum(RESAMPLE, "lanczos", label="Lấy mẫu")}},
    "op.normalize": {"label": "Chuẩn hoá khung", "in": {"images": "image[]"}, "out": {"images": "image[]"},
                     "params": {"size": _int(512, min=8, max=4096, label="Cỡ (px)"),
                                "pad": _float(0.06, min=0, max=0.5, label="Lề"),
                                "anchor": _enum(["bottom", "center"], "bottom", label="Neo"),
                                "resample": _enum(RESAMPLE, "lanczos", label="Lấy mẫu")}},
    "op.mirror_dirs": {"label": "Lật thành 8 hướng", "in": {"images": "image[]"}, "out": {"images": "image[]"},
                       "params": {"count": _int(8, options=[4, 8, 16], label="Số hướng")}},
    "op.downscale": {"label": "Thu nhỏ", "in": {"images": "image[]"}, "out": {"images": "image[]"},
                     "params": {"factor": _float(2, min=1, max=16, label="Hệ số"),
                                "resample": _enum(RESAMPLE, "box", label="Lấy mẫu")}},
    "op.pixelate": {"label": "Pixel art", "in": {"images": "image[]"}, "out": {"images": "image[]"},
                    "params": {"size": _int(64, min=8, max=1024, label="Cỡ (px)"),
                               "palette_colors": _int(min=2, max=256, label="Số màu"),
                               "outline": _text(label="Màu viền (#rrggbb)")}},
    "op.sheet": {"label": "Ghép sprite sheet", "in": {"images": "image[]", "pivot": "json"},
                 "out": {"sheet": "image", "meta": "json"},
                 "params": {"preset": {"type": "preset", "label": "Preset xuất"},
                            "cols": _int(min=1, max=64, label="Số cột"),
                            "cell": _int(min=8, max=4096, label="Cỡ ô (px)"),
                            "padding": _int(0, min=0, max=64, label="Khoảng cách"),
                            "pivot": _enum(["", "bottom"], "", label="Pivot"),
                            "order": {"type": "list", "label": "Góc của từng khung"},
                            "asset": _text(label="Tên asset (đặt tên khung)")}},
    "op.glb_validate": {"label": "Kiểm tra GLB", "in": {"mesh": "mesh"}, "out": {"meshes": "mesh"},
                        "params": {"require_textures": _bool(True, label="Bắt buộc texture"),
                                   "max_triangles": _int(min=0, label="Tối đa tam giác"),
                                   "max_bytes": _int(min=0, label="Tối đa dung lượng (byte)")}},
    "codex.image": {"label": "Sinh ảnh (Codex)", "in": {"refs": "image[]"}, "out": {"images": "image[]"},
                    "params": {"prompt": _text(label="Prompt", multiline=True),
                               "count": _int(1, min=1, max=4, label="Số ảnh"),
                               "transparent": _bool(False, label="Nền trong suốt")}},
    "claude.task": {"label": "Claude phân tích", "in": {"images": "image[]"}, "out": {"json": "json", "text": "text"},
                    "params": {"prompt": _text(label="Yêu cầu", multiline=True),
                               "model": _enum(["haiku", "sonnet", "opus"], "sonnet", label="Model"),
                               "max_turns": _int(4, min=1, max=40, label="Số lượt tối đa"),
                               "system": _text(label="System prompt", multiline=True, advanced=True),
                               "schema": {"type": "json", "label": "JSON schema", "advanced": True},
                               "cache": _bool(False, label="Cho phép dùng lại kết quả")}},
    "claude.brief": {"label": "Claude viết brief", "in": {}, "out": {"json": "json", "text": "text"},
                     "params": {"idea": _text(label="Ý tưởng", multiline=True),
                                "target": _enum(["generic", "concept", "sprite", "prop"], "generic", label="Mục tiêu"),
                                "style": _text(label="Phong cách"),
                                "model": _enum(["haiku", "sonnet", "opus"], "haiku", label="Model"),
                                "cache": _bool(False, label="Cho phép dùng lại kết quả")}},
    "claude.procedural_3d": {"label": "Claude dựng 3D procedural", "in": {},
                             "out": {"meshes": "mesh", "images": "image[]", "code": "text", "json": "json"},
                             "params": {"idea": _text(label="Ý tưởng", multiline=True),
                                        "style": _text(label="Phong cách"),
                                        "height": _float(1.0, min=0.01, label="Chiều cao"),
                                        "rounds": _int(3, min=1, max=3, label="Số vòng sửa"),
                                        "accept_score": _int(8, min=1, max=10, label="Điểm chấp nhận"),
                                        "views": _int(6, min=1, max=16, label="Số góc render"),
                                        "res": _int(256, options=[128, 256, 512], label="Cỡ render"),
                                        "code_model": _enum(["haiku", "sonnet", "opus"], "sonnet", label="Model viết code"),
                                        "qa_model": _enum(["haiku", "sonnet", "opus"], "sonnet", label="Model QA"),
                                        "cache": _bool(False, label="Cho phép dùng lại kết quả")}},
    "blender.cleanup": {"label": "Dọn mesh (Blender)", "in": {"mesh": "mesh"}, "out": {"meshes": "mesh"},
                        "params": {"target_faces": _int(50000, min=100, label="Số mặt đích"),
                                   "height": _float(1.0, min=0.01, label="Chiều cao"),
                                   "origin": _enum(["feet", "center", "keep"], "feet", label="Gốc toạ độ"),
                                   "upright": _enum(["none", "x90", "x-90", "y90", "y-90", "x180"], "none",
                                                    label="Dựng đứng"),
                                   "smooth_angle": _float(40, min=0, max=180, label="Góc làm mượt"),
                                   **TIMEOUT}},
    "blender.render_dirs": {"label": "Render các hướng", "in": {"mesh": "mesh"},
                            "out": {"images": "image[]", "meta": "json"},
                            "params": {"count": _int(8, options=[4, 8, 16], label="Số hướng"),
                                       "elevation": _float(30, min=0, max=90, label="Góc nghiêng"),
                                       "res": _int(256, options=[64, 128, 256, 512, 1024], label="Cỡ khung"),
                                       "supersample": _int(2, min=1, max=4, label="Siêu lấy mẫu"),
                                       "mirror": _bool(False, label="Lật gương (5 hướng → 8)"),
                                       "engine": _enum(["BLENDER_EEVEE", "CYCLES", "BLENDER_WORKBENCH"],
                                                       "BLENDER_EEVEE", label="Engine"),
                                       "lighting": _enum(["studio", "flat"], "studio", label="Ánh sáng"),
                                       "margin": _float(1.05, min=1, max=2, label="Lề khung", advanced=True),
                                       **TIMEOUT}},
    "blender.script": {"label": "Chạy script Blender", "in": {"mesh": "mesh", "code": "text"},
                       "out": {"meshes": "mesh", "images": "image[]", "report": "json"},
                       "params": {"code": _text(label="Mã bpy", multiline=True, code=True),
                                  "name": _text("script", label="Tên file"),
                                  "params": {"type": "json", "label": "Tham số script"},
                                  "render": {"type": "json", "label": "Render sau khi chạy", "advanced": True},
                                  "timeout_s": _int(120, min=5, label="Giới hạn thời gian (giây)")}},
    "blender.agent": {"label": "Agent Blender (MCP)", "in": {"mesh": "mesh"}, "out": {"meshes": "mesh"},
                      "params": {"task": _text(label="Nhiệm vụ", multiline=True),
                                 "model": _enum(["haiku", "sonnet", "opus"], "sonnet", label="Model"),
                                 "max_turns": _int(40, min=1, max=200, label="Số lượt tối đa"),
                                 "require_mesh": _bool(True, label="Bắt buộc xuất mesh"),
                                 "timeout_s": _int(900, min=30, label="Giới hạn thời gian (giây)")}},
}

_COMFY_LABELS = {
    "t2i_zimage_turbo": "Sinh ảnh (Z-Image)",
    "t2i_zimage_controlnet": "Sinh ảnh theo khung (ControlNet)",
    "remove_bg_birefnet": "Tách nền (BiRefNet)",
    "img2mesh_hunyuan21": "Dựng mesh (Hunyuan3D)",
    "img2mesh_trellis2_textured": "Dựng mesh (TRELLIS)",
    "edit_flux2_klein4b": "Sửa ảnh (Flux2 Klein)",
    "multi_angles_qwen2511": "Nhiều góc (Qwen)",
}


def comfy_entries(workflows_dir: Path | None) -> dict[str, P]:
    if workflows_dir is None:
        return {}
    try:
        index = json.loads((workflows_dir / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.info("comfy workflow index unavailable: %s", e)
        return {}
    out: dict[str, P] = {}
    for name, entry in index.items():
        if not isinstance(entry, dict):
            continue
        meshy = any("image" not in str(o.get("class", "")).lower() for o in entry.get("outputs", []))
        params: dict[str, P] = {t["key"]: _text(t.get("default"), label=t.get("title") or t["key"], multiline=True,
                                                 node=True)
                                for t in entry.get("texts", []) if "key" in t}
        params.update(COMFY_OPTIONS)
        out[f"comfy.{name}"] = {
            "label": _COMFY_LABELS.get(name, f"ComfyUI · {name.replace('_', ' ')}"),
            "in": {"image": "image"} if entry.get("images") else {},
            "out": {"meshes": "mesh"} if meshy else {"images": "image[]"},
            "params": params,
            "free_params": True,
        }
    return out


def describe_kinds(registry: ExecutorRegistry, workflows_dir: Path | None = None) -> list[P]:
    """Every exact kind plus one entry per ComfyUI workflow; unknown kinds still list with lane only."""
    known = {**CATALOG, **comfy_entries(workflows_dir)}
    out: list[P] = []
    names = [k for k in registry.kinds() if not k.endswith("*")]
    prefixes = [k[:-1] for k in registry.kinds() if k.endswith("*")]
    names += sorted(k for k in known if any(k.startswith(p) for p in prefixes) and k not in names)
    for kind in names:
        try:
            lane = registry.resolve(kind).lane.value
        except Exception as e:  # a prefix factory may reject a workflow that vanished since startup
            log.warning("kind %s not resolvable: %s", kind, e)
            continue
        spec = known.get(kind, {})
        out.append({"kind": kind, "lane": lane, "label": spec.get("label", kind), "in": spec.get("in"),
                    "out": spec.get("out"), "params": spec.get("params", {}),
                    "free_params": spec.get("free_params", kind not in known)})
    return out
