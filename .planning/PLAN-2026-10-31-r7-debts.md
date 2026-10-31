# PLAN 2026-10-31 — trả nợ lượt 7 (manager-work-agent)

Chủ nhân chốt: commit từng phần (không push, không trailer), làm cả live GPU. Base: `a76388a`.
Ràng buộc chung: GPU 1 lane — `mkdir L:\AI\AI-Asset-Studio\.gpu-lock` trước khi chạy GPU, xoá đúng khoá mình khi xong;
Portable data chỉ đọc; không audio/TTS, không train LoRA, không cloud; Unity 6000.3.27f1; không build app/dist.
File dùng chung (`engine/kinds.py`, `app/.../changelog.ts`, `gen_agent_docs`) → agent ghi đề xuất vào báo cáo, main sửa.

## Đợt 1 (song song)
### WP-1: Unity/Godot script dùng chung
- Godot `class_name` trùng khi nhập nhiều bản (npc_data.gd, card_set_data.gd, asset_studio_*.gd): xử lý tương đương `copy_unity_export` cho Godot (MCP export_to_engine godot + plugin Godot dock) hoặc bỏ class_name.
- Compile thử `StudioRuns.cs` (SharedScripts) trong Unity thật; kiểm Spine physics #51 (script cũ mất theo scratchpad → viết lại).
- Phạm vi: export/godot*.py, export/{npc,card_set,hitfx,paperdoll,parallax,destructible}.py phần Godot, mcp/server.py export_to_engine, integrations/godot, integrations/unity.
- Test: `pytest core/tests/test_unity_shared_scripts.py core/tests/test_pack.py core/tests/test_npc*.py`.
### WP-2: playable-scene
- Sheet không có walk → không trượt (dùng idle + bob hoặc khoá di chuyển có cảnh báo); tường có chiều sâu (cạnh/bóng); mở .tmx/.ldtk bằng parser chuẩn (pytmx/ldtk schema hoặc Tiled/LDtk CLI nếu có).
- Phạm vi: ops/playable_scene.py, export/playable_scene.py, adapters/scene_layout.py, recipes/playable-scene.yaml, tests/test_playable_scene.py.
### WP-3: tile-animated (GPU)
- Torch lệch tông mép (seam x 1.98 sát ngưỡng) → khớp màu mép; thử tile Z-Image thật; scene demo Godot.
- Phạm vi: ops/tile_anim.py, export/tile_anim.py, recipes/tile-animated.yaml, tests/test_r7_tile_anim.py.
### WP-4: decal + preview engine 3D (GPU)
- Decal cracks mảnh; preview.gd 3D hạt (GPUParticles3D) trống; preview Unity URP cho modular-kit.
- Phạm vi: ops/decal.py, recipes/decal-pack.yaml, workflows decal, export/engine_scripts/*, export/preview.py.

## Đợt 2 (sau đợt 1)
- WP-5 spine-face: mặt bị che → fallback thay vì op_no_face cứng; mày dưới tóc viền mờ. (ops/spine_face.py)
- WP-6 card-set: chữ bitmap font/TMP, nhánh Klein live. (ops/card_set.py, export/card_set.py)
- WP-7 icon-3d: input folder rỗng/≥1 file, TRELLIS toon phẳng. (ops/icon3d.py, adapters/blender/icon3d*)
- WP-8 ui-screen icon Z-Image live + SeedVR2 ảnh >1024. (ops/ui_screen.py, adapters/comfy/upscale.py)

## Cuối
- Main: changelog 1.2.x, gen_agent_docs --check, pytest toàn bộ, HANDOFF.
