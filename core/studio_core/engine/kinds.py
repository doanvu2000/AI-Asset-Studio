"""Step-kind catalog for the flow editor: lane, typed ports and typed params of every registered kind."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..adapters.claude import DEFAULT_MODEL, MODEL_CHOICES
from ..contracts import ExecutorRegistry
from ..ops.pixel import PALETTE_CHOICES

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
CLAUDE_TIMEOUT = {"_timeout_s": _int(300, min=10, label="Giới hạn thời gian (giây)", advanced=True)}
COMFY_OPTIONS: dict[str, P] = {
    "_free_before": _bool(False, label="Giải phóng VRAM trước khi chạy", advanced=True),
    "_restart_before": _bool(False, label="Khởi động lại ComfyUI trước khi chạy", advanced=True),
    "_timeout_s": _int(1800, label="Giới hạn thời gian (giây)", advanced=True),
    "_auto_retry": _int(1, min=0, max=3, label="Tự thử lại khi lỗi GPU", advanced=True),
}

VIEW_CHECK: dict[str, P] = {
    "max_aspect": _float(1.35, min=1, max=4, label="Tỉ lệ rộng/cao tối đa so với mặt trước", advanced=True),
    "height_tol": _float(0.15, min=0, max=1, label="Lệch chiều cao tối đa so với mặt trước", advanced=True),
    "min_area": _float(0.02, min=0, max=1, label="Diện tích nhân vật tối thiểu", advanced=True),
    "max_area": _float(0.85, min=0, max=1, label="Diện tích nhân vật tối đa", advanced=True),
    "rot_margin": _float(0.08, min=0, max=1, label="Ngưỡng phát hiện nằm ngang", advanced=True),
    "edge_px": _int(2, min=0, max=64, label="Lề chống cắt mép (px)", advanced=True),
}

# "in"/"out" map port name -> port type (image, image[], mesh, json, text, any); "*" means pass-through.
CATALOG: dict[str, P] = {
    "gate.pick": {"label": "Chọn ảnh", "in": {"images": "image[]"}, "out": {"images": "image[]"},
                  "params": {"k": _int(1, min=0, label="Số ảnh cần chọn (0 = ít nhất 1)"),
                             "enabled": _bool(True, label="Chờ người chọn (tắt = tự chọn ảnh đầu)")}},
    "gate.approve": {"label": "Duyệt kết quả", "in": {"*": "any"}, "out": {"*": "any"},
                     "params": {"enabled": _bool(True, label="Chờ người duyệt (tắt = tự duyệt)")}},
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
                       "params": {"count": _int(8, options=[4, 8, 16], label="Số hướng"),
                                  "normal": _bool(False, label="Ảnh normal map (đảo kênh R khi lật)"),
                                  "optional": _bool(False, label="Bỏ qua khi đầu vào rỗng", advanced=True)}},
    "op.downscale": {"label": "Thu nhỏ", "in": {"images": "image[]"}, "out": {"images": "image[]"},
                     "params": {"factor": _float(2, min=1, max=16, label="Hệ số"),
                                "resample": _enum(RESAMPLE, "box", label="Lấy mẫu"),
                                "normal": _bool(False, label="Ảnh normal map (giữ hậu tố _n)"),
                                "optional": _bool(False, label="Bỏ qua khi đầu vào rỗng", advanced=True)}},
    "op.pixelate": {"label": "Pixel art", "in": {"images": "image[]"}, "out": {"images": "image[]"},
                    "params": {"size": _int(64, min=8, max=1024, label="Cỡ (px)"),
                               "palette_colors": _int(min=2, max=256, label="Số màu"),
                               "outline": _text(label="Màu viền (#rrggbb)")}},
    "op.pixel_art": {"label": "Pixel art (bảng màu chung)", "in": {"images": "image[]", "palette_image": "image"},
                     "out": {"images": "image[]", "palette": "json", "swatch": "image"},
                     "params": {"size": _int(64, min=8, max=512, label="Cỡ khung (px, cạnh dài)"),
                                "factor": _float(min=1, max=32, label="Hoặc chia nhỏ theo hệ số", advanced=True),
                                "colors": _int(16, min=2, max=256, label="Số màu"),
                                "palette": _enum(PALETTE_CHOICES, "auto", label="Bảng màu"),
                                "outline": _text("dark", label="Viền ngoài (none / dark / #rrggbb)"),
                                "dither": _enum(["none", "bayer"], "none", label="Dither"),
                                "dither_strength": _float(0.6, min=0, max=2, label="Độ mạnh dither", advanced=True),
                                "alpha_threshold": _int(128, min=1, max=255, label="Ngưỡng alpha", advanced=True),
                                "cleanup": _bool(True, label="Dọn pixel lẻ", advanced=True)}},
    "op.sprite_fx": {"label": "Bóng đổ & viền sprite", "in": {"images": "image[]", "pivot": "json"},
                     "out": {"images": "image[]"},
                     "params": {"shadow": _enum(["none", "drop", "blob"], "none", label="Bóng đổ"),
                                "shadow_color": _text("#000000", label="Màu bóng (#rrggbb)"),
                                "shadow_opacity": _float(0.45, min=0, max=1, label="Độ đậm bóng"),
                                "offset": {"type": "list", "label": "Lệch bóng [x, y] (px)", "advanced": True},
                                "blur": _float(1.5, min=0, max=16, label="Làm mờ bóng (px)", advanced=True),
                                "blob_scale": _float(0.8, min=0.1, max=2, label="Cỡ bóng tròn (× bề ngang)",
                                                     advanced=True),
                                "blob_squash": _float(0.3, min=0.05, max=1, label="Độ dẹt bóng tròn", advanced=True),
                                "outline": _text("none", label="Viền ngoài (none / #rrggbb)"),
                                "width": _int(1, min=1, max=4, label="Độ dày viền (px)"),
                                "pixel": _bool(False, label="Pixel art (cạnh cứng, không mờ)"),
                                "normal": _bool(False, label="Ảnh normal map (bỏ bóng, viền normal phẳng)"),
                                "optional": _bool(False, label="Bỏ qua khi đầu vào rỗng", advanced=True)}},
    "op.sheet": {"label": "Ghép sprite sheet", "in": {"images": "image[]", "pivot": "json"},
                 "out": {"sheet": "image", "meta": "json"},
                 "params": {"preset": {"type": "preset", "label": "Preset xuất"},
                            "cols": _int(min=1, max=64, label="Số cột"),
                            "cell": _int(min=8, max=4096, label="Cỡ ô (px)"),
                            "padding": _int(0, min=0, max=64, label="Khoảng cách"),
                            "pivot": _enum(["", "bottom"], "", label="Pivot"),
                            "order": {"type": "list", "label": "Góc của từng khung"},
                            "asset": _text(label="Tên asset (đặt tên khung)"),
                            "normal": _bool(False, label="Sheet normal map"),
                            "optional": _bool(False, label="Bỏ qua khi đầu vào rỗng", advanced=True)}},
    "op.seamless": {"label": "Làm liền mạch (tileable)", "in": {"images": "image[]"}, "out": {"images": "image[]"},
                    "params": {"name": _text("texture", label="Tên texture"),
                               "flatten": _float(0.5, min=0, max=1, label="San phẳng ánh sáng"),
                               "match_edges": _bool(False, label="Biến thể chung mép với ảnh đầu (tileset)"),
                               "mode": _enum(["edge", "wang"], "edge", label="Tileset: chung mép / Wang góc"),
                               "group": _int(1, min=1, max=16, label="Số ảnh mỗi vật liệu (Wang lấy ảnh đầu mỗi nhóm)",
                                             advanced=True),
                               "grid": _enum(["auto", "off"], "auto",
                                             label="Căn theo chu kỳ (gạch, ván): tự nhận / tắt"),
                               "overlap": _float(0.125, min=0.02, max=0.3,
                                                 label="Vùng chồng nối mép (tỉ lệ cạnh, ảnh nhỏ đi bấy nhiêu)",
                                                 advanced=True),
                               "edge_overlap": _float(0.08, min=0.02, max=0.3, label="Vùng nối khi chung mép",
                                                      advanced=True),
                               "feather": _int(3, min=0, max=16, label="Độ mềm đường cắt (px)", advanced=True)}},
    "op.tile_wang": {"label": "Bộ Wang góc 16 ô (2 vật liệu)", "in": {"images": "image[]"},
                     "out": {"images": "image[]", "nmap": "image[]", "atlas": "image[]", "wang": "json"},
                     "params": {"mode": _enum(["wang", "edge"], "wang", label="Chế độ (edge = bỏ qua)"),
                                "size": _int(512, min=16, max=2048, label="Cỡ ô (px)"),
                                "normal": _bool(True, label="Kèm normal map"),
                                "seed": _int(0, min=0, max=1_000_000, label="Seed đường ranh", advanced=True),
                                "softness": _float(0.08, min=0.01, max=0.5, label="Độ mềm ranh giới",
                                                   advanced=True)}},
    "op.tile_resize": {"label": "Đổi cỡ texture (giữ liền mạch)", "in": {"images": "image[]"},
                       "out": {"images": "image[]", "sizes": "image[]"},
                       "params": {"size": _int(1024, min=16, max=4096, label="Cỡ (px, 0 = giữ nguyên)"),
                                  "levels": _int(2, min=0, max=6, label="Số bản nhỏ thêm (½, ¼…)"),
                                  "square": _bool(False, label="Ép vuông (size × size)")}},
    "op.tile_normal": {"label": "Normal map từ độ sáng (liền mạch)", "in": {"images": "image[]", "nmap": "image[]"},
                       "out": {"images": "image[]"},
                       "params": {"enabled": _bool(True, label="Bật"),
                                  "strength": _float(4.0, min=0.1, max=32, label="Độ nổi"),
                                  "blur": _int(1, min=0, max=16, label="Làm mịn độ cao (px)", advanced=True),
                                  "green": _enum(["opengl", "directx"], "opengl", label="Kênh G",
                                                 advanced=True)}},
    "op.tile_pixel": {"label": "Pixel art texture (liền mạch)", "in": {"images": "image[]"},
                      "out": {"images": "image[]", "palette": "json"},
                      "params": {"size": _int(0, min=0, max=512, label="Cỡ pixel (px, 0 = tắt)"),
                                 "colors": _int(16, min=2, max=256, label="Số màu"),
                                 "palette": _enum(PALETTE_CHOICES, "auto", label="Bảng màu"),
                                 "dither": _enum(["none", "bayer"], "none", label="Dither")}},
    "op.tile_check": {"label": "Chấm điểm mối nối + xem lát", "in": {"images": "image[]", "nmap": "image[]",
                                                                    "pixel": "image[]", "sizes": "image[]",
                                                                    "atlas": "image[]", "wang": "json"},
                      "out": {"images": "image[]", "preview": "image", "report": "json", "atlas": "image[]",
                              "nmap": "image[]", "pixel": "image[]", "sizes": "image[]", "wang": "json"},
                      "params": {"min_score": _float(70, min=0, max=100, label="Điểm đạt tối thiểu"),
                                 "grid": _int(3, min=2, max=8, label="Lát xem trước (n×n)"),
                                 "preview_px": _int(1536, min=256, max=4096, label="Cỡ ảnh xem trước", advanced=True),
                                 "fail": _bool(False, label="Báo lỗi khi dưới điểm đạt")}},
    "op.glb_validate": {"label": "Kiểm tra GLB", "in": {"mesh": "mesh"}, "out": {"meshes": "mesh"},
                        "params": {"require_textures": _bool(True, label="Bắt buộc texture"),
                                   "max_triangles": _int(min=0, label="Tối đa tam giác"),
                                   "max_bytes": _int(min=0, label="Tối đa dung lượng (byte)")}},
    "op.view_check": {"label": "Kiểm tra góc nhìn", "in": {"front": "image", "images": "image[]"},
                      "out": {"images": "image[]", "report": "json"},
                      "params": {"names": {"type": "list", "label": "Tên từng góc"},
                                 "fail": _bool(False, label="Báo lỗi khi có góc hỏng"), **VIEW_CHECK}},
    "views.qwen_checked": {
        "label": "Nhiều góc có kiểm tra (Qwen)", "in": {"image": "image"},
        "out": {"images": "image[]", "report": "json"},
        "params": {"views": {"type": "json", "label": "Các góc (name, output, prompt_key, prompt, slot)"},
                   "workflow": _text("multi_angles_qwen2511", label="Workflow sinh góc", advanced=True),
                   "attempts": _int(3, min=1, max=6, label="Số lần thử mỗi góc"),
                   "on_fail": _enum(["drop", "keep", "fail"], "drop", label="Góc vẫn hỏng thì"),
                   "min_views": _int(1, min=0, max=8, label="Số góc tối thiểu"),
                   "retry_prompt": _text(label="Thêm vào prompt khi thử lại", multiline=True, advanced=True),
                   "consistency": _bool(False, label="Claude kiểm tra nhất quán với mặt trước"),
                   "consistency_model": _enum(MODEL_CHOICES, DEFAULT_MODEL, label="Model kiểm tra", advanced=True),
                   **VIEW_CHECK, **COMFY_OPTIONS}},
    "mesh.checked": {
        "label": "Dựng mesh có kiểm tra", "in": {"image": "image[]"}, "out": {"meshes": "mesh", "report": "json"},
        "params": {"workflow": _text("img2mesh_trellis2_mv_textured", label="Workflow dựng mesh", advanced=True),
                   "attempts": _int(3, min=1, max=6, label="Số lần thử"),
                   "min_iou": _float(0.75, min=0, max=1, label="Độ khớp silhouette tối thiểu"),
                   "on_fail": _enum(["best", "fail"], "best", label="Vẫn không khớp thì"),
                   "azimuths": {"type": "json", "label": "Góc của từng ảnh vào (key → độ)", "advanced": True},
                   "check_res": _int(256, min=64, max=1024, label="Cỡ render kiểm tra", advanced=True),
                   **COMFY_OPTIONS},
        "free_params": True},
    "op.parts": {"label": "Tách part (rig 2D)", "in": {"image": "image", "masks": "image[]"},
                 "out": {"meta": "json", "labels": "image", "preview": "image"},
                 "params": {"roles": {"type": "list", "default": ["head", "torso", "arm", "hand", "leg", "cape"],
                                      "label": "Vai trò của từng mask"}}},
    "op.layers": {"label": "Tách layer & khớp", "in": {"image": "image", "labels": "image", "parts": "json",
                                                        "bones": "json"},
                  "out": {"layers": "image[]", "meta": "json", "preview": "image", "holes": "image[]"},
                  "params": {"rig": _enum(["claude", "auto"], "claude", label="Đặt khớp"),
                             "fill": _bool(True, label="Tạo vùng cần vá ở khớp"),
                             "overlap": _float(0.6, min=0.2, max=1.5, label="Độ chồng ở khớp"),
                             "tolerance": _float(0.06, min=0, max=0.5, label="Dung sai khớp", advanced=True),
                             "inpaint": {"type": "list", "label": "Part cần vá",
                                         "default": ["torso", "upper_arm_l", "upper_arm_r", "thigh_l", "thigh_r"]}}},
    "op.spine_export": {"label": "Xuất Spine 4.2", "in": {"layers": "image[]", "meta": "json", "holes": "image[]",
                                                           "fills": "image[]", "image": "image"},
                        "out": {"skeleton": "json", "atlas": "text", "texture": "image", "preview": "image",
                                "html": "text", "report": "json", "frames": "image"},
                        "params": {"name": _text("character", label="Tên skeleton"),
                                   "animations": _bool(True, label="Thêm anim (idle, đi, đánh, trúng đòn, nhảy)"),
                                   "swap_sides": _bool(False, label="Ảnh mặt sau (đổi tên trái/phải)"),
                                   "padding": _int(2, min=0, max=32, label="Khoảng cách atlas")}},
    "op.video_frames": {"label": "Trích frame video", "in": {"video": "video"},
                        "out": {"sheets": "image[]", "frames": "image[]", "meta": "json"},
                        "params": {"fps": _float(5, min=0.5, max=12, label="Frame mỗi giây"),
                                   "window": _text("auto", label="Đoạn video (vd 1.6-5.6 hoặc auto)"),
                                   "tile": _text("8x2", label="Lưới contact sheet (cột x hàng)"),
                                   "crop": _enum(["auto", "none"], "auto", label="Cắt sát nhân vật"),
                                   "cell": _int(360, min=120, max=1024, label="Chiều cao ô", advanced=True)}},
    "op.spine_rig_summary": {"label": "Tóm tắt rig Spine", "in": {"skeleton": "json"}, "out": {"summary": "text"},
                             "params": {"limit": _int(3000, min=500, max=20000, label="Số ký tự tối đa",
                                                      advanced=True)}},
    "op.spine_add_anim": {"label": "Thêm anim Spine từ spec", "in": {"skeleton": "json", "spec": "json"},
                          "out": {"skeleton": "json", "anim": "json"},
                          "params": {"name": _text("sway", label="Tên anim")}},
    "op.spine_render_strip": {"label": "Render strip Spine", "in": {"skeleton": "json", "atlas": "file[]"},
                              "out": {"strip": "image"},
                              "params": {"anim": _text("", label="Anim (trống = setup pose)"),
                                         "times": _text("auto", label="Các mốc giây (vd 0,0.5,1) hoặc auto"),
                                         "count": _int(8, min=2, max=16, label="Số mốc khi auto"),
                                         "skin": _text("", label="Skin ghép (phẩy)"),
                                         "height": _int(560, min=200, max=1600, label="Chiều cao ô")}},
    "claude.spine_review_loop": {
        "label": "Claude soát anim Spine", "in": {"skeleton": "json", "atlas": "file[]", "ref": "image[]",
                                                 "spec": "json", "rig": "text", "ref_meta": "json"},
        "out": {"skeleton": "json", "anim": "json", "strip": "image", "strips": "image[]", "spec": "json",
                "review": "json"},
        "params": {"name": _text("sway", label="Tên anim"), "brief": _text(label="Mô tả", multiline=True),
                   "rounds": _int(3, min=1, max=8, label="Số vòng tối đa"),
                   "accept_score": _float(8, min=0, max=10, label="Điểm chấp nhận"),
                   "skin": _text("", label="Skin ghép (phẩy)"),
                   "model": _enum(MODEL_CHOICES, DEFAULT_MODEL, label="Model"), **CLAUDE_TIMEOUT}},
    "codex.image": {"label": "Sinh ảnh (Codex)", "in": {"refs": "image[]"}, "out": {"images": "image[]"},
                    "params": {"prompt": _text(label="Prompt", multiline=True),
                               "count": _int(1, min=1, max=4, label="Số ảnh"),
                               "transparent": _bool(False, label="Nền trong suốt")}},
    "claude.task": {"label": "Claude phân tích", "in": {"images": "image[]"}, "out": {"json": "json", "text": "text"},
                    "params": {"prompt": _text(label="Yêu cầu", multiline=True),
                               "model": _enum(MODEL_CHOICES, DEFAULT_MODEL, label="Model"),
                               "max_turns": _int(4, min=1, max=40, label="Số lượt tối đa"),
                               "system": _text(label="System prompt", multiline=True, advanced=True),
                               "schema": {"type": "json", "label": "JSON schema", "advanced": True},
                               "cache": _bool(False, label="Cho phép dùng lại kết quả"), **CLAUDE_TIMEOUT}},
    "claude.brief": {"label": "Claude viết brief", "in": {}, "out": {"json": "json", "text": "text"},
                     "params": {"idea": _text(label="Ý tưởng", multiline=True),
                                "target": _enum(["generic", "concept", "sprite", "prop"], "generic", label="Mục tiêu"),
                                "style": _text(label="Phong cách"),
                                "model": _enum(MODEL_CHOICES, DEFAULT_MODEL, label="Model"),
                                "cache": _bool(False, label="Cho phép dùng lại kết quả"), **CLAUDE_TIMEOUT}},
    "claude.procedural_3d": {"label": "Claude dựng 3D procedural", "in": {},
                             "out": {"meshes": "mesh", "images": "image[]", "code": "text", "json": "json"},
                             "params": {"idea": _text(label="Ý tưởng", multiline=True),
                                        "style": _text(label="Phong cách"),
                                        "height": _float(1.0, min=0.01, label="Chiều cao"),
                                        "rounds": _int(3, min=1, max=3, label="Số vòng sửa"),
                                        "accept_score": _int(8, min=1, max=10, label="Điểm chấp nhận"),
                                        "views": _int(6, min=1, max=16, label="Số góc render"),
                                        "res": _int(256, options=[128, 256, 512], label="Cỡ render"),
                                        "code_model": _enum(MODEL_CHOICES, DEFAULT_MODEL, label="Model viết code"),
                                        "qa_model": _enum(MODEL_CHOICES, DEFAULT_MODEL, label="Model QA"),
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
                            "out": {"images": "image[]", "normal_frames": "image[]", "meta": "json"},
                            "params": {"count": _int(8, options=[4, 8, 16], label="Số hướng"),
                                       "elevation": _float(30, min=0, max=90, label="Góc nghiêng"),
                                       "res": _int(256, options=[64, 128, 256, 512, 1024], label="Cỡ khung"),
                                       "supersample": _int(2, min=1, max=4, label="Siêu lấy mẫu"),
                                       "mirror": _bool(False, label="Lật gương (5 hướng → 8)"),
                                       "normals": _bool(False, label="Render thêm normal map"),
                                       "engine": _enum(["BLENDER_EEVEE", "CYCLES", "BLENDER_WORKBENCH"],
                                                       "BLENDER_EEVEE", label="Engine"),
                                       "lighting": _enum(["studio", "flat"], "studio", label="Ánh sáng"),
                                       "margin": _float(1.05, min=1, max=2, label="Lề khung", advanced=True),
                                       **TIMEOUT}},
    "blender.animate_dirs": {"label": "Dựng xương + anim (đi/đứng/chạy/đánh)",
                             "in": {"mesh": "mesh", "joints": "json", "front": "json"},
                             "out": {"images": "image[]", "normal_frames": "image[]", "meta": "json"},
                             "params": {"anims": _text("walk", label="Anim (walk, idle, run, attack)"),
                                        "frames": _int(8, min=2, max=24, label="Số khung đi bộ"),
                                        **{f"{c}_frames": _int(8, min=2, max=24, label=f"Số khung {c}", advanced=True)
                                           for c in ("idle", "run", "attack")},
                                        "normals": _bool(False, label="Render thêm normal map"),
                                        "count": _int(8, options=[4, 8, 16], label="Số hướng"),
                                        "elevation": _float(30, min=0, max=90, label="Góc nghiêng"),
                                        "res": _int(256, options=[64, 128, 256, 512, 1024], label="Cỡ khung"),
                                        "supersample": _int(2, min=1, max=4, label="Siêu lấy mẫu"),
                                        "engine": _enum(["BLENDER_EEVEE", "CYCLES", "BLENDER_WORKBENCH"],
                                                        "BLENDER_EEVEE", label="Engine"),
                                        "lighting": _enum(["studio", "flat"], "studio", label="Ánh sáng"),
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
                                 "model": _enum(MODEL_CHOICES, DEFAULT_MODEL, label="Model"),
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
    "segment_sam3": "Tách part (SAM 3.1)",
    "inpaint_flux2_klein4b": "Vá ảnh (Flux2 Klein)",
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
