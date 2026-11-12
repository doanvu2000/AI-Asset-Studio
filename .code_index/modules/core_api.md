# core_api — FastAPI app, route, auth, WS

Gốc `core/studio_core/`. `build_app` dựng app, `mount_api` gắn các router.

## Dựng app + vòng đời
- `app.py::build_app` — tạo `FastAPI`, `state.{settings,token,db,repo,blobs,bus,registry,resources,engine,translator}`; `_create_engine` + `_register_plugins` (đăng ký executor từ `adapters`, `ops`, `engine.gates`); `_shutdown` đóng engine/resource/db.
- `app.py::UnavailableEngine` — engine giả (503 `engine_unavailable`) khi `create_engine` lỗi/thiếu, API vẫn boot.
- `main.py::main` / `_serve` — CLI `serve` (`--host`, `--port` 0 = tự chọn) + subcommand `agent`; `_bind`, `_announce_when_started` ghi lock; `_watch_parent` tự tắt khi process cha (Electron) chết; `AlreadyRunning`.
- `config.py::Settings` / `load_settings` / `save_overrides` — cấu hình + đường dẫn công cụ (`data/settings.json`), `find_godot`, `find_unity`.
- `paths.py` — `default_data_dir`, `recipes_dir`, `flows_dir`, `blender_scripts_dir`, `core_command` (frozen vs dev).
- `contracts.py` — pydantic + Protocol dùng chung: `RunRow`, `StepRow`, `VersionRow`, `BlobRef`, `ProjectRow`, `WorkflowDef`, `Event`, `Repo`, `Engine`, `Executor`, `ExecutorRegistry`, `Resource`.

## Mount
- `api/__init__.py::mount_api` — `install_error_handlers`, include tất cả router, thêm `CORSMiddleware` + `OriginCheckMiddleware`.
- `api/auth.py::require_token` (header `Authorization: Bearer`) · `require_token_or_query` (cho `<img>`/blob) · `OriginCheckMiddleware` · `token_matches` (so sánh hằng thời gian).
- `api/errors.py::ApiError(status, code, message)` · `OrjsonResponse` · `error_response` — mọi lỗi dạng `{code, message, detail}`.

## Router (file → prefix / việc)
- `api/routes.py` — `public` (`/health`), `api`, `blob_api`: resources start/stop, `/recipes` (CRUD + validate + preview), `/kinds`, `/runs` (+rerun, cancel), `/steps/{id}/retry`, `/gates`, `/versions` (approve/reject), `/blobs` (upload/get/info/thumb/export), `/steps/{id}/export` (engine export), `/batches`, `/presets`, `/translate`, `/shutdown`.
- `api/flows.py` — `/api/flows`, `/flows/{id}/runs`, `/flow-runs`; flow-batch.
- `api/projects.py` (`/api/projects`), `manifest.py` (asset list), `gdd.py` (kế hoạch từ GDD), `style_bible.py`, `project_style.py`, `project_qa.py`, `similar.py`.
- `api/pack.py` (`build_pack`, `list_packs`, `ai_credits`), `atlas.py`, `hitfx.py`, `restyle.py`, `region_edit.py`, `audit.py` (quét/sửa folder game), `engine_preview.py` (chạy thử Godot/Unity).
- `api/gpu.py` (`/api/gpu`: yield/resume/priority), `agent.py` (`/api/agent/activity`, `/status` — nhật ký MCP), `setup.py` (cài model, đăng nhập Claude), `tools.py` (đường dẫn công cụ), `hardware.py`, `diagnostics.py` (log + `redact`), `stats.py`, `qa.py` (scan job), `run_presets.py`, `library.py` (`/library/import`, shaders).
- `api/ws.py::events_ws` — WS `/ws?t=<token>` (sai token → close 4401): gửi `hello{seq}`, stream event từ `seq`; lệnh client `subscribe_runs{run_ids}` (lọc), `resume{after_seq}`; `_ping` giữ kết nối.

## Invariant
- Mọi router gắn `Depends(require_token)`, trừ `public` (`/health`); blob GET dùng `require_token_or_query`; `ws` tự kiểm token query `t`.
- `routes.py::_engine_call`: `LookupError` → 404 `not_found`, `ValueError` → 400 `invalid`.
- Export step chạy `asyncio.to_thread` (CPU nặng), lỗi `BundleError` → 422 `not_exportable`.
- Test: `core/tests/test_api_basic.py`, `test_p6_recipes_api.py`, `test_w8_projects.py`, `test_step_export.py`, `test_p8_diagnostics.py`.
