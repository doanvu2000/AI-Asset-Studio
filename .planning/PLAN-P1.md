# P1 — Nền (core + shell) — 2026-10-18

Đạt: R1 `concept-2d` bằng Z-Image chạy end-to-end từ UI; restart core không mất lịch sử.
Kiến trúc tổng: `PLAN-architecture-v2.md`. Số đo + bẫy adapter: `P0-REPORT.md`. Bản mẫu: `spikes/comfy_lib.py`,
`spikes/blender_worker.py`, `spikes/spike_b_codex.py`, `spikes/spike_a_claude.py`.

## Layout

```
core/                         uv project (Python 3.13), venv core/.venv — KHÔNG import torch
  studio_core/
    contracts.py              ★ contract chung (enum, row, Executor, Repo, Engine, EventBus, BlobStore, Resource)
    config.py                 Settings (đường dẫn tool/model, lane limits, origins)
    __main__.py / main.py     [A] CLI: `python -m studio_core serve --port 0` ; env STUDIO_TOKEN
    app.py                    [A] build_app(settings) -> FastAPI; wiring (registry, resources, engine, repo, bus)
    api/                      [A] routes + ws + auth
    store/                    [A] db.py (aiosqlite, WAL, schema), repo.py (Repo), blobs.py (BlobStore), events.py (EventBus)
    engine/                   [E] recipe.py, runner.py (Engine), scheduler.py (lanes), cache.py, gates.py
    recipes/*.yaml            [E] concept-2d (R1 Z-Image) + concept-2d-codex
    adapters/                 [B] comfy/{server,client,executor}.py, codex.py, claude.py, blender/{worker.py,client.py,executor.py}
    ops/                      [C] image.py (trim_pad_resize, thumbnail, sheet, mirror), executors.py (op.*)
  tests/                      pytest (fake executor, không cần GPU); test thật đánh dấu @pytest.mark.live
app/                          [D] Electron + Vue 3 + TS + Vite (electron-vite) + Pinia
data/                         runtime: studio.db, blobs/aa/bb/<hash>, thumbs/, jobs/<run>/<step>-<attempt>/
```

Registry wiring (`app.py`): mỗi package expose 1 hàm `register(registry, settings, bus, blobs) -> list[Resource]`:
`adapters.register`, `ops.register`, `engine.gates` tự đăng ký gate kinds.

## Step kinds P1

| kind | lane | owner | inputs → outputs | params |
|---|---|---|---|---|
| `comfy.<workflow>` | gpu | B | `image` (list, gắn lần lượt vào `index.images` keys) → `images`, `meshes` | key thô `"57:27.text"`… ; cacheable khi mọi seed key có trong params |
| `codex.image` | cloud | B | `refs` (tuỳ chọn, `-i`) → `images` | `prompt`, `transparent` bool, `count` (n process song song ≤ lane) |
| `claude.task` | cloud | B | `images` (tuỳ chọn) → `json` (1 blob application/json) | `prompt`, `model`, `schema` |
| `blender.render_dirs` | blender | B | `mesh` → `images` (theo góc tăng dần) | `elevation`, `count`, `res`, `mirror`, `engine` |
| `op.trim_pad_resize` | cpu | C | `images` → `images` | `size`, `pad` (tỉ lệ), `square`, `resample` |
| `op.sheet` | cpu | C | `images` → `sheet` (png) + `meta` (json) | `cols`, `cell` |
| `op.mirror_dirs` | cpu | C | `images` (5 hướng 0..180) → `images` (8 hướng) | `count` |
| `gate.pick` | none | E | `images` → `images` (đã chọn) | `k` |
| `gate.approve` | none | E | `*` → pass-through | — |

## Recipe YAML

