# P0 — Báo cáo spike (2026-10-18)

Code spike: `spikes/` (venv `spikes/.venv`, uv, Python 3.13). Log thô: `spikes/spike_*.log`, ảnh: `spikes/out/`.
Tất cả số đo trên máy thật (i5-14600KF, RTX 5070 12GB, model trên NVMe).

## Tổng kết

| Spike | Kết quả | Số đo chính |
|---|---|---|
| a. Claude Agent SDK | ✅ | 3.0s/lượt tối thiểu (haiku) · 5.7s có JSON schema · tool in-process OK |
| b. Codex image headless | ✅ (có điều kiện prompt) | 35s/ảnh · 3 song song 38.5s wall · 1254² · alpha thật khi yêu cầu |
| c. ComfyUI từ Python | ✅ | boot 30s · Z-Image 19.7s / nóng 17.8s · BiRefNet 3.1s · kill-tree 2.4s, VRAM về 1.7GB |
| d. Blender worker headless | ✅ | boot 6.7s · job đầu 14.7s (compile shader) · job nóng **3.5s / 8 hướng 512²** · mirror 2.7s |
| e. Qwen 8 hướng | ⚠️ chỉ dùng concept | 149s / 8 ảnh, góc không chính xác |

→ Kiến trúc v2 giữ nguyên. Đường chính sprite 8 hướng = **R5** (3D → Blender) được xác nhận; R3 chỉ để xem nhanh.

## a. Claude Agent SDK (`spike_a_claude.py`, `spike_a2.py`)

- `claude-agent-sdk` 0.2.168. **Dùng login Claude sẵn có** (`apiKeySource: none`, không cần `ANTHROPIC_API_KEY`).
- `setting_sources=[]` → không nạp CLAUDE.md/hook cá nhân (model xác nhận `saw_personal_instructions=false`, output không
  caveman).
- **Bẫy**: connector claude.ai (Gmail, Slack, Figma…) vẫn bị nạp → phải thêm `strict_mcp_config=True`
  (hoặc env `ENABLE_CLAUDEAI_MCP_SERVERS=false`). Đã verify cả 2 → `mcp_servers` rỗng.
- `tools=[]` tắt tool built-in; `output_format={"type":"json_schema",…}` → `ResultMessage.structured_output` là dict.
- `create_sdk_mcp_server` + `@tool` chạy in-process, tên tool `mcp__<server>__<tool>`.
- Plugin builtin (`cc-plugin-*`) vẫn có trong init — vô hại.
- Latency: 3.0s (say OK) · 5.7s (3 prompt + schema, haiku) · 5.8s (1 tool call, sonnet). Cost SDK báo là ước tính.

Áp dụng adapter: `ClaudeAgentOptions(setting_sources=[], strict_mcp_config=True, tools=[], allowed_tools=[mcp__core__*], model=…)`.

## b. Codex image (`spike_b_codex.py`)

- Lệnh: `codex.exe exec --json --skip-git-repo-check --sandbox read-only -C <job_dir> [--ephemeral] "<prompt>"`.
- **Spawn thẳng `codex.exe` native** (`%APPDATA%\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe`),
  không qua `codex.cmd` (escape argv qua cmd vỡ prompt nhiều dòng). `stdin=DEVNULL` (Codex đọc thêm stdin).
- **Bẫy**: prompt "Generate an image: …" trơn → model trả rỗng, không gọi tool. Phải thêm hậu tố:
  `Only call your image generation tool once … do not run shell commands … reply DONE.` → ổn định 5/5.
- Ảnh: `~/.codex/generated_images/<thread_id>/exec-<uuid>.png`, `thread_id` từ event `thread.started`.
  Event JSON **không** có item ảnh → gom file theo thư mục thread sau khi process thoát.
- `--ephemeral` **vẫn lưu ảnh** → dùng mặc định (không rác session).
- Kích thước 1254×1254. Mặc định RGB; yêu cầu "TRUE alpha channel, no checkerboard" → **RGBA thật** (4 góc alpha 0,
  59% pixel trong suốt) → có thể bỏ BiRefNet cho ảnh Codex.
