# Brainstorm chức năng mới (2026-10-28)

Nguồn: agent khảo sát web (Ludo, PixelLab, Scenario, Layer, Meshy, Tripo, Rodin, Retro Diffusion, ComfyUI audio) +
đối chiếu năng lực hiện có của app. Tìm kiếm nông, mục ghi "(kb)" là kiến thức chưa xác minh nguồn.

## Ưu tiên đề xuất

| # | Chức năng | Vì sao | Cách làm local | GPU |
|---|-----------|--------|----------------|-----|
| 1 | **3D game-ready**: retopo quad theo poly budget, LOD 0-2, bake normal/AO từ mesh TRELLIS | TRELLIS ra mesh dày, chưa dùng thẳng trong game; Tripo/Rodin có Smart Low-poly | Blender (QuadriFlow / decimate + bake) qua Blender automation sẵn có | CPU |
| 2 | **Âm thanh**: SFX ngắn (bước chân, hit, UI click) + BGM lặp | Đối thủ đã gói audio; app chưa có gì | Stable Audio Open 1.0 (SFX ≤47s), ACE-Step (nhạc) trên ComfyUI; cắt điểm loop + crossfade | 8–12GB |
| 3 | **Train LoRA style theo Dự án** từ 5–20 ảnh | "Khoá style" hiện chỉ là prompt; Scenario/Layer bán chính tính năng này | ai-toolkit/kohya cho Flux.2 Klein / Z-Image; dự án gắn LoRA vào mọi recipe | 16–24GB train |
| 4 | **Thư viện anim Spine có sẵn**: attack, hit, death, idle variants, chuyển tiếp mix | Nối tiếp G2: walk/run/jump đã có, thiếu one-shot chiến đấu | Preset sinh key trên `spine_cycle`/`keys` + Claude chỉnh theo video; không cần GPU | CPU |
| 5 | **VFX flipbook** (nổ, chém, khói, phép) nền trong, loop | Thiếu ở hầu hết đối thủ (kb) | Wan 2.2 / LTX video → tách nền → cắt loop → op.sheet sẵn có | 16GB |
| 6 | **Skin/biến thể Spine**: đổi màu, đổi trang phục trên atlas có sẵn | Rig một lần, nhiều nhân vật; tận dụng auto-cut + Klein | Klein edit từng region atlas, giữ kích thước/pivot, xuất skin mới | 10–12GB |

## Ứng viên khác

- **2D**: bộ icon/UI cùng style + xuất 9-slice; autotile 47-blob (ngoài Wang 16) + decor khớp tileset; tách ảnh
  thành layer RGBA (giúp auto-cut Spine tốt hơn; Qwen-Image-Layered, chưa xác minh); pixel hoá ảnh có sẵn với palette
  khoá + snap pixel thật.
- **Anim**: tư thế theo skeleton cho sprite 8 hướng (ControlNet OpenPose); retarget BVH/FBX vào Spine hoặc mesh
  (thư viện motion kiểu Mixamo); khẩu hình/biểu cảm Spine theo audio thoại.
- **3D**: auto-rig mesh (UniRig / Rigify) + anim preset; PBR (albedo/normal/roughness/metal/AO).
- **Khác**: TTS thoại NPC (Kokoro / F5-TTS, CPU được); foley tự khớp clip anim (MMAudio, kb).
- **Workflow**: plugin Godot/Unity tự import thư mục xuất; plugin Aseprite (PixelLab có); thư viện tìm theo nghĩa
  (CLIP) + metadata nguồn gốc/giấy phép (sidecar/C2PA), hàng đợi batch.

## Chốt (2026-10-28)
Chủ nhân duyệt cả 6 mục. GPU ≤12GB VRAM → chọn model vừa 12GB (offload/fp8 khi cần).

Thứ tự (rủi ro VRAM tăng dần):
1. #1 3D game-ready — CPU/Blender.
2. #4 Anim Spine sẵn (attack/hit/death/chuyển tiếp) — CPU.
3. #6 Skin/biến thể Spine — Flux.2 Klein (đang chạy được trên máy).
4. #2 Âm thanh — Stable Audio Open (~6–8GB) cho SFX, ACE-Step (~8GB, offload) cho BGM.
5. #5 VFX flipbook — Wan 2.2 TI2V-5B hoặc LTX-Video 2B (vừa 12GB với offload), cắt loop + op.sheet.
6. #3 Train LoRA — rủi ro nhất trên 12GB: ai-toolkit low-VRAM (rank thấp, quantize, gradient checkpointing) cho
   Klein/Z-Image; cần spike đo VRAM/thời gian trước khi hứa.

Mỗi mục: plan riêng `.planning/PLAN-feature-<tên>.md` trước khi code; ghi tiến độ vào PROGRESS.md.
