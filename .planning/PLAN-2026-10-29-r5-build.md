# PLAN — làm #40–#44 (lượt 5) — 2026-10-29

Chủ nhân: "ok làm hết đi". Spec từng mục: `.planning/PLAN-2026-10-29-feature-ideas-r5.md`.

## Cách chạy
- 5 agent song song, cùng working tree (không worktree, giống lượt #30–#38). Mỗi agent 1 chức năng.
- GPU: khoá `mkdir L:/AI/AI-Asset-Studio/.gpu-lock` trước mọi live run ComfyUI/Blender GPU; khoá đang có → chờ (poll 30s), xong xoá
  khoá mình tạo. Không giết ComfyUI của agent khác.
- File dùng chung (engine/kinds.py, recipe registry, api/routes.py, mcp/server.py, app art/index.ts, BundleMenu, followUp.ts…):
  sửa nhỏ kiểu thêm vào, đọc lại ngay trước khi Edit, không format lại. `composables/changelog.ts` để con chính sửa cuối.
- Không git commit/push. Không build app (`npm run build/dist`). Được chạy `uv run pytest`, `npm run typecheck`.
- Unity kiểm bằng CLI `L:\setup\unity\bin\unity.exe` (Editor 6000.3.27f1, project test `L:\AI\unity-test-batch` Built-in /
  `L:\AI\unity-urp-shadercheck` URP), Godot tự dò (`config.find_godot`).
- Model mới tải vào thư mục models ComfyUI hiện có (F:\work\AI\comfyUI_models\…), ghi license vào `store/model_licenses.py` /
  `bootstrap/manifest.json` theo mẫu sẵn có.

## Phân công
| Agent | # | Recipe | Live GPU | Model mới |
|---|---|---|---|---|
| A | 40 | sprite-anim-video | Wan2.2 TI2V-5B | (Fun-Control 5B tuỳ chọn, chỉ khi rẻ) |
| B | 43 | skybox-360 | Qwen-Image int8 + LoRA 360 | Qwen-Image, LoRA qwen-360-diffusion |
| C | 42 | restyle-folder | Klein 4B | không |
| D | 44 | prop-destructible | Z-Image (texture mặt cắt) | không (Cell Fracture add-on) |
| E | 41 | mesh-retexture | Z-Image + ControlNet Union 2.0 | ControlNet Union 2.0 |

## Xong khi
- Mỗi mục: recipe + op/step + export Unity/Godot + UI lối vào + art thẻ recipe + test; pytest toàn bộ pass; typecheck sạch;
  1 live run thật (hoặc ghi rõ vì sao không); kiểm mở trong Unity/Godot.
- Con chính: changelog 1.1.0 → thêm mục, PROGRESS, handoff.
