# Rà soát license model — 2026-10-29

Gửi chủ nhân: bảng dưới tra từ model card HF / repo GitHub gốc (ngày 2026-10-29). Đây là bản tóm tắt
kỹ thuật, không phải tư vấn pháp lý. Với các mục "có điều kiện" / "không", trước khi bán nên đọc lại
nguyên văn license.

Ghi chú về repo repackage (Comfy-Org, Kijai, ComfyUI-Wiki): **repack không tạo license mới**, file
vẫn theo license của bản gốc. Phần lớn repo Comfy-Org gắn đúng tag gốc (Wan 2.1 → apache-2.0,
stable-audio → stable-audio-community + ghi rõ "Powered by Stability AI", sam3.1 → sam-license,
ace_step → apache-2.0), **nhưng không phải lúc nào cũng chính xác từng file**: `Comfy-Org/Pixal3D`
gắn tag MIT cho cả repo, trong khi trong đó có `dino_v3_L_naf_fp32.safetensors` (DINOv3 — license
riêng của Meta). `Kijai/WanVideo_comfy` không gắn license. → Luôn tra theo **bản gốc**.

## Bảng license

| Model file | Recipe dùng (theo workflow) | License | Thương mại | Điều kiện cụ thể | Nguồn |
|---|---|---|---|---|---|
| RealESRGAN_x4plus.safetensors | upscale, store-kit, store-kit-image | BSD-3-Clause (repo code) | được | Giữ notice BSD nếu phân phối lại file. Repo gốc không ghi license riêng cho weights; repack Comfy-Org gắn bsd-3-clause | github.com/xinntao/Real-ESRGAN · huggingface.co/Comfy-Org/Real-ESRGAN |
| birefnet.safetensors | concept-2d*, icon-pack, item-rarity, parallax-layers, damage-states, spine-character*, sprite-8dir-2d*, store-kit*, variants-2d | MIT | được | Giữ notice MIT khi phân phối lại | huggingface.co/ZhengPeng7/BiRefNet |
| z_image_turbo_bf16 / ae.safetensors / qwen_3_4b | t2i_zimage_turbo → hầu hết recipe text→image (concept-2d, icon-pack, sprite-8dir-*, prop-3d, ui-kit, tileset, texture…) | Apache-2.0 | được | — | huggingface.co/Tongyi-MAI/Z-Image-Turbo · huggingface.co/Qwen/Qwen3-4B |
| Z-Image-Turbo-Fun-Controlnet-Union | t2i_zimage_controlnet | Apache-2.0 | được | — | huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union |
| moge_2_vitl_normal_fp16 | img2mesh_trellis2_textured (prop-3d, sprite-8dir-3d*, character-3d-rigged-image) | MIT (tag HF trên repo weights `Ruicheng/moge-2-vitl-normal`, README rỗng). Code MIT, phần DINOv2 Apache-2.0 | được (độ tin cậy trung bình) | Chỉ có tag metadata, không có văn bản license riêng cho weights; base model DINOv2 là Apache-2.0 nên không vướng | huggingface.co/Ruicheng/moge-2-vitl-normal · github.com/microsoft/MoGe |
| trellis_2_int8_convrot / trellis_2_shape_vae / trellis_2_texture_vae | img2mesh_trellis2_textured + _mv_textured | MIT | được | Dependency nvdiffrast/nvdiffrec có license riêng (code render, không phải weights app tải) | huggingface.co/microsoft/TRELLIS.2-4B · github.com/microsoft/TRELLIS.2 |
| pixal3d_int8_convrot / pixal3d_multiview_int8_convrot | img2mesh_trellis2_textured / _mv_textured (prop-3d, sprite-8dir-3d*, sprite-8dir-anim/walk, character-3d-rigged-image) | MIT (TencentARC) | được | NOTICE liệt kê thành phần bên thứ 3: dinov2 (Apache-2.0), TRELLIS.2/Direct3D-S2/MoGe (MIT). NOTICE **không** nhắc DINOv3 | huggingface.co/TencentARC/Pixal3D (+ /blob/main/NOTICE) |
| dino_v3_L_naf_fp32.safetensors | cùng 2 workflow 3D ở trên (mọi recipe 3D) | DINOv3 License (Meta, 2025-08-14) | có điều kiện | Không cấm thương mại, không trần doanh thu. Bắt buộc: (1) kèm bản copy DINOv3 License khi phân phối DINO Materials/derivative; (2) **hiển thị nổi bật "Built with DINOv3"** trên website/UI/about/tài liệu sản phẩm liên quan; (3) cấm dùng cho quân sự/chiến tranh, hạt nhân, gián điệp, ITAR, vũ khí; tuân thủ trade controls (người dùng không thuộc diện bị cấm vận); (4) cấm reverse-engineer; Meta có thể sửa license, chấm dứt khi vi phạm. Chưa xác minh được file "naf" chính xác lấy từ checkpoint DINOv3 nào (Pixal3D NOTICE chỉ ghi dinov2) → theo tên file, coi như DINOv3 cho an toàn | ai.meta.com/resources/models-and-libraries/dinov3-license/ · huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m |
| flux-2-klein-base-4b-fp8 | edit_flux2_klein4b, inpaint_flux2_klein4b (bitmap-font-klein, concept-2d-style, damage-states, icon-pack, item-rarity, npc-pack*, spine-*, store-kit-image, time-season-pack, ui-kit-style, variants-2d, image-localize) | Apache-2.0 (bản **4B**) | được | Bản 9B là FLUX Non-Commercial — **không** dùng nhầm 9B. Không dùng trademark BFL/FLUX gây hiểu nhầm tài trợ | huggingface.co/black-forest-labs/FLUX.2-klein-base-4B · …/FLUX.2-klein-base-4b-fp8 |
| full_encoder_small_decoder | như trên | Apache-2.0 | được | Như trên (card ghi rõ "licensed under Apache 2.0") | huggingface.co/black-forest-labs/FLUX.2-small-decoder |
| qwen_image_edit_2511_int8_convrot | multi_angles_qwen2511 (sprite-8dir-2d, sprite-8dir-2d-prompt) | Apache-2.0 | được | — | huggingface.co/Qwen/Qwen-Image-Edit-2511 |
| qwen_2.5_vl_7b_fp8_scaled | text encoder của Qwen-Image-Edit / Qwen-Image-Layered | Apache-2.0 | được | (Lấy từ repo repack HunyuanVideo_1.5 nhưng file gốc là Qwen2.5-VL-7B-Instruct) | huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct |
| qwen_image_vae | Qwen-Image-Edit 2511 | Apache-2.0 | được | — | huggingface.co/Qwen/Qwen-Image |
| Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16 | multi_angles_qwen2511 | Apache-2.0 | được | — | huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning |
| qwen-image-edit-2511-multiple-angles-lora | multi_angles_qwen2511 (sprite-8dir-2d*) | Apache-2.0 (chỉ tag metadata) | được (độ tin cậy trung bình) | Card không có văn bản license / câu nào về thương mại; chỉ metadata `license: apache-2.0`, base = Qwen-Image-Edit-2511 (Apache) | huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA |
| sam3.1_multiplex_fp16 | segment_sam3: damage-states, spine-character, spine-character-image, spine-character-mv | SAM License (Meta, cập nhật 2025-11-19) — HF tag "other", gated | có điều kiện | Không cấm thương mại, không trần doanh thu, **không** yêu cầu dòng "Built with" như DINOv3. Bắt buộc: kèm bản copy license khi phân phối lại SAM Materials; cấm quân sự/hạt nhân/gián điệp/ITAR; tuân thủ trade controls; cấm reverse-engineer; publish nghiên cứu phải ghi nhận. Repo gated (phải đồng ý điều khoản trên HF) | github.com/facebookresearch/sam3/blob/main/LICENSE · huggingface.co/facebook/sam3.1 · huggingface.co/Comfy-Org/sam3.1 |
| stable-audio-open-1.0 | sfx_stable_audio: audio-sfx, scene-soundscape | Stability AI Community License | có điều kiện | Thương mại được nếu tổ chức **doanh thu năm < 1.000.000 USD** (tính mọi nguồn); dùng thương mại **phải đăng ký với Stability AI**; vượt ngưỡng → mua Enterprise License; tuân thủ AUP; phân phối model/derivative phải ghi "Powered by Stability AI". Output thuộc người dùng | huggingface.co/stabilityai/stable-audio-open-1.0 (LICENSE.md) · stability.ai/license |
| t5-base | text encoder Stable Audio | Apache-2.0 | được | — | huggingface.co/google-t5/t5-base · huggingface.co/ComfyUI-Wiki/t5-base |
| acestep_v1.5_turbo / qwen_0.6b_ace15 / qwen_1.7b_ace15 / ace_1.5_vae | music_ace_step15: audio-music, scene-soundscape | MIT (HF card ACE-Step/Ace-Step1.5: "License: MIT"); code GitHub MIT; repack Comfy-Org gắn apache-2.0 | được | Card ghi "You can strictly use the generated music for commercial purposes"; dữ liệu train tự khai là licensed + royalty-free/public domain + synthetic. Repo GitHub không ghi license riêng cho weights; card tự cảnh báo người dùng tự kiểm tra trùng phong cách/bản quyền | huggingface.co/ACE-Step/Ace-Step1.5 · github.com/ace-step/ACE-Step-1.5 · huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files |
| wan2.1_t2v_14B_fp8_scaled | vfx_wan_alpha: vfx-flipbook | Apache-2.0 | được | — | huggingface.co/Wan-AI/Wan2.1-T2V-14B |
| umt5_xxl_fp8_e4m3fn_scaled | text encoder Wan 2.1 / 2.2 | Apache-2.0 | được | — | huggingface.co/google/umt5-xxl |
| wan_alpha_2.1_rgba_lora / wan_alpha_2.1_vae_rgb_channel / wan_alpha_2.1_vae_alpha_channel | vfx-flipbook | HF weights Apache-2.0 (chỉ metadata); code GitHub MIT | được (độ tin cậy trung bình) | Card không có văn bản license, không câu về thương mại/nghiên cứu; base Wan2.1 Apache. Repack Comfy-Org (Wan_2.1_ComfyUI_repackaged) gắn apache-2.0 và dẫn nguồn htdong/Wan-Alpha | huggingface.co/htdong/Wan-Alpha · github.com/WeChatCV/Wan-Alpha |
| lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16 | vfx-flipbook | Apache-2.0 (bản gốc lightx2v) | được | Tải từ Kijai/WanVideo_comfy — repo này **không gắn license**; gốc lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill là apache-2.0 | huggingface.co/lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill · huggingface.co/Kijai/WanVideo_comfy |
| wan2.2_ti2v_5B_fp16 / wan2.2_vae | keyart_wan22_ti2v: key-art-motion | Apache-2.0 | được | — | huggingface.co/Wan-AI/Wan2.2-TI2V-5B |
| qwen_image_layered_fp8mixed / qwen_image_layered_vae | qwen_image_layered: image-layers | Apache-2.0 | được | Card: "Qwen-Image-Layered is licensed under Apache 2.0"; repack Comfy-Org cũng apache-2.0 | huggingface.co/Qwen/Qwen-Image-Layered · huggingface.co/Comfy-Org/Qwen-Image-Layered |
| CLIP ViT-B/32 ONNX (vision/text_model_quantized.onnx, tokenizer.json) | engine/similar, image_index (tìm ảnh giống — không tạo asset) | Xenova repo: MIT. Bản gốc openai/clip-vit-base-patch32: card không ghi license (repo code openai/CLIP là MIT) | có điều kiện (rủi ro thấp) | Model card OpenAI: "Any deployed use case of the model - whether commercial or not - is currently out of scope" — đây là khuyến cáo phạm vi dùng, không phải điều khoản license. CLIP không sinh nội dung vào asset bán ra, chỉ dùng nội bộ để tìm kiếm | huggingface.co/Xenova/clip-vit-base-patch32 · huggingface.co/openai/clip-vit-base-patch32 |
| vits-piper-vi_VN-vais1000-medium | TTS tiếng Việt: npc-pack, npc-pack-prompt, npc-voice-csv, storyboard-animatic | Repo piper-voices: MIT (code/packaging). Dataset VAIS-1000: CC BY 4.0. **Fine-tune từ voice en_US lessac medium** | chưa rõ (rủi ro) | CC BY 4.0 → phải ghi công VAIS-1000. Nhưng checkpoint gốc lessac train trên dữ liệu Lessac/Blizzard 2013 với license **chỉ cho nghiên cứu, cấm mọi mục đích thương mại** (gồm phát triển/bán sản phẩm tổng hợp giọng). Voice fine-tune có kế thừa ràng buộc không: license không nói rõ → chưa kết luận được | huggingface.co/rhasspy/piper-voices (vi/vi_VN/vais1000/medium/MODEL_CARD) · cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html |
| vi_VN-25hours_single-low | (mới tải, audio đang tạm dừng) | Dataset "InfoRe Technology 1": **License: Unknown**. Fine-tune từ en_US **ryan** low (dataset RyanSpeech **CC BY-NC-SA 4.0**) | không (nên coi là không) | Dataset không rõ license + base voice non-commercial → không dùng cho asset bán | huggingface.co/rhasspy/piper-voices (vi/vi_VN/25hours_single/low/MODEL_CARD, en/en_US/ryan/low/MODEL_CARD) |
| vi_VN-vivos-x_low | (mới tải, audio đang tạm dừng) | Dataset VIVOS: **CC BY-NC-SA 4.0**, train from scratch | **không** | NC = cấm thương mại; SA = phái sinh phải cùng license | huggingface.co/rhasspy/piper-voices (vi/vi_VN/vivos/x_low/MODEL_CARD) · ailab.hcmus.edu.vn/vivos |
| kokoro-int8-multi-lang-v1_0 | TTS (npc-pack*, npc-voice-csv, storyboard-animatic) | Apache-2.0 | được | Card: train trên dữ liệu permissive/không bản quyền; có Koniwa (CC BY 3.0) và SIWIS (CC BY 4.0) trong dữ liệu train; "deployed in numerous projects and commercial APIs" | huggingface.co/hexgrad/Kokoro-82M |

