# AI Asset Studio — Phân tích 3 tài liệu + Kiến trúc đề xuất v2

Ngày: 2026-10-18 · Nguồn: `AI-ASSET-STUDIO-IMPLEMENTATION-PLAN.md` (gọi tắt PLAN), `COMFYUI-INTEGRATION.md`,
`COMFYUI-WORKFLOWS.md` + kiểm tra máy thật.

## 0. Môi trường đã xác minh (2026-10-18)

| Thành phần | Thực tế |
|---|---|
| Claude Code CLI | 2.1.294 — có `-p`, `--output-format stream-json`, `--input-format stream-json`, `--json-schema`, `--mcp-config`, `--strict-mcp-config`, `--resume`, `--model` |
| Codex CLI | 0.162.1 — `codex exec --json`, `--output-schema`, `-i <image>`, `--skip-git-repo-check`, `--ephemeral`; feature `image_generation` = stable/true |
| Codex ảnh ra | `~/.codex/generated_images/<thread_id>/exec-<uuid>.png` |
| Blender | 5.2 tại `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` (không có trong PATH) |
| Blender MCP | `uvx mcp-for-blender` (stdio) → điều khiển Blender **GUI đang mở** |
| ComfyUI | 0.41.0, 7 workflow API + `index.json` tại `L:\AI\setup\workflows\` (đã chạy thử) |
| Toolchain | Python 3.13.2 `F:\work\python\setup` (đã thêm PATH 2026-10-18) + uv 0.11 (có 3.13.14), Node 24, npm 11 (chưa có pnpm), Rust 1.99, Go 1.27 |

## 1. Đánh giá từng tài liệu

### 1.1 COMFYUI-INTEGRATION.md + COMFYUI-WORKFLOWS.md — dùng làm spec, chất lượng cao
- Đã verify thật: API flow, 3 tầng lỗi (`node_errors` lúc 200 · `execution_error` · process chết cứng), bắt buộc
  `--disable-pinned-memory`, kill cả cây process, history nằm trong RAM, 1 GPU tuần tự, timing từng workflow.
- Thiếu cho app:
  - `index.json` chỉ có `images/texts/seeds/outputs` — **không có param typed** (width/height bội 16,
    `target_face_count`, texture size, steps…) → app cần lớp overlay `workflow-manifest` (type, min/max, default, label).
  - SAM3.1 + RealESRGAN có model nhưng chưa có workflow → cần cho Spine (tách part) và upscale.
  - ControlNet chỉ nối Canny → animation theo pose cần preprocessor pose (chưa có).
  - Mục 8 gợi ý adapter FastAPI → map sang Rust (mục 3).

### 1.2 PLAN — khung tốt, nhưng viết "mù" môi trường và lệch mục tiêu
Giữ lại: adapter cô lập từng tool, approval gate, không ghi đè bản đã duyệt, audit log, Electron security
(contextIsolation, không shell từ renderer, argv array), GPU lock, giới hạn vòng tự sửa, Phase 0 verify trước.

Lệch / lỗi thời:

| # | PLAN nói | Thực tế / mục tiêu chủ nhân | Sửa |
|---|---|---|---|
| 1 | Codex = reviewer tuỳ chọn, mặc định tắt | Codex = **nguồn ảnh input** (image gen cloud) | Codex thành `ImageProvider` hạng nhất |
| 2 | 3D = Blender procedural / blockout, "ảnh không tự thành mesh" | ComfyUI có TRELLIS.2 (GLB + PBR) và Hunyuan3D 2.1 | 3D chính = img2mesh → Blender cleanup; procedural là nhánh phụ |
| 3 | Không có sprite sheet / 8 hướng / Spine / animation | Đây là nhu cầu lõi | Thêm recipe + local ops riêng (mục 4) |
| 4 | "Claude-first": Claude điều phối mọi job | Mỗi lần gọi `claude -p` tốn khởi động CLI + latency LLM + quota | **Recipe-first**: DAG cố định, Claude chỉ ở step cần suy luận |
| 5 | Orchestrator Python + FastAPI | Hướng đúng; máy không có Python global | **Giữ Python** (chốt mục 3.1): venv riêng bằng uv, đóng gói onedir |
| 6 | 1 state machine 15 trạng thái trộn job/step/approval | Khó mở rộng cho DAG nhiều step + gate | Tách 3 tầng state (mục 3.4) |
| 7 | Không nói Blender GUI vs headless | MCP chỉ điều khiển Blender GUI, 1 instance | Pipeline dùng worker headless; MCP chỉ cho step agent |
| 8 | Format file vỡ (`\#`, `&#x20;`, dòng trống kép) | — | Chuẩn hoá lại khi dùng làm prompt |