- 1 ảnh 35–50s; 3 song song 38.5s wall → lane CLOUD song song ≥3 được (chưa đo giới hạn quota).
- Codex vẫn nạp AGENTS.md cá nhân (~20k token input, phần lớn cache). Chưa ảnh hưởng kết quả; theo dõi.

## c. ComfyUI (`comfy_lib.py`, `spike_c_comfy.py`)

- Job Object: spawn `CREATE_SUSPENDED` → `AssignProcessToJobObject` → `NtResumeProcess` → bắt được cả launcher uv lẫn
  python con (2 PID). `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + `TerminateJobObject` → 0 process sót, VRAM 9.4GB → 1.7GB.
- Boot **30s** (doc ghi ~60s), argv xác nhận `--disable-pinned-memory`.
- Submit sai kiểu → **400** `prompt_outputs_failed_validation` + `node_errors` → adapter fail ngay.
- WS: route theo `prompt_id` OK, nhận `execution_start/cached/success`, `executing` 10 node, 8 frame preview binary.
- Đọc output thẳng từ đĩa, stage input bằng copy vào `input\studio\` (không upload HTTP).
- Z-Image 19.7s (lần đầu sau boot) / 17.8s nóng · BiRefNet 3.1s, RGBA góc alpha 0.

## d. Blender worker (`blender_worker.py`, `spike_d_blender.py`)

- `blender.exe -b --factory-startup -noaudio -P blender_worker.py -- 0` → TCP localhost JSON-line, mỗi job
  `read_homefile(use_empty=True)`. Dùng numpy bundled của Blender (OK, không cần cài thêm).
- GLB TRELLIS 465k vertex: import 1.8s lần đầu / 0.9s sau.
- EEVEE 512²: **frame đầu tiên của process 10.7s** (compile shader), sau đó ~0.28s/frame. Job nóng 8 hướng = 3.5s.
  → worker thường trú là bắt buộc; nên chạy 1 render "warm-up" lúc khởi động worker.
- Ortho scale chung (bán kính ngang max + độ cao, theo góc nghiêng) → 8 frame cùng scale, pivot chân cố định. Nền trong
  suốt OK (alpha góc 0). Elevation 30° và 60° đều đúng hình học.
- Mirror (5 hướng) 2.7s.
- **Cần sửa ở P3**: ánh sáng quá tối (sun 3 + ambient 1, view Standard) → cần preset đèn 3 điểm + ambient cao hơn;
  enum engine khi `--factory-startup` chỉ có `BLENDER_EEVEE` → muốn Cycles phải `addon_utils.enable("cycles")`.
- Mesh GLB giữ hướng như ảnh gốc (rìu nằm nghiêng) → cần bước chuẩn hoá hướng "đứng" (tuỳ chọn, hoặc Claude/người chỉnh).

## e. Qwen multi-angle 8 hướng

- 8 prompt "Rotate the camera N degrees to the right/left", "…180 degrees to show the back view", "high-angle 45°".
- 149s cho cả 8 nhánh (doc ghi 461s khi model trên HDD).
- Chất lượng: nhân vật giữ nhất quán tốt (giáp, áo choàng, mặt), **nhưng góc không chính xác**: "45° phải" ra gần
  profile 90°, 135° không ra 3/4 sau, "high-angle 45°" ra gần top-down. Không khoá được góc nghiêng.
- Kết luận: R3 chỉ dùng để xem concept nhiều phía / làm ảnh tham chiếu; sprite thật dùng R5.

## Thay đổi so với plan v2

1. Claude adapter thêm `strict_mcp_config=True` (bắt buộc).
2. Codex adapter: spawn `codex.exe` native, `stdin=DEVNULL`, `--ephemeral`, prompt hậu tố cố định; prompt alpha → bỏ
   bước tách nền cho ảnh Codex (vẫn giữ BiRefNet làm fallback khi alpha bẩn).
3. Timeout ComfyUI boot giữ 120s nhưng kỳ vọng ~30s; số thời gian workflow cập nhật (Qwen ~150s).
4. Blender worker: warm-up render khi spawn; preset đèn; bật addon Cycles khi cần.
5. P3 thêm op "chuẩn hoá hướng mesh".
