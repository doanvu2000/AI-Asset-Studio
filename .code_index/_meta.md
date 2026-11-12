---
last_updated: 2026-11-13
---
# AI Asset Studio — _meta

## Mục đích
App Windows tạo asset game bằng AI chạy local: mô tả → chọn ảnh (gate) → sprite / texture / tileset / 3D /
Spine → export cho engine (Flutter/Flame mặc định, Android Compose, iOS SwiftUI, Unity, Godot, Spine, GLB).
Gồm 3 phần: **core** Python (FastAPI, chạy recipe), **app** Electron + Vue (UI), **MCP** cho Claude Code / Codex.
Đọc `lookup.md` trước, rồi đúng 1 slice trong `modules/`.

## Layout
| Thư mục | Nội dung |
|---|---|
| `core/studio_core/` | package Python: engine, ops, adapters, export, store, api, mcp, bootstrap |
| `core/studio_core/recipes/*.yaml` | ~110 recipe (step graph); `flows/*.yaml` = workflow mẫu (graph các recipe) |
| `core/studio_core/workflows/*.api.json` | ComfyUI workflow + `index.json` (map param → node) |
| `core/tests/` | ~160 file pytest (`conftest.py` + `test_*.py`) |
| `core/scripts/` | script e2e tay (`e2e_*.py`, `verify_shaders.py`), không phải test tự động |
| `core/packaging/` | PyInstaller spec (`studio-core.exe`) |
| `app/src/main/` | Electron main: spawn core, devlog, diagnostics, agent install, export zip |
| `app/src/preload/` | contextBridge API cho renderer |
| `app/src/renderer/src/` | Vue 3 + Pinia + vue-router: `views/` `components/` `stores/` `composables/` `api/` |
| `integrations/` | plugin Claude (`claude-plugin/`), Codex, Unity bridge, Godot addon |
| `scripts/` | `check.mjs` (kiểm Python core), `gen_agent_docs.py` |
| `docs/`, `.planning/`, `.bug_feedback/` | tài liệu, plan/handoff, bug đã ghi |
| `*.md` ở gốc | `README.md`, `BUILD.md`, `AI-ASSET-STUDIO-IMPLEMENTATION-PLAN.md`, `COMFYUI-*.md` |

## Kiến trúc 1 dòng
`app (Vue)` ⇄ HTTP+WS (Bearer token, 127.0.0.1) ⇄ `studio_core.api` → `StudioEngine` (runner) → `ExecutorRegistry`
(kind `comfy.*` / `blender.*` / `claude.*` / `op.*` / `gate.*`) → blob store (content-hash) + SQLite. MCP server là
proxy HTTP tới cùng core (qua `core.lock.json`), không chứa logic pipeline.

## Chạy
- Core venv: `core/.venv/Scripts/python.exe` (uv, Python 3.13). Serve: `python -m studio_core serve` (port 0 = tự chọn,
  ghi `core.lock.json` trong data dir).
- App dev: `cd app && npm run dev` (`dev:mock` = UI với mock API, không cần core). Typecheck: `npm run typecheck`.
- Build bản cài: `cd app && npm run dist` (xem `BUILD.md`; `dist:core` rồi `dist:app`).
- Cần ngoài repo: ComfyUI + model, Blender, Codex CLI, Claude Code CLI (cấu hình trong `data/settings.json`).

## Scripts
| Tên | Lệnh | Khi nào |
|---|---|---|
| check | `node scripts/check.mjs` | sau khi sửa Python core, trước khi báo xong: kiểm dòng > 120 ký tự trong file `core/*.py` đổi, rồi pytest (`-m "not slow and not live"`, `-n` theo luồng) |

Cờ `check`: `--tests tests/test_a.py,tests/test_b.py` (chỉ chạy file chỉ định) · `--slow` (gồm test slow) ·
`--only pytest` (bỏ bước kiểm dòng) · `--skip lines` · `--max-len 120` · `--verbose`. Metadata: `scripts/jin-scripts.json`.
Marker pytest: `slow` (> 5s), `live` (cần GPU/ComfyUI/Blender/Codex thật — không chạy trong check).

## Routing: task → slice đọc
| Task | Slice |
|---|---|
| Thêm / sửa endpoint HTTP, WS, auth, lỗi API | `modules/core_api.md` |
| Run / step / gate / scheduler / cache / seed / chain / flow (workflow) / QA | `modules/core_engine.md` |
| Thêm kind `op.*`, sửa xử lý ảnh / spine / tile / audio, recipe YAML, ComfyUI workflow json | `modules/core_ops_recipes.md` |
| Executor gọi ComfyUI / Blender / Claude / Codex, resource, cài model, hardware fit | `modules/core_adapters.md` |
| Export engine (flutter/android/ios/unity/godot), atlas, shader, theme, pack, audit folder game | `modules/core_export.md` |
| DB, blob, event bus, repo, pack bundle, AI credits | `modules/core_store.md` |
| MCP tool, CLI `agent`, lockfile, plugin Claude/Codex, Unity/Godot bridge | `modules/core_mcp.md` |
| Electron main, preload, spawn core, diagnostics, cài agent | `modules/app_main.md` |
| Màn / store / client API / viewer / flow editor trong UI | `modules/app_ui.md` |
| Viết / chạy test, e2e, check script | `modules/core_tests.md` |

## Invariant xuyên suốt
- Mọi route `/api/*` yêu cầu Bearer token (`require_token`); route ảnh/blob chấp nhận token qua query; origin phải nằm trong allowlist.
- Step output là blob content-addressed (hash); cache step theo `cache_key(kind, version, params, inputs)`.
- `gate.*` có `Lane.NONE`: không chiếm GPU/Blender, dừng run ở `awaiting_human` tới khi `resolve_gate`.
- `yield_gpu`: không step mới nào lên lane GPU (`LaneScheduler.hold`), step đang chạy chạy nốt, ComfyUI nhả model; trạng thái chỉ trong RAM — restart core là hết yield.
- Một core / data dir: `SpawnLock` + `core.lock.json` chặn chạy đôi (`AlreadyRunning`).
