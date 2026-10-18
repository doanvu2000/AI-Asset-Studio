# HANDOFF 2026-10-18 10:04 — ComfyUI + Blender asset pipeline setup

> Session chạy từ repo game `car-evolve` nhưng chỉ setup môi trường AI tạo asset (ComfyUI + Blender) — đã chuyển sang đây.
> Tài liệu tích hợp: `COMFYUI-INTEGRATION.md`, `COMFYUI-WORKFLOWS.md` (thư mục gốc project này).

## Mục tiêu của chủ nhân

- Tự viết **phần mềm quản lý riêng** điều khiển ComfyUI qua HTTP/WS API — KHÔNG dùng ComfyUI desktop app.
- Dùng cho: sinh ảnh 2D + model 3D + sprite; kết hợp **Codex image gen** (`codex-cli 0.162.1` đã có) + **Blender** (MCP).
- "Cần model hay gì hỗ trợ cứ tải rồi vào" → đã được phép tải model/cài thứ cần thiết.

## Trạng thái: XONG phần setup + test

### ComfyUI — `L:\AI\setup\comfyUI`
- git clone `comfy-org/ComfyUI` (commit ce1e922d, v0.41.0), venv `.venv` Python 3.13.14 (uv managed),
  torch 2.14.1+cu130 (RTX 5070 sm_120 OK), `requirements.txt` + `manager_requirements.txt` (comfyui_manager 4.2.2).