Không có trong manifest nhưng còn workflow `img2mesh_hunyuan21` trong `workflows/index.json` — recipe
hiện không dùng, nên chưa rà.

## Rủi ro khi bán asset

**Chặn / không nên dùng cho asset bán:**
- Giọng Piper **vi_VN-vivos-x_low** (CC BY-NC-SA) và **vi_VN-25hours_single-low** (dataset license
  Unknown, base ryan NC) → nếu bật lại audio thì không đưa 2 voice này vào recipe npc-pack /
  npc-voice-csv / storyboard-animatic cho bản thương mại.

**Có điều kiện:**
- **Mọi recipe 3D** (prop-3d, sprite-8dir-3d / -3d-brief / -3d-pixel / -3d-mv, sprite-8dir-anim,
  sprite-8dir-walk, character-3d-rigged-image) — qua `dino_v3_L_naf_fp32`: game/website/tài liệu
  liên quan phải hiển thị "Built with DINOv3"; kèm license nếu phân phối lại weights; cấm dùng quân
  sự. Asset bán ra là output, license không nói rõ output có phải mang dòng "Built with" hay không —
  an toàn nhất là ghi "Built with DINOv3" ở credits / trang sản phẩm.
- **audio-sfx, scene-soundscape** (Stable Audio Open): chỉ khi tổ chức doanh thu < 1M USD/năm và
  **đã đăng ký** với Stability AI; vượt ngưỡng phải mua Enterprise. Nên ghi "Powered by Stability AI".