```yaml
id: concept-2d
name: Concept 2D (Z-Image)
description: Text → N ảnh → chọn → tách nền → căn chỉnh
inputs:
  prompt: {type: text, label: Prompt}
  count: {type: int, default: 4, min: 1, max: 8, label: Số biến thể}
  size: {type: int, default: 512, options: [128, 256, 512, 1024]}
steps:
  - id: gen
    kind: comfy.t2i_zimage_turbo
    params: {"57:27.text": "${inputs.prompt}, isolated on plain white background, centered, game asset",
             "57:13.batch_size": "${inputs.count}"}
  - id: pick
    kind: gate.pick
    in: {images: gen.images}
    params: {k: 1}
  - id: rmbg
    kind: comfy.remove_bg_birefnet
    in: {image: pick.images}
    map: image            # chạy executor 1 lần / item, gộp outputs theo thứ tự
  - id: fit
    kind: op.trim_pad_resize
    in: {images: rmbg.images}
    params: {size: "${inputs.size}", pad: 0.06}
outputs:
  final: fit.images       # thành version draft khi run succeeded
```

- `${inputs.x}` thay nguyên kiểu nếu chuỗi chỉ gồm 1 placeholder; trong chuỗi dài → str.
- `in: {name: "<step>.<output>"}`; deps suy ra từ `in`. DAG kiểm cycle lúc load.
- Ghép nhiều nguồn: `in: {image: [inputs.image, angles.images]}` → blob nối theo thứ tự list.
- Comfy option `_drop_outputs: ["373"]` bỏ output node (ComfyUI bỏ luôn cả nhánh).
- `map: <input>` = chạy executor 1 lần / item của input đó. `map_param: <param key>` (P2) = chạy 1 lần / phần tử của
  param dạng list (cùng inputs), gộp outputs theo thứ tự; không dùng chung với `map`. Param list lấy từ list YAML hoặc
  filter `${inputs.x | lines}` (input `text`, mỗi dòng không rỗng = 1 phần tử; phần chữ quanh placeholder ghép vào
  từng dòng). Filter list chỉ được dùng 1 lần, đúng trong param `map_param`. Danh sách rỗng → `POST /runs` lỗi 400.

```yaml
  - id: edit                                  # variants-2d (R2)
    kind: comfy.edit_flux2_klein4b
    in: {image: inputs.image}
    params: {"75:74.text": "${inputs.instructions | lines}. Keep the same object, shape, pose…"}
    map_param: "75:74.text"                   # "make the potion blue\nadd golden trim" → 2 lần chạy
```
- Hash recipe = blake3(file bytes).

## Engine semantics

- Step: `pending → ready → waiting_resource → running → succeeded|failed|cancelled|cached`; gate: `ready → awaiting_human → succeeded`.
- Run status suy ra: có step failed → failed (các step chưa chạy → skipped); có awaiting_human và không còn gì chạy → awaiting_human;
  tất cả ok → succeeded; cancel → cancelled.
- Cache key = blake3(kind | executor.version | canonical json params | sorted input hashes theo tên | cache_material).
  Hit → status `cached`, outputs từ cache.
- Lane scheduler: semaphore theo `settings.lane_limits`; FIFO theo thời điểm ready; step chờ lane → `waiting_resource`
  (meta.waiting_for = lane). Model-affinity (P2, chỉ lane GPU): khi slot rảnh, ưu tiên waiter có `model_group` trùng
  step GPU vừa chạy (`executor.model_group` nếu có, không thì `kind` → `comfy.<workflow>`), FIFO trong nhóm; waiter chờ
  ≥120s (`AFFINITY_MAX_WAIT_S`) thắng bất kể nhóm → batch tự chạy stage-major mà không đói.
- Retry: attempt+1, job_dir mới, downstream reset pending, run → running.
- Cancel: set `ctx.cancelled`, task.cancel(); executor phải dọn process con (comfy: `/api/jobs/{id}/cancel` hoặc `/interrupt`).
- Recover lúc start: step `running|waiting_resource|ready` → failed(code `interrupted`); run → interrupted. Gate đang chờ giữ nguyên
  (resume được sau restart).