- 22/22 model tải đủ, đã verify size (~80GB) — log `L:\AI\setup\comfyui-models.log` dòng `03:13:50 DONE ok=22 fail=0`.
- Ảnh mẫu template đã tải vào `comfyUI\input\` (the_lily_veil, 3d_hunyuan3d-v2.1_input_image, viking_wolf_rune_axe,
  character_input, robed_women, image_z_image_turbo_fun_union_controlnet_input_image).
- Server đang TẮT (đã stop sau test). Chạy: `L:\AI\setup\run-comfyui.ps1` (127.0.0.1:8188).

### 7 workflow API — tất cả đã chạy thử thành công
| Workflow (`L:\AI\setup\workflows\`) | Kết quả | Thời gian |
|---|---|---|
| t2i_zimage_turbo | sprite pixel art OK | 174s lạnh / 19s nóng |
| t2i_zimage_controlnet | bám canny OK | 313s |
| remove_bg_birefnet | PNG alpha (patch thêm SaveImage `save_rgba` → `output\sprites\`) | 6s |
| img2mesh_hunyuan21 | GLB không texture (~416k faces) | 61s |
| img2mesh_trellis2_textured | GLB PBR 4K, có UV | ~364s |
| edit_flux2_klein4b | sửa nền giữ người | 102s |
| multi_angles_qwen2511 | 8 góc máy nhất quán | 461s |

### Tools tự viết — `L:\AI\setup\tools\`
- `ui2api.py` — UI workflow (kể cả subgraph lồng) → API prompt. Đã xử lý: proxyWidgets (giá trị ở node con),
  legacy instance widgets_values (thứ tự theo **definition inputs**, ghép theo tên), widget = input không phải socket
  trong `node.inputs` (bắt COLOR/LOAD_3D), control_after_generate, upload widget, DynamicCombo (`name.sub`),
  bypass/mute/Reroute/PrimitiveNode, fill default cho input mới thiếu trong template (vd MoGeInference.refine_steps), prune.
- `build_workflows.py` — sinh lại 7 workflow + `workflows\index.json` (images/texts/seeds/outputs).
  `FILE_SWAPS`: `qwen_image_edit_2511_bf16` → `..._int8_convrot`. `PATCHES`: remove_bg thêm SaveImage.
  Cần server chạy (lấy /object_info) hoặc `--object-info file.json`.
- `run_workflow.py` — queue + `--set NODE.INPUT=VALUE` + in `node_errors` (server bỏ qua output lỗi mà vẫn báo success!) + outputs.

### Script/khác — `L:\AI\setup\`
- `run-comfyui.ps1` — params `-Port -Listen -Manager -PinnedMemory -Extra`; mặc định thêm `--disable-pinned-memory`.
- `comfyui-models.ps1` — downloader resumable, skip file đủ size (22 entry, có thêm `full_encoder_small_decoder` cho Klein).
- `README.md` — tài liệu đầy đủ (API flow, bảng workflow, lỗi đã gặp).

### Blender (5.2.2 LTS, MCP addon `blender_mcp` kết nối OK)
- Cycles → OptiX RTX 5070, prefs đã save.
- Mọi dữ liệu Blender chuyển sang `L:\AI\blender` (C: gần đầy, còn ~45GB): temp=`temp\`, python tempfile=`temp\python\`,
  texture cache=`cacche\texture\` (tên thư mục "cacche" do chủ nhân tạo, giữ nguyên), render cache=`source\render-cache\`
  (chủ nhân tự đặt, giữ), asset lib=`assets\`, render output=`renders\`, project=`project\<tên>\`.
- Startup script `L:\AI\blender\source\scripts\startup\l_drive_paths.py` (script dir "L_AI" trong prefs): set
  `tempfile.tempdir`/TEMP → `temp\python`, đổi render path mặc định `/tmp\` → `renders\`. Temp dir chính cần restart Blender.
- Scene đang mở (unsaved): collection `ComfyUI_test` chứa 2 mesh test (`Mesh_0` robot Hunyuan ẩn, `Mesh_0.001` rìu TRELLIS), Cube mặc định đang ẩn.

## Quyết định & ràng buộc quan trọng

- **`--disable-pinned-memory` bắt buộc** (root cause đã xác minh): Windows commit limit = 64GB RAM + pagefile 4GB (C:),
  app khác (8 claude, chrome, bun, java, ChatGPT…) giữ ~36–44GB commit → pinned memory làm cấp phát fail → torch crash
  `Windows fatal exception: access violation` (Event log: c10.dll / VCRUNTIME140.dll, 0xc0000005), lúc ở
  `voxel_to_mesh_surfnet` (nodes_hunyuan3d.py:254), lúc ở `Linear.__init__` khi load model. `--disable-dynamic-vram` KHÔNG fix.
  Hàm surfnet chạy riêng ngoài ComfyUI thì OK. Tắt pinned → Hunyuan OK 61s.
- L: = HDD Seagate ST4000VX016 (SATA, surveillance) → load model chậm; F: = NVMe Lexar 1TB (410GB trống); C: = NVMe XPG.
- Quantized thay bf16 vì 12GB VRAM: TRELLIS.2/Pixal3D int8_convrot, Qwen-Edit-2511 int8_convrot.
- Bỏ template `character_sheet`, `match_game_art_style` (API Gemini/Recraft trả phí) → dùng Codex image gen thay.
- Chủ nhân KHÔNG muốn di chuyển uv cache (6.4GB) / HF cache (4.5GB) khỏi C: ("thôi k cần").
- CLAUDE.md: tiếng Việt, gọi "chủ nhân", xưng "con"; không tự commit/push; không build-verify; ít comment;
  bug non-Android/Flutter → skill `bug-feedback-memory` (`.bug_feedback/`), chỉ lưu sau khi chủ nhân confirm OK.
- Hook RTK rewrite lệnh bash: grep với `\|` hay hỏng → dùng Grep tool / PowerShell. `TaskStop` không giết process con
  (từng còn `tail.exe` mồ côi khoá log) → kiểm tra & kill process con sau khi stop monitor.

## File cần đọc lại khi tiếp tục

- `L:\AI\setup\README.md` (tổng quan đầy đủ)
- `L:\AI\setup\workflows\index.json` (điểm cắm workflow)
- `L:\AI\setup\tools\ui2api.py`, `build_workflows.py`, `run_workflow.py`
- `L:\AI\setup\run-comfyui.ps1`, `L:\AI\setup\comfyui-models.ps1`
- `L:\AI\blender\source\scripts\startup\l_drive_paths.py`
- Memory: `C:\Users\dauph\.claude\projects\F--work-android-WeJoy-car-evolve\memory\` (`comfyui-local-setup.md`, `blender-mcp.md`)

## Bước tiếp theo (theo thứ tự) — đều đang CHỜ chủ nhân trả lời

1. **Bug crash access violation**: chủ nhân confirm OK → lưu `.bug_feedback/comfyui-pinned-memory-access-violation.md`
   (đặt ở `L:\AI\setup\.bug_feedback\`), theo template của skill `bug-feedback-memory` (triệu chứng = stacktrace/event log
   ở trên, nguyên nhân = commit limit, solution = `--disable-pinned-memory` trong launcher).
2. **Pagefile** (chưa làm, cần admin + reboot, phải hỏi trước): đề xuất thêm pagefile ~32GB trên F: để có thể bật lại pinned memory
   (`run-comfyui.ps1 -PinnedMemory`).
3. ~~Chuyển models sang F: NVMe~~ — ĐÃ LÀM: model ở `F:\work\AI\comfyUI_models`, launcher `--models-directory` (param `-Models`),
   downloader `$root` trỏ F:. `comfyUI\models\` chỉ còn file giữ chỗ git + configs. Log move: `L:\AI\setup\move-models.log`.
   Verify: 22 file 78.35GB, server thấy đủ, Z-Image lạnh 27s (HDD 174s). Server đã tắt lại.
   Docs tích hợp: `L:\AI\setup\docs\COMFYUI-INTEGRATION.md` + `COMFYUI-WORKFLOWS.md`, đã copy sang `L:\AI\AI-Asset-Studio\`
   (project app quản lý: Electron + Vue 3 + TS, FastAPI). Workflow regenerate: prefix multi-angle → `angles/<góc>`, title null → class name.
4. ~~Scene Blender~~ — ĐÃ DỌN: xoá collection `ComfyUI_test` + mesh/material/image của nó, Cube hiện lại.
5. ~~Rigify~~ — ĐÃ BẬT (persistent, prefs đã save).
   Pagefile (bước 2): chủ nhân hoãn ("chưa tăng được bây giờ, anh đang làm việc") — chỉ làm khi chủ nhân nhắc.
6. Sau đó tuỳ chủ nhân: làm pipeline sprite thực tế (Codex/Z-Image → BiRefNet → TRELLIS → Blender decimate → render
   sprite 8 hướng orthographic ra `L:\AI\blender\renders\`), hoặc hỗ trợ code phần mềm quản lý gọi API.