## 2. Tốc độ: thời gian thật nằm ở đâu

Flow "prop 3D từ text": Codex/Z-Image 20–90s → BiRefNet 6s → **TRELLIS ~6 phút** → Blender 10–30s → Claude 10–30s/lần.
Overhead orchestrator (Rust hay Python) chỉ cỡ ms → **ngôn ngữ core không phải nút thắt; lập lịch mới là**.

Đòn bẩy tốc độ theo thứ tự hiệu quả:
1. **Gate chọn trước bước đắt**: sinh N concept rẻ → người chọn 1 → mới chạy TRELLIS. Tránh đốt 6 phút GPU cho concept hỏng.
2. **Song song theo tài nguyên**: Codex + Claude chạy cloud, **không dùng GPU local** → chạy song song với lane GPU.
   CPU ops (trim/pack/validate) chạy song song luôn.
3. **Model-affinity scheduling**: lane GPU ưu tiên job cùng bộ model đang nạp (Z-Image lạnh 27s / nóng 20s; Qwen-Edit,
   TRELLIS nạp lâu hơn nhiều). Batch nhiều asset chạy **stage-major** (tất cả t2i → tất cả tách nền → tất cả mesh).
4. **Process luôn ấm**: ComfyUI giữ sống; Blender worker headless thường trú (bỏ 2–5s khởi động/lần);
   Claude dùng `--input-format stream-json` giữ 1 process cho vòng sửa nhiều lượt.
5. **Cache bền**: key = blake3(step kind + version + params + hash input + hash workflow) → trùng thì bỏ qua.
   (Cache của ComfyUI mất khi restart.)
6. **Bỏ HTTP thừa**: cùng máy → copy thẳng vào `comfyUI\input\`, đọc thẳng `output\` (không multipart, không `/view`).
7. Z-Image `batch_size` cho fan-out N biến thể trong 1 job thay vì N job.
8. UI: thumbnail WebP do core sinh sẵn, gallery virtual scroll, viewer GLB/Spine lazy-load.

## 3. Kiến trúc đề xuất

### 3.1 Core: **Python** (chốt 2026-10-18, chủ nhân giao con chọn phương án tiện nhất)
Python 3.13 + uv, asyncio, FastAPI/uvicorn, pydantic v2, SQLite WAL, Pillow + numpy, trimesh/pygltflib,
`claude-agent-sdk`, pywin32 (Job Object), `orjson`.

Vì sao tiện nhất:
- `claude-agent-sdk` chính thức: không nạp CLAUDE.md/hook cá nhân trừ khi bật `setting_sources` → giải luôn bẫy
  caveman/RTK; tool cho Claude khai báo bằng `@tool` chạy in-process, khỏi dựng MCP server riêng.
- Cùng ngôn ngữ với bpy script, tool ComfyUI có sẵn (`ui2api.py`, `build_workflows.py`), client mẫu trong doc.
- Pillow/numpy/trimesh đủ cho trim, pack, quantize, kiểm GLB (lõi C, nhả GIL).
- OpenAPI của FastAPI → `openapi-typescript` sinh type TS cho Vue, không lệch contract.

Quy tắc:
- 3 runtime tách riêng: venv core (nhẹ, **không** import torch) · venv ComfyUI · Python nhúng của Blender
  (script Blender chỉ dùng stdlib + bpy).
- Giữ nhanh: không gọi blocking trong event loop, ops ảnh qua thread pool, lazy import thư viện nặng,
  đóng gói PyInstaller/Nuitka **onedir** (onefile giải nén mỗi lần mở → chậm 3–5s).
- Rust không làm core; chỉ viết module PyO3/maturin nếu profile thấy hotspot thật.

### 3.2 Sơ đồ

```
Electron main (mỏng: window, tray, spawn/supervise core, dialog, auto-update)
  └─ renderer Vue 3 + TS + Vite + Pinia  (Vue Flow: DAG · three.js: GLB · spine-player: Spine)
        ⇅ HTTP + WS  127.0.0.1:<port ngẫu nhiên>, bearer token qua preload, kiểm Origin