- **damage-states, spine-character / -image / -mv** (SAM 3.1): thương mại được, chủ yếu ràng buộc
  cấm quân sự/trade controls; kèm license nếu phân phối lại weights (app bán asset thì không phân
  phối weights).
- **TTS tiếng Việt vais1000** (npc-pack*, npc-voice-csv, storyboard-animatic): chưa rõ do base lessac
  research-only → nếu bán, ưu tiên Kokoro (không có tiếng Việt) hoặc tìm voice VI có chuỗi license sạch.

**Được (permissive, tin cậy trung bình vì chỉ có tag metadata):** fal multiple-angles LoRA
(sprite-8dir-2d*), Wan-Alpha (vfx-flipbook), MoGe-2 weights, ACE-Step 1.5 (audio-music).

## Đề xuất sửa model_licenses.py / manifest (chưa áp dụng)

`core/studio_core/store/model_licenses.py` → `MODELS`:
- `"moge_2_vitl_normal_fp16.safetensors"`: `("MIT", "yes", "Ruicheng/moge-2-vitl-normal (HF tag MIT; base DINOv2 Apache-2.0)")`
- `"pixal3d_int8_convrot.safetensors"` và `"pixal3d_multiview_int8_convrot.safetensors"`: `("MIT", "yes", "TencentARC/Pixal3D")`
- `"dino_v3_L_naf_fp32.safetensors"`: giữ `restricted`, đổi source → `"facebook/dinov3 (DINOv3 License: show 'Built with DINOv3', include license copy, no military/ITAR use)"`
- `"sam3.1_multiplex_fp16.safetensors"`: giữ `restricted`, source → `"facebook/sam3.1 (SAM License: include license copy, no military/ITAR use, trade controls)"`
- `"stable-audio-open-1.0.safetensors"`: giữ `restricted`, source → `"stabilityai/stable-audio-open-1.0 (commercial only if annual revenue < USD 1M AND registered with Stability AI; 'Powered by Stability AI')"`
- `"qwen-image-edit-2511-multiple-angles-lora.safetensors"`: `("Apache-2.0", "yes", "fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA (HF tag only)")`
- `"acestep_v1.5_turbo.safetensors"`, `"qwen_0.6b_ace15.safetensors"`, `"qwen_1.7b_ace15.safetensors"`, `"ace_1.5_vae.safetensors"`: `("MIT", "yes", "ACE-Step/Ace-Step1.5")`
- `"wan_alpha_2.1_rgba_lora.safetensors"`, `"wan_alpha_2.1_vae_rgb_channel.safetensors"`, `"wan_alpha_2.1_vae_alpha_channel.safetensors"`: `("Apache-2.0", "yes", "htdong/Wan-Alpha (HF tag only)")`
- `"Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"`: source → `"lightx2v/Qwen-Image-Edit-2511-Lightning"` (hiện ghi Qwen-Image-Lightning)
- `"lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors"`: source → `"lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill"`
- `"vits-piper-vi_VN-vais1000-medium"`: `("CC-BY-4.0 dataset; fine-tuned from Piper en_US lessac (Lessac/Blizzard 2013 research-only)", "unknown", "rhasspy/piper-voices vi_VN/vais1000/medium")` — giữ `unknown`, ghi rõ lý do.
- Thêm mới (nếu dùng): `"vits-piper-vi_VN-vivos-x_low"`: `("CC-BY-NC-SA-4.0 (VIVOS dataset)", "no", "rhasspy/piper-voices vi_VN/vivos/x_low")`;
  `"vits-piper-vi_VN-25hours_single-low"`: `("dataset license Unknown; fine-tuned from en_US ryan (CC-BY-NC-SA-4.0)", "no", "rhasspy/piper-voices vi_VN/25hours_single/low")` (tên key theo đúng tên thư mục sherpa-onnx khi tải).
