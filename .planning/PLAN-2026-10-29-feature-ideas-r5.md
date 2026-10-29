# Đề xuất chức năng lượt 5 (#40–#44) — soạn 2026-10-29, chờ chủ nhân chốt

Ràng buộc giữ nguyên: local 1 máy (RTX 5070 12GB + 64GB RAM), không cloud trả phí, không train LoRA, **không audio/TTS**.
License model phải cho thương mại (hoặc ghi rõ rủi ro). Đã đối chiếu #1–#39 (lượt 2026-10-27/28/29/30 + PLAN-feature-3d-gameready)
và 60 recipe hiện có → 5 mục dưới đây **chưa có**: app chưa có skybox/panorama, chưa tô texture cho mesh có sẵn, chưa có
anim 2D vẽ tay khung-theo-khung (anim hiện chỉ qua rig Blender/Spine), chưa thay style hàng loạt tại chỗ trong project
game, chưa có prop 3D phá huỷ.

## Bối cảnh thị trường (tóm tắt)

- GDC State of the Industry 2026: 36% dev dùng genAI, 52% cho rằng genAI hại ngành → người dùng cần công cụ **kiểm soát
  được, chạy local, rõ nguồn gốc/license**, không phải "bấm ra ảnh". [1]
- Ludo.ai (06/2026) thêm Pose Generator, Keyframe Animation (≤3 khung chính → 4–64 frame), Transition/Before/After,
  xuất thẳng Godot SpriteFrames [2][3]; PixelLab bán text→animation từ khung đầu + skeleton [4]. → Anim 2D từ 1 ảnh là
  tính năng đối thủ đang đẩy mạnh; app mình chỉ có đường qua rig.
- Meshy / Tripo bán "Retexture": giữ hình học, tô lại texture theo text/ảnh, có PBR, tuỳ chọn giữ UV gốc [5][6]. Bản
  local mã nguồn mở: StableGen (Blender + ComfyUI, chiếu nhiều view, ControlNet depth/normal, inpaint tuần tự) [7].
- Skybox: Comfy có template "Equirectangular HDR Skybox" (Qwen-Image + SeedVR2) [8]; LoRA qwen-360-diffusion (MIT) [9].

## #40 — Hoạt ảnh sprite từ 1 ảnh (video AI → sprite sheet)

**Giá trị:** 1 ảnh nhân vật (side-view / top-down) + câu mô tả động tác → clip sprite vẽ tay (idle, chém, nhảy, bị đánh)
không cần rig — đúng thứ Ludo/PixelLab bán, nhưng local và nối sẵn vào exporter.

**Luồng UI:** chọn ảnh từ Thư viện ("Dùng tiếp cho… → Hoạt ảnh sprite") → chọn preset động tác hoặc gõ mô tả, số frame
(8/12/16), loop/oneshot, tuỳ chọn video tham chiếu (quay điện thoại) → xem 3 ứng viên → chọn → chỉnh điểm cắt loop
trên timeline → xuất.

**Recipe `sprite-anim-video`:**
1. `op.anim_prep` — trim, pad lên nền phẳng (xám/xanh chroma), 480p/640p, ghi bbox gốc.
2. `claude.task` — viết prompt chuyển động (camera tĩnh, không zoom, nền giữ nguyên).
3. `comfy.i2v_wan22_ti2v` (Wan2.2 TI2V-5B, **đã có** trong key-art-motion) — hoặc khi có video tham chiếu:
   `comfy.wan22_fun_control` (Wan2.2-Fun-Control-5B, OpenPose/Depth từ video) [10][11].
4. `gate.pick` — 3 seed.
5. `op.video_frames` (có sẵn) → `comfy.remove_bg_birefnet` từng frame → `op.anim_loop` (mới: tìm cặp frame giống nhất
   theo SSIM mask, crossfade 1–2 frame, khoá tâm chân/pivot, chống rung bbox) → `op.downscale` / `op.pixel_art` tuỳ chọn.
6. `op.sheet` + `op.sprite_fx` (có sẵn) → `gate.approve`.