studio-core (Python 3.13: asyncio + FastAPI)  — chạy độc lập, đóng cửa sổ job vẫn chạy
  ├─ Pipeline engine: recipe (DAG YAML) → run → step, gate, fan-out/map, cache
  ├─ Scheduler lanes: GPU(1, model-affinity) · BLENDER(n worker) · CLOUD(Codex/Claude, rate limit) · CPU(thread pool)
  ├─ Store: SQLite WAL + blob store content-addressed (blake3) + cây export theo project
  ├─ Adapters
  │   ├─ ComfyUI  : supervise process (Job Object), 1 WS cố định, /prompt, node_errors, /free, crash → restart
  │   ├─ Codex    : codex exec --json → thread_id → gom ~/.codex/generated_images/<thread_id>/*.png
  │   ├─ Claude   : claude-agent-sdk (stream, json schema, tool in-process, +blender MCP khi cần)
  │   ├─ Blender  : worker headless thường trú (blender -b -P worker.py, JSON-RPC localhost) + GUI qua MCP
  │   └─ LocalOps : trim/pad/resize/pack/quantize/atlas/spine-json/glb-validate (Pillow + numpy + trimesh)
  └─ Tool cho Claude (@tool in-process): comfy.run, asset.read, asset.save_version, sprite.pack, blender.exec(scoped)…
Process ngoài: ComfyUI (venv riêng) · Blender 5.2 · claude · codex
```

Renderer nói thẳng với core (không đi vòng IPC main) để stream log/preview nhanh. Core cũng chạy được dạng CLI
(`studio-core run recipe.yaml --input …`) cho batch và cho Claude gọi.

### 3.3 Adapter chi tiết

**ComfyUI** (spec = COMFYUI-INTEGRATION.md):
- Spawn `python.exe main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --disable-pinned-memory
  --models-directory F:\work\AI\comfyUI_models --preview-method auto` trong Job Object; poll `/system_stats` ≤120s, kiểm argv.
- 1 WS/core, route event theo `prompt_id`; frame binary preview → đẩy lên UI (xem ảnh đang sample live).
- Tự sinh `prompt_id`; `node_errors` khác rỗng = fail ngay.
- Process chết → step `failed(comfy_crashed)` → restart server → retry 1 lần theo policy.
- Core restart mà ComfyUI còn sống → re-attach theo `prompt_id` qua `/history`.
- Khởi động: validate mọi `class_type` + combo model qua `/object_info`; đổi `comfyui_version` → cảnh báo regen workflow.

**Codex (image gen)** — `codex exec --json --skip-git-repo-check --sandbox read-only -C <job_dir> [-i ref.png] "<prompt>"`
- Đọc JSONL lấy `thread_id` → gom ảnh `generated_images/<thread_id>/`. Chạy N song song có giới hạn (quota gói ChatGPT).
- Cần verify ở P0: `--ephemeral` có còn lưu ảnh; thời gian/ảnh; kích thước/alpha; giới hạn tần suất.

**Claude** — `claude-agent-sdk` (Python), `ClaudeAgentOptions`: `setting_sources` để trống, `allowed_tools`
chỉ gồm tool của core, `mcp_servers` chỉ thêm `blender` khi step cần, `output_format` JSON schema, `model` theo step.
- Không nạp CLAUDE.md/hook/MCP cá nhân (caveman, RTK, figma…) → output sạch, khởi động nhanh.
- P0 verify: SDK dùng được login Claude sẵn có trên máy hay cần API key; thời gian mỗi lượt gọi.
- Model theo step: haiku cho mở rộng prompt, sonnet cho viết bpy script, opus cho QA khó. Giữ session cho vòng sửa.

**Blender**
- Pipeline xác định: worker `blender.exe -b --factory-startup -P worker.py` thường trú, mỗi job `read_homefile(use_empty)`
  rồi chạy op template (import GLB, chuẩn scale/origin, decimate, render ortho, export). 2 worker cho việc CPU.
- Agent tương tác: Claude + `mcp-for-blender` trên Blender GUI, lock 1 scene/1 job.
- VRAM: render Cycles GPU = task lane GPU, gọi ComfyUI `/free` trước; preview nên dùng EEVEE/Workbench cho nhẹ.

### 3.4 State 3 tầng (thay 15 state của PLAN)
- **Step**: `pending → ready → waiting_resource → running → succeeded | failed | cancelled | cached | skipped`;
  gate có `awaiting_human`. Retry = attempt mới, giữ log cũ.
- **Run**: suy ra từ step: `running | awaiting_human | succeeded | failed | cancelled | interrupted`.
  Core khởi động lại: step `running` → `interrupted` → re-attach (ComfyUI) hoặc chạy lại an toàn.
- **Version**: `draft → approved | rejected`; export chỉ từ `approved`, export là event.

### 3.5 Step kinds
`codex.image` · `comfy.<workflow>` · `claude.task` · `blender.script` · `blender.agent` · `op.*` (Rust) ·
`gate.pick` (chọn k/N) · `gate.approve` · `map` (chạy sub-step cho từng item: góc, biến thể, part).

Cổng typed: `image`, `image[]`, `rgba`, `mesh`, `blend`, `spine`, `text`, `brief`.

### 3.6 MCP server cho Claude CLI / Codex CLI (chủ nhân yêu cầu 2026-10-18)

Mục tiêu: agent CLI bên ngoài (Claude Code, Codex CLI, …) gọi Asset Studio như 1 tool — gửi input, nhận output file.
Khác §3.1 (tool in-process cho Claude *bên trong* core): đây là chiều ngược lại, agent ngoài điều khiển core.

- **Transport**: `studio-core mcp` = MCP server **stdio** (SDK `mcp`/FastMCP), là proxy mỏng tới core đang chạy
  (đọc port + token từ file lock trong data dir; core chưa chạy → tự spawn như app làm). Không mở thêm cổng mạng;
  mọi logic vẫn ở engine → app UI và agent CLI thấy cùng run/gate/library.
- **Tools**:
  - `list_recipes` / `describe_recipe(id)` — inputs typed, default, options.
  - `start_run(recipe, inputs, title?, gate_policy?)` — input `image`/`mesh` nhận **đường dẫn file local**
    (core copy vào blob store); trả `run_id`.
  - `get_run(run_id)` — trạng thái run + từng step, lỗi, gate đang chờ (kèm thumbnail ứng viên).
  - `wait_run(run_id, timeout_s)` — chặn tới khi xong / chờ gate / fail; gửi progress notification.
  - `resolve_gate(step_id, pick=[…] | approve=bool)`, `cancel_run`, `retry_step`.
  - `get_outputs(run_id)` — export output ra `<data>/exports/mcp/<run_id>/`, trả **đường dẫn tuyệt đối** + metadata
    (sheet JSON, GLB report); ảnh nhỏ trả kèm dạng MCP image content để agent nhìn được.
  - `list_versions` / `set_version_status` — duyệt trong Library.
- **Gate**: mặc định `human` (chờ chủ nhân chọn trên app, `wait_run` trả `awaiting_human`); `gate_policy: agent` cho
  agent tự chọn sau khi xem thumbnail; `auto_first` cho batch/test.
- **Đăng ký**: Claude — `claude mcp add asset-studio -- <core>\.venv\Scripts\python.exe -m studio_core mcp`;
  Codex — `~/.codex/config.toml` `[mcp_servers.asset-studio] command = "…python.exe", args = ["-m","studio_core","mcp"]`.
  App có nút "Copy lệnh kết nối" cho cả hai.
- **An toàn**: chỉ đọc file input chủ nhân/agent chỉ định, output là bản copy; run do MCP tạo gắn `source: mcp` để lọc
  trên UI; không có tool xoá dữ liệu.
- **Test**: unit test tool qua core giả; E2E: `claude -p` và `codex exec` gọi R1 thật, nhận PNG về đúng đường dẫn.

## 4. Recipe theo loại asset

| Recipe | Chuỗi | Ghi chú |
|---|---|---|
| R1 `concept-2d` (icon/item/UI) | [codex.image ×N ‖ t2i_zimage_turbo batch N] → gate.pick → remove_bg_birefnet → op.trim_pad_resize → export @1x/@2x | MVP đầu tiên |
| R2 `variants` | ảnh gốc → edit_flux2_klein4b ×N (màu/phụ kiện) → remove_bg → op.sheet | |
| R3 `sprite-8dir-2d` (nhanh) | concept → gate → multi_angles_qwen2511 (8 prompt hướng) → remove_bg ×8 → op.normalize (scale, baseline) → op.sheet | Độ nhất quán giữa góc trung bình |
| R4 `prop-3d` | concept → gate → remove_bg → trellis2 (faces 10–50k, tex 2048) hoặc hunyuan21 → blender.script cleanup → op.glb_validate → turntable → gate.approve → GLB/FBX | |
| R5 `sprite-8dir-3d` (nhất quán) | R4 tới mesh → blender.script render ortho 8/16 hướng, nền trong suốt → op.pixelate (tuỳ) → op.sheet + JSON metadata Unity/Godot | Tốt hơn R3 cho game |
| R6 `spine-character` (R&D) | nhân vật front-view T-pose → gate → tách part (workflow SAM3.1 cần dựng) → inpaint phần bị che (Flux2 Klein) → op.layers (trim, pivot) → claude.task đặt bone (json-schema) → op.spine_export (skeleton JSON 4.2 + .atlas + png) → spine-player preview → (tuỳ) claude.task keyframe idle/breathe/blink | Chất lượng tự động chưa chứng minh |
| R7 `procedural-3d` | claude.task viết bpy theo template → blender.script → render → claude vision QA (≤3 vòng) → export | Đúng tinh thần PLAN |
| R8 `sprite-anim` (sau) | cần pose ControlNet (thiếu preprocessor) hoặc rig 3D (Mixamo = upload ngoài → approval) | Chưa vào MVP |

### 4.1 Sprite 8 hướng — trọng tâm (chủ nhân chốt 2026-10-18)

**Đường chính R5 (3D → render)**: cùng mesh, cùng đèn, cùng scale → 8 hướng khớp nhau tuyệt đối.
- Camera ortho, góc nghiêng là tham số: 30° (isometric kiểu ARPG/RTS) · 45° · 60–90° (top-down);
  hướng 0…315° bước 45°. **Xoay model, giữ camera + đèn cố định** → ánh sáng đồng nhất theo màn hình.
- Ortho scale tính 1 lần từ bounding box lớn nhất qua mọi hướng → kích thước sprite đều; pivot = tâm chân,
  cùng toạ độ pixel ở mọi frame.
- Film trong suốt. Ảnh thường: render 2× rồi downscale. Pixel art: render nhỏ + nearest + quantize palette + outline (tuỳ chọn).
- EEVEE mặc định (nhanh, ít VRAM); Cycles khi cần chất lượng (lane GPU, `/free` ComfyUI trước).
- Mirror tuỳ chọn: render 5 hướng (S, SW, W, NW, N), lật ngang ra 3 hướng còn lại → nhanh gần gấp đôi;
  tắt với asset bất đối xứng (vũ khí cầm 1 tay).
- Xuất: thứ tự hướng + tên file theo preset engine (Unity/Godot/tuỳ chỉnh); sheet hàng = hướng, cột = frame;
  JSON metadata (frame size, pivot, thứ tự hướng).

**Đường nhanh R3 (Qwen multi-angle)**: 8 prompt mặc định của workflow là kiểu camera (cận cảnh, từ trên cao, góc rộng…),
**không phải** 8 hướng → phải thay bằng front / rotate 45° / 90° / 135° / back…; góc nghiêng không khoá được,
độ khớp giữa các hướng trung bình → dùng cho concept/xem nhanh.

**Animation 8 hướng** (đi, chạy, đánh): cần rig. Mesh TRELLIS là 1 khối dày → decimate → rig (Rigify có sẵn nhưng
phải fit metarig; Mixamo = upload ngoài → approval; auto-rig AI mã nguồn mở chưa có trên máy) → action → render
mỗi hướng × mỗi frame. Thuộc R8, cần spike riêng.

## 5. Lộ trình

Đích MVP: **sprite 8 hướng** (R5) chạy end-to-end.

- **P0 — spike verify**: Claude Agent SDK (login sẵn có hay API key, không nạp hook/CLAUDE.md) · Codex image headless
  (đường ảnh, thời gian, song song) · Blender worker headless + render 8 hướng thử từ 1 GLB TRELLIS · Qwen 8 prompt hướng ·
  core Python gọi 1 workflow ComfyUI end-to-end · Job Object kill-tree.
  ✅ Xong 2026-10-18 — kết quả + các chỉnh adapter: `.planning/P0-REPORT.md`.
- **P1 — nền**: core (FastAPI, SQLite, blob store, lanes, ComfyUI adapter đủ) + Electron/Vue shell (dashboard health,
  run list, gallery). Đạt: R1 bằng Z-Image chạy end-to-end, restart không mất lịch sử.
- **P2 — 2D**: Codex adapter, gate inbox, R1 Codex, R2, op sprite (trim/pack/sheet), R3.
- **P3 — 3D + sprite 8 hướng**: Blender worker, R4, **R5**.
- **P4 — Claude**: brief/prompt expansion, tool in-process cho Claude, R7 + Blender MCP agent step.
- **P4b — MCP ngoài** (§3.6): `studio-core mcp` stdio cho Claude CLI / Codex CLI — start run bằng input file,
  chờ, xử lý gate, nhận output path. Đạt: `claude -p` và `codex exec` chạy R1 + R5 qua MCP, nhận file về.
- **P5 — Spine**: workflow SAM3.1, R6, Spine export + preview.
- **P6 — flow editor**: Vue Flow chỉnh recipe, batch từ danh sách (stage-major), preset theo project/engine.
- **P7 — đóng gói**: electron-builder NSIS, core PyInstaller onedir trong `extraResources`, cấu hình đường dẫn tool/model
  (không bundle model).

## 6. UI/UX & motion (yêu cầu chủ nhân, 2026-10-18)

Bắt buộc khi code UI: gọi skill `frontend-design` + `ui-ux-pro-max`, bám **Apple HIG**, dùng **Codex image gen**
(qua chính adapter của app) để sinh asset UI. Project có style riêng (HIG) → **không** dùng `web-frontend-defaults`.

**Motion mượt, không cứng**
- Chỉ animate `transform`/`opacity` (GPU), spring kiểu HIG, 150–300ms; tôn trọng `prefers-reduced-motion`.
- Số thay đổi (progress %, thời gian chạy, VRAM, số job trong queue, số asset) → tween bằng `useTransition`
  (VueUse) + `font-variant-numeric: tabular-nums` để số không nhảy layout.
- Thư viện: `motion-v` (Motion cho Vue) cho spring/layout/shared-element; `@formkit/auto-animate` cho list.
- Chuyển trạng thái step có hiệu ứng riêng: pending (mờ) → waiting_resource (pulse nhẹ + lý do "chờ GPU")
  → running (vòng progress + shimmer + tên node ComfyUI đang chạy + đồng hồ chạy) → succeeded (check morph)
  / failed (rung nhẹ + lỗi rõ) / cached (badge "dùng lại").

**State từng bước luôn rõ**
- Run detail = DAG/timeline: mỗi step 1 chip trạng thái + thời gian + input/output thumbnail.
- Ảnh preview live từ WS ComfyUI khi đang sample; GLB/sprite/Spine xem ngay khi step xong.
- ETA chỉ hiện khi có số đo lịch sử, ghi rõ "ước tính" (PLAN cấm giả tiến độ).
- Gate inbox: các step đang `awaiting_human` gom 1 chỗ, chọn/duyệt bằng phím tắt.

**Tốc độ UI**
- Window `show` ở `ready-to-show`, core spawn song song với renderer; preload tối thiểu.
- Event WS gộp theo `requestAnimationFrame` (không re-render mỗi event); `shallowRef` cho list lớn.
- Gallery virtual scroll (`@tanstack/vue-virtual`), thumbnail WebP do core sinh sẵn, `content-visibility: auto`.
- three.js / spine-player lazy-load theo route; việc parse nặng đưa vào Web Worker.

**Typography / icon** — SF Pro & SF Symbols bị license giới hạn cho nền tảng Apple → trên Windows dùng Inter
(gần SF nhất) + Lucide/Phosphor. Codex gen: app icon, minh hoạ empty state/onboarding, thumbnail loại recipe.

## 7. Rủi ro

- Codex image headless chưa có doc chính thức về nơi lưu ảnh / quota → verify P0, có fallback Z-Image local.
- Claude headless dính hook/CLAUDE.md cá nhân → phải cô lập.
- Tranh VRAM Blender ↔ ComfyUI → scheduler + `/free`.
- ComfyUI crash cứng (pinned memory) → đã có hướng xử lý; workflow JSON gắn version ComfyUI → kiểm `/object_info`.
- Spine/animation tự động là R&D, tách khỏi MVP.
- License: kiểm Hunyuan3D 2.1 (Tencent community license có giới hạn lãnh thổ) trước khi dùng asset thương mại;
  Spine runtime (spine-player) yêu cầu license Spine Editor.