- Thêm `"qwen_image_layered_fp8mixed.safetensors"` / `"qwen_image_layered_vae.safetensors"`: `("Apache-2.0", "yes", "Qwen/Qwen-Image-Layered")` (manifest đã có, thêm để bảng fallback đủ).
- Thêm CLIP: `"vision_model_quantized.onnx"` / `"text_model_quantized.onnx"`: `("MIT", "yes", "Xenova/clip-vit-base-patch32 (openai/CLIP; search only, not in outputs)")`.

`core/studio_core/bootstrap/manifest.json` → `models[]` (manifest thắng bảng nên phải sửa cả ở đây):
- `moge_2_vitl_normal_fp16.safetensors`: `"commercial": "unknown"` → `"yes"`.
- `pixal3d_int8_convrot.safetensors`, `pixal3d_multiview_int8_convrot.safetensors`: thêm `"license": "MIT", "commercial": "yes"`.
- `qwen-image-edit-2511-multiple-angles-lora.safetensors`: thêm `"license": "Apache-2.0", "commercial": "yes"`.
- 4 file ACE-Step 1.5: thêm `"license": "MIT", "commercial": "yes"`.
- 3 file wan_alpha: thêm `"license": "Apache-2.0", "commercial": "yes"`.
- `vits-piper-vi_VN-vais1000-medium`: license → `"CC-BY-4.0 dataset (VAIS-1000); fine-tuned from Piper en_US lessac (research-only data)"`, giữ `"commercial": "unknown"`.
- Nếu thêm voice vivos / 25hours vào manifest: `"commercial": "no"` như trên.
- Giữ nguyên: `dino_v3_L_naf_fp32` (restricted), `sam3.1_multiplex_fp16` (restricted), `stable-audio-open-1.0` (restricted), các mục Apache/MIT/BSD còn lại.

Lưu ý code: `model_license()` chỉ lấy manifest entry khi có `license`; các entry manifest chưa có
`license` (pixal3d, fal lora, ace, wan_alpha) hiện rơi về bảng fallback → sửa 1 trong 2 nơi là đủ
cho các mục đó, nhưng moge_2 thì phải sửa manifest (manifest đang ghi `unknown`).
