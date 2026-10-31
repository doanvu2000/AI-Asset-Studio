# Đề xuất chức năng lượt 7 (#56–#58) — soạn 2026-10-30, chờ chủ nhân chốt

Ràng buộc giữ nguyên: local (RTX 5070 12GB), không audio, không train LoRA, không cloud. Đã soát 72 recipe hiện có: 3 ý dưới chưa trùng.

| # | Chức năng | Công cụ | Model mới | Effort |
|---|-----------|---------|-----------|--------|
| 56 | Tile động lặp: nước, dung nham, cỏ lay, đuốc, thác → 4–8 frame liền mép + lặp | Wan2.2 TI2V (đã có, #22/#40) hoặc displacement CPU; tileset-seamless | không | M |
| 57 | Icon kho đồ từ model 3D: lô GLB/FBX → icon cùng góc / đèn / viền / nền độ hiếm | Blender headless, #14 rarity, icon-pack | không | S |
| 58 | Màn chơi thử: gom asset của Dự án → scene Godot / Unity chạy được (nhân vật đi 8 hướng, tileset, prop, parallax) | Claude bố cục, Godot 4.7 headless, Unity CLI, exporter có sẵn | không | M–L |

## #56 Tile động lặp
- Vào: 1 tile (hoặc tile từ tileset-seamless/autotile) + loại chuyển động (nước gợn, dung nham, cỏ gió, lửa, thác).
- Nước/dung nham/cỏ: displacement + flow map CPU (rẻ, chắc chắn liền mép). Lửa/thác: Wan2.2 trên ảnh lát 3×3, cắt ô giữa → liền mép; chọn đoạn lặp như #40.
- Ra: sheet frame + Godot `TileSetAtlasSource` animation_columns/frame_duration, Unity `AnimatedTile` (2D Tilemap Extras) hoặc Sprite anim; autotile-47 động nếu đầu vào là blob.
- Kiểm: mép trái/phải + frame cuối→đầu sai lệch thấp (đo như texture-seamless).

## #57 Icon kho đồ từ model 3D
- Vào: thư mục/nhiều GLB (prop-3d, prop-destructible, library-import) + preset góc (3/4 trên, nghiêng 30°), size 128/256/512.
- Blender: framing theo bounding box, đèn 3 điểm cố định, toon/outline tuỳ chọn, nền trong → nền độ hiếm (#14) hoặc khung icon-pack.
- Ra: PNG từng icon + atlas (#30) + Unity Sprite / Godot texture; tên icon = tên model. Cả bộ cùng phối sáng → đồng bộ hơn vẽ AI từng cái.

## #58 Màn chơi thử (playable sandbox)
- Vào: Dự án (hoặc chọn asset): 1 nhân vật 8 hướng/Spine, 1 tileset/autotile, vài prop, parallax/skybox tuỳ chọn.
- Claude xếp màn nhỏ (JSON lưới, kiểu #54/#35) → sinh scene: Godot (TileMapLayer + CharacterBody2D đi 8 hướng + Camera2D) hoặc Unity (Tilemap + script di chuyển).
- Chạy headless chụp vài frame làm preview trong app; nút "Mở trong Godot/Unity" (Unity MCP / plugin #53). Gộp được #35 (ghép màn mẫu + Tiled) vào đây.
- Giá trị: chủ nhân thấy ngay asset đứng chung với nhau trong game (tỉ lệ, palette, pivot) trước khi đổ vào project thật.

## Thứ tự con đề xuất
#57 (rẻ, nhanh) → #56 → #58 (gộp #35).
