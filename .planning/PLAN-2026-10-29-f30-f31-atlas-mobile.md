# #30 Atlas mobile + #31 Ngân sách mobile (agent, 2026-10-29)

## #30 `export/atlas.py`
- Gom sprite từ `engine_source` từng asset: ô sheet (pivot theo layout, pivot chân khi bật collider), UI (giữ nguyên
  không trim nếu có viền 9-slice), rarity, damage, NPC portrait, localized, image layers; asset không có gì → ảnh PNG của step.
- MaxRects (BSSF), trim alpha, padding + extrude, POT, max page 2048, xoay tắt (chỉ cho JSON), trùng ảnh → dùng chung rect.
- Ra: trang PNG + JSON (kiểu TexturePacker hash, có pivot); Unity: trang Sprite Mode Multiple, rect + pivot từng sprite trong
  .meta (+ physics shape dời theo trim); Godot: AtlasTexture `.tres` từng sprite (region + margin giữ kích thước gốc, metadata pivot).
- Báo byte/GPU trước–sau. Lối vào: pack (`atlas`), Gallery chọn nhiều → `POST /api/atlas`, MCP `build_asset_pack(atlas=)`.

## #31 `export/mobile.py`
- Preset theo loại (sprite/ui/texture/tileset/normal/pixel): max size, ASTC block (Unity override Android + iPhone),
  Godot `.import` (VRAM/lossless, mipmap, size_limit). Bảng GPU trước/sau + dung lượng tệp → README + `mobile_budget.json`.
- Mặc định TẮT, nhớ lựa chọn: Godot VRAM đổi cả bản desktop và nén mất mát với UI/pixel art.

## Kiểm
- pytest; Unity batchmode `unity-test-batch` (import settings + sprite atlas render); Godot headless load `.tres`/`.import`.