- Mọi thay đổi step → `step.updated` (full StepRow). Progress → `step.progress` (executor gọi ctx.progress; engine throttle ≤10/s/step).

## HTTP API (prefix `/api`, bearer token bắt buộc trừ `/api/health`)

| Method | Path | Body / query | Trả |
|---|---|---|---|
| GET | `/api/health` | — | `{ok, version}` |
| GET | `/api/resources` | — | `[ResourceHealth]` |
| POST | `/api/resources/{name}/start` \| `/stop` | — | ResourceHealth |
| GET | `/api/recipes` | — | `[RecipeInfo]` |
| GET | `/api/kinds` | — | `[str]` |
| POST | `/api/runs` | `{recipe_id, inputs, params?, title?}` | `{run_id}` |
| GET | `/api/runs` | `?limit&before&status` | `[RunRow]` |
| GET | `/api/runs/{id}` | — | `{run: RunRow, steps: [StepRow]}` |
| POST | `/api/runs/{id}/cancel` | — | `{ok}` |
| POST | `/api/steps/{id}/retry` | — | `{ok}` |
| POST | `/api/gates/{step_id}` | `GateResolution` | `{ok}` |
| GET | `/api/gates` | — | `[StepRow]` đang awaiting_human |
| POST | `/api/blobs` | multipart `file` | BlobRef |
| GET | `/api/blobs/{hash}` | — | file (Cache-Control immutable) |
| GET | `/api/blobs/{hash}/thumb` | `?size=256` | image/webp |
| GET | `/api/versions` | `?run_id&status` | `[VersionRow]` |
| POST | `/api/versions/{id}/approve` \| `/reject` | — | VersionRow |

- Blob endpoints nhận token qua header **hoặc** `?t=<token>` (để `<img src>` dùng được).
- Lỗi: `{"error": {"code", "message"}}` + HTTP 4xx/5xx.
- Origin: request có header `Origin` không thuộc `allowed_origins` → 403. Không có Origin (curl/test) → cho qua nếu đúng token.
- JSON response dùng `ORJSONResponse`.

## WebSocket `/ws?t=<token>`

- Server gửi `{"type":"hello","seq":<last_seq>}` rồi stream `Event` (JSON).
- Client có thể gửi `{"op":"resume","after_seq":N}` → server phát lại từ ring buffer (1000 event gần nhất) rồi tiếp live.
- Server ping 20s. UI gộp event theo rAF.

## Core process

- `python -m studio_core serve [--host 127.0.0.1] [--port 0] [--data-dir …]`; token từ env `STUDIO_TOKEN`
  (không có → sinh và in ra stderr, chế độ dev).
- Khi uvicorn đã bind: in đúng 1 dòng stdout `STUDIO_CORE_READY {"port": N, "pid": P}`.
- Thoát: SIGINT/CTRL_BREAK/`POST /api/shutdown` → engine.shutdown → stop resources managed (ComfyUI do core spawn, Blender worker).

## Agent phân công (chạy song song, chỉ sửa trong vùng của mình)

- **A — store + API + main**: `store/`, `api/`, `app.py`, `main.py`, `__main__.py`, tests của mình.
- **E — engine**: `engine/`, `recipes/`, tests engine bằng fake executor + fake repo/bus in-memory (hoặc dùng store của A khi có).
- **B — adapters**: `adapters/` (ComfyUI server Job Object + client WS + executor; Codex; Claude; Blender worker + client + executor).
- **C — ops**: `ops/`.
- **D — app**: `app/` (Electron + Vue). Gọi skill `frontend-design` + `ui-ux-pro-max:ui-ux-pro-max` trước khi viết UI.

Quy tắc chung: Python 3.13, type hints, async không chặn loop (I/O nặng → `asyncio.to_thread`), ít comment (chỉ "tại sao"),
không tự build; `uv run pytest` được phép.
