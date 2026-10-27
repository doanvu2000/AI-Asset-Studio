# PLAN — Cài đặt một chạm cho người mới (2026-10-27; đã làm 2026-10-28, xem PROGRESS)

Mục tiêu: máy trắng (Windows + GPU NVIDIA) → mở app → bấm **Cài tất cả** → chờ tải → dùng được. Không terminal, không
sửa đường dẫn tay.

## Hiện trạng

- Cài tay ngoài repo: ComfyUI 0.41.0 (git clone + uv venv, torch cu130) ở `L:\AI\setup\comfyUI`, model ~79 GB tải bằng
  `L:\AI\setup\comfyui-models.ps1` (22 file Hugging Face, curl resume), workflow API ở `L:\AI\setup\workflows`
  (sinh bởi `tools/build_workflows.py`), Blender 5.2, Claude Code CLI.
- Workflow chỉ dùng node gốc ComfyUI (không custom node) → cài ComfyUI đơn giản.
- App đã có `GettingStarted.vue` + màn **Đường dẫn công cụ** kiểm tra version.

## Cách làm

1. **Workflow vào repo**: chép `*.api.json` + `index.json` vào `core/studio_core/workflows/`, đóng gói theo app;
   `comfy_workflows_dir` mặc định trỏ vào đó. Bỏ phụ thuộc `L:\AI\setup\workflows`.
2. **Manifest model** `core/studio_core/setup/models.json`: tên, thư mục, URL, size, sha256, gói (`2d`, `3d`, `sprite`,
   `spine`). Recipe khai báo gói cần → app biết recipe nào dùng được.
3. **Core op `setup`** (chạy nền, tiếp tục được sau khi tắt máy / mất mạng, báo tiến độ qua WS như lượt chạy):
   - ComfyUI: tải `uv.exe` (đi kèm app, ~30 MB) → clone/zip ComfyUI tag 0.41.0 → `uv venv` Python 3.13 → torch cu130 →
     requirements. Kiểm GPU / driver trước (nvidia-smi), driver cũ thì báo cập nhật.
   - Model: tải song song 2 luồng, HTTP Range resume, kiểm sha256, chỉ tải gói đã chọn.
   - Blender: zip portable 5.2 từ download.blender.org vào thư mục công cụ (không cần admin).
   - Claude Code: chạy installer chính thức, rồi nút **Đăng nhập Claude** mở terminal `claude`.
   - Xong ghi `settings.json`, chạy **Kiểm tra lại**, chạy thử 1 lượt Concept 2D nhỏ.
4. **UI "Thiết lập lần đầu"** (mở rộng `GettingStarted`): chọn ổ đặt công cụ + ổ model (gợi ý ổ NVMe còn trống nhiều),
   chọn gói (mặc định tất cả, hiện dung lượng từng gói), một nút **Cài tất cả**, thanh tiến độ từng mục + tổng GB /
   tốc độ / thời gian còn lại. Đã có ComfyUI / model sẵn → **Dùng bản có sẵn** chỉ cần chọn thư mục, app tự nhận.
5. **Gói offline** (tuỳ chọn): thư mục model chép từ ổ cứng / NAS → app kiểm sha256 rồi dùng luôn, khỏi tải 80 GB.
6. Test: manifest hợp lệ, resume từ file dở, sha sai thì tải lại, setup idempotent; chạy thật trên thư mục trắng.

## Rủi ro

- 80 GB tải lâu (100 Mbps ≈ 2 giờ) → chia gói, cho dùng recipe ngay khi gói của nó xong.
- Một số repo Hugging Face cần đồng ý license (FLUX.2) → kiểm từng URL tải ẩn danh được; nếu không, hỏi token HF.
- torch cu130 cần driver NVIDIA đủ mới → kiểm trước khi tải.
