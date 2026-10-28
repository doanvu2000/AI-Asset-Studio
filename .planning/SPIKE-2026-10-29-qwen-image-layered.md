# SPIKE #11: Qwen-Image-Layered (tách ảnh thành layer RGBA / PSD)

Ngày 2026-10-29. Chỉ là spike: không sửa code repo, không commit.

## 1. License, dung lượng, mức hỗ trợ trong ComfyUI
- **License là Apache-2.0** (theo model card `Qwen/Qwen-Image-Layered`), dùng thương mại được. Model 20B, finetune từ Qwen-Image. Paper ra ngày 2025-12-17.
- Dung lượng diffusion model:
  - `Comfy-Org/Qwen-Image-Layered`: bf16 40.9GB · **fp8mixed 20.5GB** · VAE `qwen_image_layered_vae` 0.25GB.
  - `unsloth/Qwen-Image-Layered-GGUF`: Q2_K 7.4 · Q3_K_M 9.9 · Q4_0 11.9 · Q4_K_M 13.2 · Q5_K_M 15.0 · Q6_K 16.9 · Q8_0 21.8GB.
  - Ngoài ra có `appmana/...int8convrot` (20.6GB, Apache-2.0) và bản Control `DiffSynth-Studio/Qwen-Image-Layered-Control`, dùng prompt để chọn vật cần tách.
- Text encoder `qwen_2.5_vl_7b_fp8_scaled` (8.9GB) **đã có sẵn trên máy**.
- **ComfyUI có hỗ trợ native.** Bản đang cài là 0.41.0 (commit ce1e922d), có sẵn node `EmptyQwenImageLayeredLatentImage`, `LatentCut`, `LatentCutToBatch` và template `image_qwen_image_layered(.json|_control.json)` (templates 0.11.87). **Không cần nâng cấp.**
- GGUF thì cần cài thêm custom node ComfyUI-GGUF (máy chưa có), nên spike không dùng.
- Đã tải: `F:\work\AI\comfyUI_models\diffusion_models\qwen_image_layered_fp8mixed.safetensors` (20.5GB) và `vae\qwen_image_layered_vae.safetensors`. Tổng ~20.8GB.

## 2. Đo trên RTX 5070 12GB + 64GB RAM (fp8mixed)
Cấu hình chạy: dynamic VRAM offload (19.6GB staged ở RAM), `--disable-pinned-memory`, KSampler euler/simple, 20 bước, cfg 2.5, seed 42, có prompt mô tả ảnh.

| Run | Độ phân giải | Số layer | s/it | Tổng thời gian | Peak VRAM (nvidia-smi, cả máy) |
|---|---|---|---|---|---|
| l640 | 640² | 4 | 9.2 | 194s (load nguội) | 11.1GB |
| l640x8 | 640² | 8 | 15.4 | 310s | 11.0GB |
| l1024 | 1024² | 4 | 27.8 | 557s (~9.3 phút) | 10.9GB |

- Chạy được, không OOM, không crash. VRAM bị offload lấp gần đầy 12GB, nên khi chạy model này không thể song song việc GPU khác.
- Thời gian tăng gần tuyến tính theo (số layer + 1) × số pixel. Model card mặc định 50 bước; nếu dùng 50 bước thì thời gian ×2.5.
- Chưa có LoRA Lightning nào cho bản Layered (chưa kiểm chứng). Nếu muốn nhanh hơn thì giảm bước hoặc dùng 640.

## 3. Chất lượng (ảnh test: `comfyUI/output/p5dev/char_00001_.png`, nhân vật T-pose phẳng)
- Layer 1 luôn là plate nền (nền trắng đặc). Các layer còn lại là RGBA sạch: alpha mịn, không viền trắng. Ghép tất cả layer lại gần như khớp ảnh gốc.
- **640 / 4 layer:** chỉ có 2 layer hữu ích: (a) hai cánh tay + găng, (b) phần còn lại của thân. Có 1 layer gần rỗng. Tách cạnh rất sạch nhưng quá thô.
- **1024 / 4 layer:** tốt nhất.
  - Layer 2: tay + **mặt sau áo choàng và lưng được vẽ bù chỗ bị che**.
  - Layer 3: tóc sau, găng và **2 chân + giày hoàn chỉnh, phần đùi bị váy che cũng được vẽ bù**.
  - Layer 4: mặt, thân, áo choàng trước.
  - Đây là điều SAM3 không làm được.
