# Đề xuất chức năng lượt 6 — soạn 2026-10-29 (chờ chủ nhân chốt)

Đã có (không lặp): #1–#44 (61 recipe trong 1.1.0). Còn treo lượt 4: #34 biểu cảm Spine, #35 ghép màn, #36 isometric,
#37 thẻ bài, #39 SeedVR2.

Ràng buộc giữ nguyên: local, RTX 5070 12GB + 64GB RAM, không train LoRA, không cloud, **không audio**, Unity 6.3 LTS + Godot 4.
Hướng lượt này: lấp mảng **3D môi trường** (mới có prop/skybox), **VFX chạy thật trong engine** (mới có flipbook), và
**đưa studio vào quy trình game có sẵn** (audit project, plugin editor).

| # | Chức năng | Công cụ dùng lại | Model mới | Effort |
|---|-----------|------------------|-----------|--------|
| 45 | Trim sheet + decal 3D | Z-Image, texture-seamless, pbr-material, Blender | không | S–M |
| 46 | Kit modular 3D theo lưới | Blender procedural, #41 retexture, #45 trim | không | M |
| 47 | Terrain 3D: heightmap + splat + texture lớp | CPU noise, Claude bố cục, Z-Image, pbr | không | M |
| 48 | VFX particle thật: texture pack + prefab ParticleSystem / GPUParticles, đạn + trail | Z-Image/Klein, CPU, vfx-flipbook | không | M |
| 49 | Item xoay + UI juice loop (coin/gem spin, glow, shine) | Blender turntable, op.sheet, CPU | không | S |
| 50 | Màn UI ghép sẵn: ui-kit → prefab UGUI/UITK + Godot Control scene | ui-kit, icon-pack, bitmap-font, Claude bố cục | không | M–L |
| 51 | Spine physics: tóc/áo choàng/đuôi tự lắc | spine-character, Spine 4.2 physics constraint | không | S |
| 52 | Audit project game có sẵn + sửa 1 chạm | #31 mobile budget, #28 tìm trùng, #42 restyle | không | S–M |
| 53 | Plugin trong Unity/Godot gọi studio-core | API core sẵn có, MCP asset-studio | không | M |
| 54 | Minimap / bản đồ thế giới từ màn chơi | #35 (nếu làm), Klein, icon-pack | không | S–M |

## Chi tiết

### #45 Trim sheet + decal 3D
- Vấn đề: môi trường 3D cần trim sheet (dải viền, gờ, ống, đinh tán xếp theo hàng) và decal (nứt, rêu, vết bẩn, logo) — #9 chỉ làm 1 texture tile.
- Cách: Claude chọn bố cục dải (chiều cao theo bội 2: 32/64/128/256 px), Z-Image sinh từng dải seamless ngang → ghép atlas 2048,
  pbr-material sinh normal/rough/height. Decal: sinh nền trong suốt (BiRefNet) + normal.
