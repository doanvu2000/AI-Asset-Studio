# Đề xuất chức năng mới — 2026-10-29

Nguồn: 2 subagent nghiên cứu (thị trường/model mới + khoảng trống workflow). Ràng buộc: local, RTX 5070 12GB +
64GB RAM, không train LoRA, không cloud. Ưu tiên ghép công cụ sẵn có: Claude CLI, Codex CLI, ComfyUI + workflow đã
build, Unity/Blender/Figma(Pro)/GitHub/GitLab MCP. Chờ chủ nhân duyệt.

| # | Chức năng | Công cụ | Effort |
|---|-----------|---------|--------|
| 1 | "Dùng tiếp cho…" — nối output sang recipe khác 1 chạm; "Bộ nhân vật" concept → 8 hướng → Spine | app thuần | S |
| 2 | Manifest asset của game: dán GDD → Claude tách bảng (tên, recipe, prompt) → lô nhiều recipe, theo dõi trạng thái | Claude CLI, (GitHub/GitLab issue) | M |
| 3 | UI kit + icon pack + 9-slice, đồng bộ Figma (lấy khung, đẩy component lại) | Z-Image ControlNet (có workflow chưa dùng), Klein, Figma MCP | M–L |
| 4 | "Đặt vào Unity": import + prefab + scene preview + chụp Game view làm cổng duyệt; Godot headless | Unity MCP, Claude CLI | M |
| 5 | QA đồng bộ cả Dự án (palette, tỉ lệ, nét, hướng sáng) + sửa 1 chạm | CPU, Claude vision, Klein | M |
| 6 | Phát hành asset pack lên GitHub/GitLab (UPM/Godot addon, manifest nguồn gốc, CHANGELOG) — chỉ khi bấm | GitHub/GitLab MCP | M |
| 7 | Upscale/Remaster HD (giữ alpha, tắt cho pixel art) | SeedVR2 3B int8 (Apache 2.0) / ESRGAN | S |
| 8 | Thoại NPC / TTS từ CSV (OGG + JSON phụ đề), dùng lại hậu kỳ audio | VieNeu-TTS (tiếng Việt, CPU) / Chatterbox | S–M |
| 9 | Vật liệu PBR từ texture (rough/metal/height) + material Unity/Godot | heuristic CPU (CHORD chỉ research-only) | S |
| 10 | Nhân vật 3D có rig + anim retarget mocap, xuất GLB/FBX | Blender MCP (joint Claude đã có), BVH CMU | M |
| 11 | Tách ảnh thành layer RGBA / PSD, đầu vào sạch cho auto-cut Spine | Qwen-Image-Layered (Apache 2.0, cần spike VRAM) | M |

Phụ: bộ biểu cảm mặt Spine (chớp mắt/nói bằng Klein inpaint), xuất/nhập JSON Aseprite.

Đã loại: Hunyuan3D-2.1 Paint (21GB), Puppeteer (license), VibeVoice, CHORD thương mại.

Gợi ý thứ tự: 1 → 7 → 2 → 4 → 3 → 5 → 9 → 8 → 6 → 10 → 11.

## Bổ sung (bản sửa của agent thị trường)
- #12 Gói NPC: 6–8 chân dung biểu cảm giữ khuôn mặt (Klein edit) + Claude viết thoại theo tính cách + TTS (CPU, không
  tranh VRAM) → Unity ScriptableObject / Godot Resource. Gộp được với #8. Effort M.
- #3/#10: xuất nhân vật 3D có rig dùng lại chuỗi `sprite-8dir-anim` (build_armature/bind_mesh/clip_pose) → bake NLA
  action, đổi tên xương chuẩn Humanoid, GLB/FBX. Không cần model mới.
- Lưu ý #4: phiên này không thấy tool Unity MCP (chỉ Blender, Figma, GitHub, GitLab) → cần chủ nhân cấu hình Unity MCP
  trước khi làm "Đặt vào Unity"; tạm thời Godot headless làm được ngay.

## Chủ nhân chốt (2026-10-29)
- Làm: #1 (có Bộ nhân vật), #7 (ESRGAN trước, SeedVR2 sau), #2 (không cần issue GitHub), #3 (chưa cần Figma),
  #5, #9, #8+#12 (cả gói NPC + TTS), #10, #4 (Godot + Unity; Unity qua batchmode `F:\Unity\6000.3.6f1\Editor\Unity.exe`,
  bật Unity MCP nếu cần), #6 chỉ đóng gói local (không đẩy remote), #11 spike trước rồi báo.
- Đợt 1 (song song): #1 app · #7 upscale · #2 manifest GDD · #3 UI kit.
- Đợt 2: #9 PBR · #5 QA dự án · #10 rig 3D · #4 engine preview.
- Đợt 3: #8+#12 NPC/TTS · #6 pack local · #11 spike.

## Lượt nghiên cứu 2 (2026-10-29, chờ chủ nhân chốt)
| # | Chức năng | Công cụ | Model mới | Effort |
|---|-----------|---------|-----------|--------|
| 13 | Auto Collider + pivot chân (Unity physicsShape, Godot CollisionPolygon2D/Occluder) | CPU | không | S |
| 14 | Bậc độ hiếm vật phẩm (5 bậc, hue/LUT + Klein + glow, ramp palette) | Klein, fx | không | S |
| 15 | Ngày/đêm/mùa + LUT .cube cho tileset/nền | Klein, CPU | không | S |
| 16 | Âm thanh khớp cảnh (ảnh → nhạc + ambience + SFX) | Claude vision, ACE-Step, Stable Audio | không | S |
| 17 | Hư hại 4 trạng thái + mảnh vỡ | Klein chuỗi, SAM3 | không | S–M |
| 18 | Shader preset sprite (hit flash, dissolve, wind…) từ template | Codex, Godot/Unity batchmode | không | S–M |
| 19 | Nền parallax 4–5 lớp + Parallax2D | Z-Image, BiRefNet, seamless 1 trục | (Depth Anything V2 Small) | M |
| 20 | Bộ store + icon app (iOS/Android/Steam sizes, Figma frame) | Klein, Z-Image, Claude, Figma MCP | không | M |
| 21 | Bản địa hoá chữ trong ảnh | Claude vision, Klein inpaint, Pillow font | không | M |
| 22 | Key art động / teaser 5s | Wan2.2 TI2V-5B, ffmpeg | Wan2.2-5B | M–L |