**Model / license / VRAM:** Wan2.2 TI2V-5B Apache-2.0 (đã rà); Wan2.2-Fun-Control-5B Apache-2.0 (mới, cần spike) —
~10–12GB ở 480p với offload; BiRefNet MIT.
**Export:** Unity AnimationClip + sprite sheet slice (dùng exporter sheet hiện có); Godot SpriteFrames `.tres`
(AnimatedSprite2D); Aseprite JSON có tag (#32).
**Độ khó:** M. **Rủi ro:** nhân vật biến dạng / đổi chi tiết giữa frame (TI2V 5B), nền đổi màu làm tách nền lem → giảm
bằng nền phẳng + BiRefNet + gate; loop không khớp → `op.anim_loop` + cho chỉnh tay.
**Kiểm:** unit `op.anim_loop` với chuỗi frame tổng hợp (vòng tròn chạy → phải tìm đúng chu kỳ, pivot lệch ≤1px);
executor fake Comfy; live 1 lượt chém 12 frame. Godot: mở `.tres` headless, đếm frame + fps; Unity batchmode: clip có
đúng số keyframe sprite.

## #41 — Tô lại texture cho mesh có sẵn (Retexture 3D local)

**Giá trị:** đưa mesh greybox / mesh asset store / mesh TRELLIS → tô texture mới theo style dự án, **giữ nguyên hình
học + UV** (đổi skin kiểu Meshy/Tripo Retexture, nhưng không mất credit).

**Luồng UI:** kéo `.glb/.fbx/.obj` vào → prompt + ảnh style (Dự án tự điền) → chọn số view (4/6/8), giữ UV gốc hay
unwrap mới → xem ảnh 4 view tô thử → duyệt → bake → turntable.

**Recipe `mesh-retexture`:**
1. `blender.views_render` (mới) — đặt camera quanh vật, render depth + normal + mask + ID mặt, UV unwrap nếu thiếu.
2. View chính: `comfy.t2i_zimage_controlnet` (đã có workflow) điều khiển depth + style ref.
3. View còn lại tuần tự: chiếu texture đã có sang view mới → vùng chưa tô thành mask → Z-Image-Turbo-Fun-ControlNet-Union
   **2.0** (depth + inpaint) [12] — kiểu "Sequential mode" của StableGen [7].
4. `blender.project_bake` (mới) — chiếu từng view lên UV, trộn theo góc nhìn (cos θ), lấp lỗ bằng dilate/inpaint UV.
5. `op.pbr` (có sẵn, #9) → normal/rough/AO từ albedo; `blender.cleanup`/`op.glb_validate` (có sẵn).
6. `blender.render_dirs` turntable → `gate.approve`.

**Model / license / VRAM:** Z-Image Turbo + ControlNet Union 2.0 — Apache-2.0, ~8–10GB; không cần model 3D mới
(tránh Hunyuan3D Paint 21GB / license Tencent).
**Export:** GLB có texture; Unity: material URP Lit (base/normal/mask map) qua exporter PBR; Godot: StandardMaterial3D.
Kèm bản gốc để so.
**Độ khó:** L. **Rủi ro:** đường nối giữa view, mặt khuất (đáy, nách) không được tô → thêm view trên/dưới + inpaint UV;
UV gốc chồng lấp (overlap) làm bake sai → cảnh báo + tuỳ chọn unwrap mới.
**Kiểm:** unit trọng số trộn view + mask phủ (100% mặt nhìn thấy có pixel); live trên 1 prop TRELLIS và 1 mesh
greybox Blender (cube/thùng gỗ); đo % texel chưa tô < 2%. Mở trong Unity/Godot: material đủ map, không vỡ UV.

## #42 — Thay style hàng loạt tại chỗ (Restyle placeholder giữ GUID)

**Giá trị:** game prototype đầy placeholder / asset pack lẫn style → chọn thư mục `Assets/` (Unity) hoặc `res://`
(Godot), app tô lại **toàn bộ** theo style bible (#27) mà giữ nguyên kích thước, alpha, pivot, slice và **file .meta /
.import** → scene, prefab, tham chiếu không vỡ. Đối thủ đều làm từng ảnh, không ai ghi đè an toàn vào project.

**Luồng UI:** chọn thư mục project → app quét, nhóm theo loại (sprite, sheet, UI 9-slice, tile) + ước thời gian → chọn
style (Dự án) → chạy thử 6 ảnh → duyệt → chạy cả lô (hàng đợi) → bảng trước/sau → "Áp vào project" (backup zip trước).

**Recipe `restyle-folder`:**
1. `op.scan_project` (mới) — đọc PNG + `.meta` (spriteMode, rect từng sprite, border 9-slice, pivot) / `.import` Godot.
2. `claude.task` (vision) — mô tả từng ảnh 1 dòng (vật gì) để làm prompt; gom nhóm cùng nhân vật để giữ đồng nhất.
3. `comfy.edit_flux2_klein4b` (có sẵn) — ref = ảnh gốc + ảnh style; sheet nhiều frame: xử lý theo hàng/khối, giữ lưới.
4. `op.restyle_fit` (mới) — ép về đúng kích thước, dùng alpha gốc (hoặc BiRefNet nếu silhouette đổi), giữ vùng border
   9-slice, kiểm pivot; `op.sprite_fx` tuỳ chọn.
5. QA dự án (#5) chấm đồng bộ → `gate.approve` theo lô → `op.apply_restyle` (mới) — ghi đè PNG, **không chạm .meta**,
   ghi manifest nguồn gốc.

**Model / license / VRAM:** Flux.2 Klein 4B (Apache-2.0 đã rà), ~8–10GB; không model mới.
**Export:** ghi thẳng vào project (có backup + nút hoàn tác), hoặc ra thư mục song song cùng cấu trúc.
**Độ khó:** M. **Rủi ro:** frame anim lệch nhau sau edit (nhấp nháy) → xử lý nguyên sheet một lần hoặc khoá seed + ref
frame đầu; ghi đè nhầm → backup bắt buộc, dry-run mặc định.
**Kiểm:** unit parser `.meta` (sprite rect, border, pivot) + `op.restyle_fit` giữ đúng kích thước/alpha; test "ghi đè
không đổi byte .meta" (hash trước/sau). Unity batchmode mở project mẫu: scene load, không missing reference, GUID không
đổi; Godot headless `--import` không lỗi.

## #43 — Skybox / panorama 360 cho game 3D

**Giá trị:** prompt → bầu trời / phông 360° liền mép, dùng ngay làm skybox + ánh sáng môi trường cho game 3D; đối thủ 2D
(PixelLab/Ludo) không có, Scenario/Blockade bán theo credit.

**Luồng UI:** gõ mô tả (hoàng hôn sa mạc, vũ trụ tím…) + chọn style dự án → 3 ứng viên xem trong viewer 360 (kéo xoay)
→ chọn → tuỳ chọn upscale 4K, tạo bản ngày/đêm (#15) → xuất.

**Recipe `skybox-360`:**
1. `claude.brief` (có sẵn) — viết prompt có trigger "360 degree panorama with equirectangular projection".
2. `comfy.t2i_qwen_image_360` (mới) — Qwen-Image int8 + LoRA qwen-360-diffusion int8 (2048×1024) [9].
3. `gate.pick` → `op.pano_fix` (mới) — nối mép trái/phải (dùng lại `op.seamless` 1 trục), làm mịn 2 cực, chỉnh đường
   chân trời; tuỳ chọn `comfy.upscale_esrgan` (có sẵn).
4. `op.pano_cubemap` (mới) — equirect → 6 mặt cube (Pillow/numpy, CPU) + ảnh irradiance mờ cho ánh sáng.
5. `gate.approve`.

**Model / license / VRAM:** Qwen-Image Apache-2.0; LoRA qwen-360-diffusion MIT (chỉ tag metadata → độ tin cậy trung
bình, ghi rủi ro như các LoRA khác). Qwen 20B int8 + offload RAM — cùng cỡ với Qwen-Image-Edit-2511 đang chạy được;
~11–12GB VRAM, chậm (ước 2–4 phút/ảnh). Tránh bản FP8 (LoRA int8 gây sọc lưới) [9]. Không dùng LoRA Klein 9B 360 (Klein
9B license phi thương mại).
**Export:** Unity: material `Skybox/Panoramic` + 6 mặt cho `Skybox/6 Sided`, gắn RenderSettings bằng Editor script;
Godot: `Sky` + `PanoramaSkyMaterial` `.tres` + `WorldEnvironment` mẫu.
**Độ khó:** S–M. **Rủi ro:** méo ở cực, mép chưa liền → `op.pano_fix` + chấm điểm seam (dùng lại `op.tile_check`);
spike VRAM Qwen-Image base trước.
**Kiểm:** unit equirect→cube (ảnh lưới kinh vĩ tổng hợp: cạnh các mặt khớp ≤1px), điểm seam trái/phải. Godot headless
render 1 frame camera xoay 4 hướng; Unity batchmode chụp Game view → không thấy đường nối.

## #44 — Prop 3D phá huỷ được (vỡ mảnh + bản vỡ sẵn)

**Giá trị:** prop 3D (thùng, bình, tường, tượng) → bản nguyên + bản đã vỡ thành N mảnh có mặt cắt texture riêng, kèm
prefab vật lý; nối tiếp #1 game-ready và bản 2D #17 damage-states. Đối thủ AI 3D (Meshy/Tripo) không có.

**Luồng UI:** từ kết quả Prop 3D bấm "Dùng tiếp cho… → Phá huỷ" → chọn số mảnh (8–40), kiểu vỡ (đều / tập trung điểm va
chạm / gỗ dạng thanh), vật liệu mặt cắt (prompt) → xem ảnh nổ tung (exploded view) → duyệt → xuất.

**Recipe `prop-destructible`:**
1. `blender.fracture` (mới) — add-on Cell Fracture (extension Blender 4.2+, GPL chỉ áp cho add-on, không áp cho
   asset sinh ra) [13]; đảm bảo mesh kín (voxel remesh nếu không manifold); đặt tên mảnh `*_chunkNN`.
2. `comfy.t2i_zimage_turbo` + `op.seamless` (có sẵn) — texture mặt cắt liền mạch (gỗ thớ, đá, gốm) gán material 2.
3. `blender.cleanup` theo ngân sách mặt mỗi mảnh; tính khối lượng mảnh theo thể tích.
4. `blender.render_dirs` — ảnh exploded view + turntable → `gate.approve`.

**Model / license / VRAM:** chỉ Z-Image Turbo (Apache-2.0) cho texture mặt cắt, ~6–8GB; còn lại CPU/Blender.
**Export:** Unity: FBX/GLB + Editor script tạo prefab `Intact` + `Fractured` (Rigidbody, MeshCollider convex, mass) +
script mẫu `SwapOnHit`; Godot: scene `.tscn` với RigidBody3D + CollisionShape3D (convex) mỗi mảnh, hậu tố `-rigid`/`-col`
khi nhập glTF.
**Độ khó:** M. **Rủi ro:** mesh TRELLIS không kín → Cell Fracture lỗi/mảnh rỗng (fallback voxel remesh); quá nhiều mảnh
làm nặng mobile → giới hạn + báo số tam giác (#31).
**Kiểm:** unit params + executor FakeBlender; live: thùng gỗ 16 mảnh → tổng thể tích mảnh ≈ thể tích gốc ±3%, mọi mảnh
kín. Godot headless chạy 60 frame vật lý: mảnh rơi, không xuyên sàn; Unity batchmode: prefab có đủ Rigidbody/Collider.

## Xếp hạng đề xuất

| Thứ tự | # | Chức năng | Độ khó | Model mới | Lý do |
|---|---|---|---|---|---|
| 1 | 40 | Hoạt ảnh sprite từ 1 ảnh | M | không bắt buộc (Fun-Control 5B tuỳ chọn) | Nhu cầu lớn nhất, đối thủ đang đẩy; Wan2.2 5B đã chạy trên máy |
| 2 | 43 | Skybox 360 | S–M | Qwen-Image + LoRA 360 (spike VRAM) | Rẻ, phần lớn CPU, mở mảng game 3D |
| 3 | 42 | Restyle hàng loạt giữ GUID | M | không | Khác biệt rõ, dùng lại Klein + style bible + QA |
| 4 | 44 | Prop 3D phá huỷ | M | không | CPU/Blender, nối #1 game-ready |
| 5 | 41 | Retexture mesh | L | ControlNet Union 2.0 | Giá trị cao nhưng khó nhất (nối view, bake) → làm sau khi #43/#44 ổn |

Spike nên làm trước: (a) Qwen-Image int8 + LoRA 360 ở 2048×1024 trên 12GB; (b) Wan2.2 TI2V-5B giữ nhân vật ổn định 12–16
frame trên nền phẳng.

## Nguồn

1. GDC 2026 State of the Game Industry — https://gdconf.com/article/gdc-2026-state-of-the-game-industry-reveals-impact-of-layoffs-generative-ai-and-more/
2. Ludo.ai Sprite Generator 06/2026 — https://www.gamedeveloper.com/press-release/ludo-ai-upgrades-sprite-generator-with-pose-generator-sequence-and-transition-tools-and-updated-export-options
3. Ludo Keyframe Animation — https://ludo.ai/tools/keyframe-sprite-animation · Godot — https://new.ludo.ai/tools/sprite-generator-for-godot
4. PixelLab — https://pixellab.ai/ · https://www.pixellab.ai/docs/ways-to-use-pixellab
5. Meshy Retexture API — https://docs.meshy.ai/api/retexture.md
6. Tripo texturing (qua 3D AI Studio) — https://www.3daistudio.com/Platform/API/Documentation/texturing
7. StableGen (Blender + ComfyUI) — https://github.com/Zaws77/StableGen
8. Comfy template Equirectangular HDR Skybox — https://comfy.org/workflows/templates_text_prompt_to_360hdr.app-f5199b5121f3/
9. qwen-360-diffusion (MIT) — https://huggingface.co/ProGamerGov/qwen-360-diffusion
10. Wan2.2 Fun Control (Apache-2.0, Canny/Depth/OpenPose/MLSD) — https://docs.comfy.org/zh/tutorials/video/wan/wan2-2-fun-control
11. Template Wan2.2 5B Fun Control — https://comfy.org/templates/video_wan2_2_5B_fun_control
12. Z-Image-Turbo-Fun-ControlNet-Union (2.0 thêm inpaint) — https://huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union · https://cnb.cool/ai-models/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.0
13. Cell Fracture extension (Blender 4.2+, GPL-3) — https://extensions.blender.org/add-ons/cell-fracture/