- **640 / 8 layer:** vụn. Áo choàng bị chia ra 3 layer, găng nằm rải rác, mắt tách riêng một layer. Phần thân bị che được vẽ bù có màu lạ (váy xanh).
- Kết luận về chất lượng:
  - Layer chia theo **độ sâu / che khuất**, không theo bộ phận ngữ nghĩa: hai tay chung một layer, hai chân chung một layer, găng có lúc tách khỏi tay.
  - Thứ tự và nội dung từng layer **không điều khiển được** (prompt chỉ mô tả cả ảnh), và thay đổi theo độ phân giải / số layer.
  - Có mảnh vụn nhỏ, ví dụ đầu ngón tay bị tách sang layer khác.

## 4. Giá trị cho app
- **Xuất PSD (tính năng riêng: tách layer → PSD/PNG stack):** hữu ích ngay, rủi ro thấp.
  - Repo chưa có code PSD nào. Có thể ghi file bằng `psd-tools` (`PSDImage.new` + `PixelLayer.frompil`) hoặc `pytoshop`.
  - Phù hợp cho key art, UI mockup, nhân vật để artist chỉnh tiếp.
- **Spine auto-cut** (`spine-character.yaml`: BiRefNet → SAM3 theo role → `op.layers` → Flux2 Klein inpaint chỗ bị che):
  - **Không thay được SAM3.** Spine cần chia theo xương (upper/lower arm L/R, thigh L/R…), trong khi Qwen gộp L/R và chia theo độ sâu.
  - **Có thể bổ sung làm nguồn "vẽ bù chỗ bị che":** ở 1024, chân dưới váy và lưng/áo choàng sau được vẽ liền mạch hơn so với Flux2 inpaint từng lỗ (`rig.py:113-140`).
  - Cách ghép: chạy Qwen-Layered trước, lấy union các layer làm "full-body behind", rồi dùng mask SAM3 cắt từng bộ phận. Phần bị che lấy từ layer phía sau thay cho Flux2.
  - Cái giá: thêm ~9 phút / nhân vật (1024) và 20GB ổ đĩa, trong khi pipeline SAM3 + BiRefNet hiện mất vài chục giây.
- So nhanh SAM3 + BiRefNet với Qwen-Layered:

| Tiêu chí | SAM3 + BiRefNet | Qwen-Layered |
|---|---|---|
| Tốc độ | Nhanh (~giây) | Chậm (3–9 phút) |
| Chia theo | Role (head/arm/leg…), có điều khiển | Độ sâu, không điều khiển |
| Vẽ bù vùng bị che | Không có, phải inpaint riêng | Có sẵn |
| Mức chắc chắn | Ổn định | Thay đổi theo seed / độ phân giải |

## 5. Khuyến nghị
- **BUILD (mức nhỏ):** recipe `image-layers` gồm ảnh → Qwen-Layered fp8 (mặc định 1024 / 4 layer, tuỳ chọn 640 cho preview nhanh) → bỏ/giữ plate nền → xuất PNG stack + **PSD**.
  - Effort khoảng **1.5–2 ngày**: workflow api.json (dựa trên template, patch được image / layers / size / steps), op ghi PSD, trường output trong recipe, preview layer trên UI, thêm model vào downloader.
- **CHƯA build** tích hợp Spine. Nếu muốn thử thì làm một spike riêng: option `fill=qwen_layered` trong `spine-character`, so với fill Flux2 trên 3–5 nhân vật.
  - Effort khoảng **2–3 ngày**, kết quả chưa chắc.
- Không cần nâng cấp ComfyUI. Chỉ cần thêm model fp8mixed (20.5GB, đã tải) và VAE (0.25GB).
- Recipe phải giữ GPU lock suốt run vì nó chiếm gần hết 12GB VRAM.

## Đường dẫn kết quả test
- Workflow API, ảnh contact sheet, ảnh zoom, log VRAM/thời gian: scratchpad session `...\scratchpad\qil\`:
  - `run640.api.json`, `run1024.api.json`, `run640x8.api.json`
  - `sheet_l640.png`, `sheet_l1024.png`, `sheet_l640x8.png`
  - `zoom_l640.png`, `zoom_l1024.png`
  - `results.txt`, `*.vram.csv`
- Layer gốc: `L:\AI\setup\comfyUI\output\spike_qil\l640_*`, `l1024_*`, `l640x8_*` (bản copy nằm trong `scratchpad\qil\out\`).
- Ảnh input: `L:\AI\setup\comfyUI\input\spike_qil\char.png`.
