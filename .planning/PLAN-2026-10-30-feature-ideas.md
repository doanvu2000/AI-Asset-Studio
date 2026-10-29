# Đề xuất chức năng lượt 4 — soạn 2026-10-29 (chờ chủ nhân chốt)

Ràng buộc giữ nguyên: local, RTX 5070 12GB + 64GB RAM, không train LoRA, không cloud. **Không có audio** (tạm dừng từ
2026-10-29). Ưu tiên ghép những gì đã có: Klein / Z-Image / Qwen edit, Blender, SAM3, BiRefNet, Claude CLI, exporter Unity/Godot.

| # | Chức năng | Công cụ | Model mới | Effort |
|---|-----------|---------|-----------|--------|
| 30 | Texture atlas cho mobile: gom sprite nhiều asset → atlas trim/POT, Unity SpriteAtlas + Godot AtlasTexture, báo dung lượng | CPU | không | S–M |
| 31 | Ngân sách mobile khi xuất: preset nén ASTC/ETC2, max size, mipmap theo loại asset + bảng MB trước/sau | CPU, .meta/.import | không | S |
| 32 | Aseprite: xuất `.aseprite`/JSON sheet có tag clip, nhập ngược sheet Aseprite vào thư viện | CPU | không | S |
| 33 | Biến thể kẻ địch: đổi palette (ramp) + thêm/bớt phụ kiện cho sprite 8 hướng, giữ nguyên khung anim | Klein edit, CPU palette | không | S–M |
| 34 | Biểu cảm mặt Spine: chớp mắt, miệng nói (A/O/M), cau mày — attachment riêng trong slot mặt, anim idle tự chớp | Klein inpaint, Spine export | không | M |
| 35 | Ghép màn mẫu: tileset + autotile + props + parallax → ảnh mockup màn chơi + xuất Tiled `.tmx` / LDtk | Claude (bố cục), CPU | không | M |
| 36 | Isometric 2:1: tile/prop iso từ prompt, lưới chuẩn, pivot chân, sort theo y | Z-Image ControlNet, Blender render iso | không | M |
| 37 | Bộ thẻ bài (TCG/roguelike card): khung + art + chữ + bậc hiếm, xuất PNG từng thẻ + atlas + JSON dữ liệu | Klein, Pillow font (#25), #14 rarity | không | M |
| 38 | Hit VFX gắn khung: đặt VFX flipbook vào frame trúng đòn của anim 8 hướng, xuất event/marker cho Unity/Godot | CPU, VFX có sẵn | không | S–M |
| 39 | Upscale SeedVR2 (bản sau ESRGAN đã ghi ở #7): giữ alpha, tắt cho pixel art | SeedVR2 3B int8 (Apache-2.0, cần spike VRAM) | SeedVR2 | M |

Con đề xuất làm trước những cái ăn ngay vào game mobile, rẻ và không cần model mới: **#30 → #31 → #32 → #33 → #38**, sau đó
#34, #35, #37, #36. #39 spike riêng.

## Chủ nhân chốt (2026-10-29)
- Làm #30 → #31 → #32 → #33 → #38. Agent: (1) #30+#31 export mobile, (2) #32 Aseprite, (3) #33 biến thể kẻ địch, (4) #38 hit VFX.
- Không commit (auto-mode chặn, chủ nhân tự commit). Không build trừ khi chủ nhân bảo.