- Xuất: JSON vị trí dải (UV v0/v1) để Blender/modeller map; Unity URP Decal Projector prefab, Godot `Decal` node; material trim URP/Godot.
- Nghiệm thu: mép dải seamless ngang (seam score như #43), Blender script đặt UV vào 1 cube bevel đúng dải.

### #46 Kit modular 3D theo lưới
- Sinh bộ tường / sàn / góc / cửa / cầu thang / cột snap lưới 2m (hoặc 1m), pivot góc, kích thước đúng bội lưới.
- Blender procedural (đã có procedural-3d) dựng hình khối → UV theo trim sheet #45 hoặc #41 retexture.
- Xuất FBX/GLB từng mảnh + prefab Unity (snap setting) + Godot `MeshLibrary` cho GridMap; ảnh mockup ghép phòng mẫu.
- Nghiệm thu: mọi bounds là bội lưới ±0.5mm, ghép 2 tường liền không hở (kiểm cạnh trùng).

### #47 Terrain 3D
- Input prompt ("đảo nhiệt đới, núi phía bắc, sông giữa") → Claude ra bản đồ vùng thô → CPU tạo heightmap 16-bit (noise + erosion nhẹ) theo vùng,
  splat map theo độ cao/độ dốc, 4 lớp texture từ texture-seamless + pbr.
- Unity: editor script dựng `TerrainData` (.asset) + TerrainLayer; Godot: mesh chia chunk + shader splat 4 lớp (không cần addon).
- Đi kèm skybox #43 cùng prompt → cảnh 3D trọn gói.
- Nghiệm thu: preview Unity/Godot chụp được, raw 16-bit không bậc thang, splat cộng = 1.

### #48 VFX particle thật
- Flipbook (#5) nặng và cứng; game mobile cần particle texture nhỏ (soft dot, spark, smoke puff, ring, slash arc) + emitter.
- Sinh texture pack 64–256px (Z-Image/Klein + alpha), preset emitter (nổ, hồi máu, nhặt đồ, đạn + trail, bụi chân) dạng JSON trung lập.
- Xuất: Unity prefab `ParticleSystem` (+ TrailRenderer cho đạn), Godot `GPUParticles2D/3D` + `ParticleProcessMaterial` .tres.
- Preview: quay GIF trong engine (dùng lại engine preview). Nối #38: hit-FX dùng preset particle thay flipbook.

### #49 Item xoay + UI juice loop
- Coin/gem/chìa khoá: mesh (prop-3d) hoặc ảnh 2D → Blender turntable 12–24 frame loop → sheet + anim Unity/Godot.
- 2D thuần: shine sweep, pulse glow, bob — CPU sinh frame hoặc xuất shader preset (#18).
- Rẻ, dùng ngay cho mobile (shop, phần thưởng). Nghiệm thu: frame cuối nối frame đầu, pivot đứng yên (như #40).

### #50 Màn UI ghép sẵn
- Hiện ui-kit/icon-pack ra mảnh rời; dev vẫn phải tự lắp. Input: loại màn (Home, Shop, Level select, Pause, Kết quả thắng/thua) + style dự án.
- Claude ra layout JSON (anchor, safe area, 9-slice, font) → mockup PNG để duyệt → xuất Unity UGUI prefab (Canvas Scaler 1080×1920)
  hoặc UI Toolkit UXML/USS, Godot Control `.tscn` (container + theme).
- Tuỳ chọn đẩy frame sang Figma (#3 đã có đường Figma).
- Nghiệm thu: mở prefab không missing sprite, đổi tỉ lệ 16:9/20:9/tablet không tràn safe area.

### #51 Spine physics
- Spine 4.2 có physics constraint. Sau auto-rig: Claude/heuristic nhận bone tóc, áo choàng, đuôi, dây (theo tên part + chuỗi bone)
  → thêm chain bone + physics constraint (inertia/strength/damping preset: tóc nhẹ, áo nặng).
- Không GPU. Nghiệm thu: Spine runtime (spine-unity/spine-godot) chạy walk thấy lắc, không nổ khi đổi hướng.
- Lưu ý: cần bản Spine runtime ≥ 4.2 trong project test.

### #52 Audit project game có sẵn
- Trỏ vào project Unity/Godot → quét: texture quá to so với kích thước hiển thị, chưa nén/không POT, sprite trùng (SigLIP #28),
  asset không ai tham chiếu (GUID/res:// graph), audio bỏ qua, mesh quá tris cho mobile.
- Báo cáo MB trước/sau + nút sửa 1 chạm (dùng #31 mobile preset, có backup + hoàn tác như #42).
- Giá trị: kéo cả user chưa sinh asset bằng studio. Nghiệm thu: chạy trên 3 project test, sửa xong build size giảm, 0 missing ref.

### #53 Plugin trong Unity/Godot
- Unity package (EditorWindow) + Godot addon (dock) gọi API core local: chọn recipe, nhập prompt, xem tiến trình, duyệt gate,
  asset về thẳng `Assets/` / `res://` đúng thư mục.
- Chuột phải asset có sẵn → "Restyle / Tô lại / Biến thể" (#42/#41/#33).
- Dùng lại exporter sẵn có; chỉ thêm lớp client. Phát hành qua UPM git URL + Godot AssetLib zip.

### #54 Minimap / bản đồ thế giới
- Từ ảnh màn (#35) hoặc tilemap `.tmx` → minimap stylized (đơn giản hoá màu, viền, icon POI), bản đồ thế giới kiểu giấy cũ (Klein restyle).
- Xuất ảnh + JSON toạ độ POI, Unity RawImage + mask tròn prefab, Godot SubViewport mẫu.
- Phụ thuộc mềm #35; không có #35 thì nhận ảnh nền bất kỳ + chấm POI tay.

## Thứ tự con đề xuất
1. **#49** (S, ăn ngay mobile) → **#51** (S, CPU) → **#52** (kéo user mới, rẻ) → **#48** (VFX thật, nối #38).
2. Sau đó mảng 3D môi trường: **#45 → #46 → #47** (phụ thuộc nhau theo thứ tự).
3. **#50**, **#53** lớn hơn, làm khi core ổn; **#54** sau #35.

Không cần model mới cho cả lượt → không thêm rủi ro license/VRAM.
