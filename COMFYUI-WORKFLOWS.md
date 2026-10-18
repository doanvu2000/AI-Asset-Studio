# Danh mục workflow ComfyUI (API)

7 workflow dạng API ở `L:\AI\setup\workflows\`, đều đã chạy thử thành công ngày 2026-10-18.
Cách gửi job / đọc kết quả: [COMFYUI-INTEGRATION.md](COMFYUI-INTEGRATION.md).
Điểm cắm dạng máy đọc: `L:\AI\setup\workflows\index.json` (`images`, `texts`, `seeds`, `outputs` cho từng workflow).

## Tổng quan

| Workflow | Việc | Vào | Ra | Thời gian* | Model chính |
|---|---|---|---|---|---|
| `t2i_zimage_turbo` | text → ảnh | prompt | PNG 1024² | 27s lạnh / 20s nóng (NVMe) | Z-Image-Turbo bf16 + Qwen3-4B |
| `t2i_zimage_controlnet` | ảnh điều khiển (canny) + text → ảnh | ảnh + prompt | PNG | 313s | Z-Image-Turbo + ControlNet Union |
| `remove_bg_birefnet` | tách nền | ảnh | PNG RGBA | 6s | BiRefNet |
| `img2mesh_hunyuan21` | ảnh → mesh (chỉ hình khối) | ảnh | GLB không texture, ~400k mặt | 61s | Hunyuan3D 2.1 |
| `img2mesh_trellis2_textured` | ảnh → mesh có texture PBR | ảnh | GLB + UV + texture 4K | ~6 phút | TRELLIS.2 + Pixal3D int8, MoGe-2, BiRefNet |
| `edit_flux2_klein4b` | sửa ảnh theo lời | ảnh + prompt | PNG ~1MP | 102s | Flux.2 Klein base 4B fp8 |
| `multi_angles_qwen2511` | 1 ảnh nhân vật → 8 góc máy | ảnh (+8 prompt) | 8 PNG | 461s | Qwen-Image-Edit-2511 int8 + Lightning + LoRA multi-angle |

\* Z-Image đo lại trên NVMe (`F:\work\AI\comfyUI_models`): lạnh 27s so với 174s khi còn ở HDD. Các workflow khác đo khi
model còn trên HDD → thực tế nạp nhanh hơn số trong bảng. "Nóng" = model đã trong VRAM/RAM; đổi sang workflow dùng model
khác thì model cũ bị đẩy ra.

Quy ước bảng dưới: key `"<node_id>.<input>"` → `prompt[node_id]["inputs"][input] = value`.
Output: ảnh nằm trong `history.outputs[node].images`, 3D trong `history.outputs[node]["3d"]`.

---

## 1. `t2i_zimage_turbo` — text → ảnh

| Key | Mặc định | Ghi chú |
|---|---|---|
| `57:27.text` | (prompt mẫu) | **prompt** (tiếng Anh tốt nhất; không có negative prompt — Turbo dùng cfg 1) |
| `57:3.seed` | 0 | random mỗi lần |
| `57:13.width` / `57:13.height` | 1024 / 1024 | bội số 16; sprite nên 1024², ảnh ngang 1344×768… |
| `57:13.batch_size` | 1 | nhiều ảnh/lần (tốn VRAM) |
| `57:3.steps` | 8 | Turbo: 8 là chuẩn |
| `9.filename_prefix` | `z-image-turbo` | |

Output: node `9` (`SaveImage`).
Mẹo sprite: thêm vào prompt "isolated on plain white background, centered, full body, game asset" → tách nền sạch hơn.

## 2. `t2i_zimage_controlnet` — sinh ảnh bám theo ảnh điều khiển

| Key | Mặc định | Ghi chú |
|---|---|---|
| `58.image` | `image_z_image_turbo_fun_union_controlnet_input_image.png` | ảnh tham chiếu (bố cục / dáng) |
| `70:45.text` | (prompt mẫu) | prompt |
| `70:44.seed` | cố định | random mỗi lần |
| `57.low_threshold` / `57.high_threshold` | 0.1 / 0.32 | ngưỡng Canny (cạnh trích từ `58`) |
| `70:60.strength` | 1 | độ bám control (0.5–0.8 thoáng hơn) |
| `70:44.steps` | 8 | |
| `9.filename_prefix` | `z-image-turbo` | |

Output: node `9`. Kích thước ảnh ra = kích thước ảnh `58` (latent `70:41` lấy size qua `70:69 GetImageSize`).
ControlNet Union hỗ trợ canny/pose/depth; workflow này nối sẵn Canny — muốn pose/depth phải đổi node tiền xử lý.

## 3. `remove_bg_birefnet` — tách nền → PNG trong suốt

| Key | Mặc định | Ghi chú |
|---|---|---|
| `17.image` | `the_lily_veil.png` | ảnh vào |
| `save_rgba.filename_prefix` | `sprites/rgba` | |

Output: node `save_rgba` (`SaveImage`, PNG RGBA → `output\sprites\rgba_00001_.png`).
Node `save_rgba` do `build_workflows.py` thêm vào (template gốc chỉ preview).

## 4. `img2mesh_hunyuan21` — ảnh → mesh (không texture)

| Key | Mặc định | Ghi chú |
|---|---|---|
| `2.image` | `3d_hunyuan3d-v2.1_input_image.png` | ảnh vật thể, nền trơn/đã tách nền |
| `7.seed` | cố định | |
| `7.steps` | 30 | |
| `8.octree_resolution` | 256 | độ chi tiết mesh (cao hơn = dày + chậm) |
| `9.threshold` | 0.6 | ngưỡng bề mặt |
| `10.filename_prefix` | `mesh/ComfyUI` | |

Output: node `10` (`SaveGLB`) → `output\mesh\*.glb`. Chỉ có hình khối, không màu/UV — dùng khi chỉ cần mesh
để retopo/texture trong Blender. Cần màu → dùng TRELLIS.

## 5. `img2mesh_trellis2_textured` — ảnh → mesh GLB có texture PBR

| Key | Mặc định | Ghi chú |
|---|---|---|
| `122.image` | `viking_wolf_rune_axe.png` | ảnh vật thể (workflow tự tách nền bằng BiRefNet) |
| `12.seed`, `18.seed`, `3.seed`, `23.seed` | 43 / 42 / 56 / 42 | seed các stage (shape, texture…) |
| `186.target_face_count` | 700000 | **số mặt mesh ra** — asset game nên 10k–50k |
| `288.value` | 4096 | kích thước texture bake + UV atlas (2048 nhẹ hơn cho mobile) |
| `322.filename_prefix` | `3d/ComfyUI` | |

Output: node `322` (`Save3DAdvanced`) → `output\3d\*.glb` (UV + base color/normal/AO, đã kiểm 4096/4096/2048).
Workflow nặng nhất (~6 phút); các node `Preview*` chỉ tạo ảnh tạm, bỏ qua.

## 6. `edit_flux2_klein4b` — sửa ảnh theo lời

| Key | Mặc định | Ghi chú |
|---|---|---|
| `76.image` | `robed_women.png` | ảnh gốc |
| `75:74.text` | (prompt mẫu: đổi nền) | **lệnh sửa** ("change the background to…", "make the car blue"…) |
| `75:67.text` | `""` | negative prompt |
| `75:73.noise_seed` | cố định | |
| `75:62.steps` | 20 | |
| `75:63.cfg` | 5 | |
| `75:80.megapixels` | 1 | ảnh vào được scale về ~1MP |
| `9.filename_prefix` | `Flux2-Klein-4b-base` | |

Output: node `9`. Giữ bố cục/nhân vật, đổi chi tiết theo prompt. Dùng để tạo biến thể màu, đổi nền, sửa lỗi nhỏ.

## 7. `multi_angles_qwen2511` — 1 ảnh → 8 góc máy

| Key | Mặc định |
|---|---|
| `25.image` | `character_input.png` — ảnh nhân vật/vật thể |

8 nhánh chạy trong 1 job, mỗi nhánh 1 prompt + seed + output:

| Góc | Prompt key | Prompt mặc định | Seed key | Output node | Prefix |
|---|---|---|---|---|---|
| cận cảnh | `427:221:151.prompt` | Turn the camera to a close-up. | `427:221:169.seed` | `373` | `angles/close_up` |
| xoay phải 45° | `427:246:228.prompt` | Rotate the camera 45 degrees to the right. | `427:246:242.seed` | `374` | `angles/rot45_right` |
| từ trên cao | `427:271:253.prompt` | Turn the camera to an aerial view. | `427:271:267.seed` | `376` | `angles/aerial` |
| góc rộng | `427:296:278.prompt` | Turn the camera to a wide-angle lens. | `427:296:292.seed` | `379` | `angles/wide_angle` |
| xoay phải 90° | `427:321:303.prompt` | Rotate the camera 90 degrees to the right. | `427:321:317.seed` | `380` | `angles/rot90_right` |
| góc thấp | `427:346:328.prompt` | Turn the camera to a low-angle view. | `427:346:342.seed` | `377` | `angles/low_angle` |
| xoay trái 90° | `427:371:353.prompt` | Rotate the camera 90 degrees to the left. | `427:371:367.seed` | `381` | `angles/rot90_left` |
| xoay trái 45° | `427:423:405.prompt` | Rotate the camera 45 degrees to the left. | `427:423:419.seed` | `424` | `angles/rot45_left` |

- Prefix template gốc đặt sai nhãn (ví dụ "xoay phải 45°" lưu thành `wide_shot`) → `build_workflows.py` đã sửa thành bảng trên.
- Đổi prompt từng nhánh để ra bộ góc khác (ví dụ 8 hướng cho sprite: "Rotate the camera 135 degrees…", "back view").
- `427:<nhánh>:168.value` (mặc định `true`) = dùng LoRA Lightning (4 steps, cfg 1). `false` → 40 steps, cfg 4,
  gấp 10 lần số step (mỗi nhánh có switch riêng; `<nhánh>` = 221, 246, 271, 296, 321, 346, 371, 423).
- Chỉ cần ít góc: xoá output node của nhánh không dùng khỏi prompt trước khi gửi → server bỏ cả nhánh đó.

---

## Pipeline gợi ý

**Sprite 2D (nhân vật / xe / item):**
1. Concept: `t2i_zimage_turbo` (hoặc Codex image gen ngoài ComfyUI → upload).
2. Biến thể: `edit_flux2_klein4b` (đổi màu, phụ kiện) hoặc `multi_angles_qwen2511` (nhiều góc nhìn).
3. `remove_bg_birefnet` → PNG RGBA → cắt/scale thành sprite sheet ở app.

**Asset 3D:**
1. Ảnh vật thể nền trơn (t2i hoặc ảnh có sẵn).
2. `img2mesh_trellis2_textured` (có texture, giảm `186.target_face_count`) hoặc `img2mesh_hunyuan21` (chỉ hình khối, nhanh).
3. Blender: import GLB → decimate / chỉnh pivot, scale → export, hoặc render sprite 8 hướng orthographic
   (`L:\AI\blender\renders\`).

## Model trên máy (`F:\work\AI\comfyUI_models`)

| Thư mục | File |
|---|---|
| `diffusion_models` | `z_image_turbo_bf16`, `flux-2-klein-base-4b-fp8`, `qwen_image_edit_2511_int8_convrot`, `trellis_2_int8_convrot`, `pixal3d_int8_convrot` |
| `text_encoders` | `qwen_3_4b`, `qwen_2.5_vl_7b_fp8_scaled` |
| `vae` | `ae`, `flux2-vae`, `full_encoder_small_decoder`, `qwen_image_vae`, `trellis_2_shape_vae_bf16`, `trellis_2_texture_vae_bf16` |
| `checkpoints` | `hunyuan_3d_v2.1`, `sam3.1_multiplex_fp16` |
| `clip_vision` | `dino_v3_L_naf_fp32` |
| `loras` | `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16`, `qwen-image-edit-2511-multiple-angles-lora` |
| `model_patches` | `Z-Image-Turbo-Fun-Controlnet-Union` |
| `background_removal` | `birefnet` |
| `geometry_estimation` | `moge_2_vitl_normal_fp16` |
| `upscale_models` | `RealESRGAN_x4plus` (chưa có workflow riêng — node `ImageUpscaleWithModel`) |

SAM3.1 (segmentation) và RealESRGAN đã có model nhưng chưa đóng gói thành workflow.
Thêm/tải model: `L:\AI\setup\comfyui-models.ps1`. Kiểm tra model server thấy: `GET /models/<thư mục>`.
